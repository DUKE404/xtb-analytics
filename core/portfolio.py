"""Rekonstrukcja dziennej wartości portfela w PLN z pozycji, cen, kursów i operacji gotówkowych.

Konwencje (opisane też w ASSUMPTIONS.md):
- kalendarz: dni robocze pon-pt; operacje z weekendu trafiają na najbliższy dzień roboczy,
- stan posiadania liczony na koniec dnia: pozycja otwarta w dniu D jest w portfelu od D,
  zamknięta w dniu D już nie (jej wynik jest w gotówce),
- akcje/ETF: wartość = ilość x cena zamknięcia x kurs waluty; CFD: w wartości portfela jest
  tylko niezrealizowany wynik (depozyt zostaje w gotówce), a ekspozycja to wartość nominalna,
- gotówka = skumulowana suma operacji gotówkowych z raportu XTB (w PLN),
- przepływy zewnętrzne = wpłaty, wypłaty i transfery; wszystko inne to wynik inwestycyjny.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

EXTERNAL_FLOW_KINDS = ("DEPOSIT", "WITHDRAWAL", "TRANSFER")   # "INTERNAL" (przelew między własnymi rachunkami) nie jest przepływem
STOCK_LEVEL_TOLERANCE = float(np.log(1.25))    # akcje: notowania do 25% od ceny transakcji uznajemy za tę samą skalę
CFD_LEVEL_TOLERANCE = 0.02                      # CFD: 2% - przy dźwigni różnica poziomów to duży fałszywy wynik
CASH_BUCKET = "_GOTÓWKA"      # wynik bez przypisanego instrumentu (odsetki, opłaty ogólne)
OTHER_BUCKET = "_INNE"        # operacje z symbolem, którego nie ma w tabelach pozycji


@dataclass
class Portfolio:
    dates: pd.DatetimeIndex
    qty: pd.DataFrame            # ilość na koniec dnia (dla CFD: loty ze znakiem)
    cost_local: pd.DataFrame     # suma (ilość ze znakiem x cena otwarcia) otwartych pozycji, waluta instrumentu
    prices: pd.DataFrame         # ceny zamknięcia w walucie instrumentu
    fx: pd.DataFrame             # PLN za jednostkę waluty instrumentu (kolumny = symbole)
    value: pd.DataFrame          # PLN: wartość rynkowa (akcje/ETF) albo niezrealizowany wynik (CFD)
    exposure: pd.DataFrame       # PLN: wartość rynkowa (akcje/ETF) albo nominał ze znakiem (CFD)
    cash: pd.Series
    equity: pd.Series
    flows: pd.Series             # przepływy zewnętrzne (wpłaty +, wypłaty -)
    returns: pd.Series           # dzienny zwrot ważony czasem (TWR)
    pnl: pd.DataFrame            # dzienny wynik w PLN per instrument (+ koszyki _GOTÓWKA / _INNE)
    meta: pd.DataFrame           # metadane instrumentów (indeks = symbol)
    cash_by_symbol: pd.DataFrame  # dzienne operacje gotówkowe per instrument (bez przepływów zewnętrznych)
    warnings: list[str] = field(default_factory=list)
    cash_by_currency: pd.DataFrame | None = None   # saldo gotówki per waluta rachunku, wyrażone w walucie bazowej
    base: str = "PLN"                              # waluta bazowa (wszystkie kwoty w obiekcie są w niej wyrażone)
    cash_fx_pnl: pd.DataFrame | None = None        # dzienny wynik z przeliczenia gotówki per waluta (już w wyniku)

    @property
    def symbols(self) -> list[str]:
        return list(self.qty.columns)

    @property
    def as_of(self) -> pd.Timestamp:
        return self.dates[-1]

    @property
    def is_cfd(self) -> pd.Series:
        return self.meta["asset_class"].reindex(self.qty.columns).eq("CFD")

    def current_exposure(self) -> pd.Series:
        """Bieżąca ekspozycja w PLN per instrument (tylko niezerowe)."""
        e = self.exposure.iloc[-1]
        return e[e.abs() > 1e-9]

    def current_value(self) -> pd.Series:
        v = self.value.iloc[-1]
        return v[(self.qty.iloc[-1].abs() > 1e-12)]

    def total_pnl(self) -> float:
        return float(self.equity.iloc[-1] - self.flows.sum())

    def window(self, start=None, end=None) -> "Portfolio":
        """Portfel oglądany w wybranym okresie: stan z dnia przed `start` staje się kapitałem początkowym,
        a dzień `end` - datą, na którą liczone są skład i ryzyko.

        Stopy zwrotu i wyniki dzienne zostają bez zmian - znika tylko historia spoza okna (np. okres, gdy
        rachunek był jeszcze mały albo prowadzony inaczej).
        """
        keep = np.ones(len(self.dates), dtype=bool)
        if start is not None:
            keep &= self.dates >= pd.Timestamp(start).normalize()
        if end is not None:
            keep &= self.dates <= pd.Timestamp(end).normalize()
        if keep.all() or keep.sum() < 2:
            return self
        first = int(np.argmax(keep))
        flows = self.flows[keep].copy()
        if first > 0:
            flows.iloc[0] += float(self.equity.iloc[first - 1])

        # instrumenty, których w oknie nie było (ani pozycji, ani wyniku), znikają z widoku
        active = [c for c in self.qty.columns
                  if (self.qty.loc[keep, c] != 0).any() or (self.pnl.loc[keep, c].abs() > 1e-9).any()]
        buckets = [c for c in self.pnl.columns if c not in self.qty.columns]

        def cut(df, cols=active):
            return None if df is None else df.loc[keep, cols]

        return replace(self, dates=self.dates[keep], qty=cut(self.qty), cost_local=cut(self.cost_local),
                       prices=cut(self.prices), fx=cut(self.fx), value=cut(self.value), exposure=cut(self.exposure),
                       cash=self.cash[keep], equity=self.equity[keep], flows=flows, returns=self.returns[keep],
                       pnl=cut(self.pnl, active + buckets), cash_by_symbol=cut(self.cash_by_symbol),
                       cash_by_currency=cut(self.cash_by_currency, slice(None)),
                       cash_fx_pnl=cut(self.cash_fx_pnl, slice(None)), meta=self.meta.loc[active],
                       warnings=list(self.warnings))

    def invested_capital(self) -> float:
        return float(self.flows.sum())


def _align(frame: pd.DataFrame, dates: pd.DatetimeIndex, columns) -> pd.DataFrame:
    """Dopasowuje dane do kalendarza: ostatnia znana wartość w przód, potem pierwsza znana wstecz."""
    f = frame.reindex(columns=columns)
    f = f[~f.index.duplicated(keep="last")].sort_index()
    f = f.reindex(f.index.union(dates)).ffill().reindex(dates)
    return f.bfill()


def reconstruct(closed: pd.DataFrame, open_: pd.DataFrame, cash: pd.DataFrame, meta: pd.DataFrame,
                prices: pd.DataFrame, fx: pd.DataFrame, as_of=None, account_currency: str = "PLN",
                base: str = "PLN", report_date=None) -> Portfolio:
    """Odtwarza portfel dzień po dniu.

    closed / open_ / cash - ramki z parsera (data/xtb_parser.py),
    meta   - mapowanie instrumentów z indeksem = symbol (asset_class, currency, multiplier, ...),
    prices - ceny zamknięcia w walucie instrumentu (kolumny = symbole),
    fx     - kursy: ile waluty bazowej za jednostkę waluty (kolumny = kody walut; baza = 1),
    account_currency - waluta kwot w `cash`, gdy ramka nie ma własnej kolumny `currency` (kilka rachunków),
    report_date - dzień, z którego pochodzą otwarte pozycje i ich „cena bieżąca” (data raportu XTB). Gdy `as_of`
             jest późniejsze, portfel jest przedłużany do `as_of`: skład i gotówka z raportu, ceny i kursy bieżące.
    base   - waluta bazowa wyniku. Gotówka jest prowadzona osobno w każdej walucie rachunku i codziennie
             przeliczana po bieżącym kursie; zmiana jej wartości z tytułu kursu trafia do wyniku (koszyk gotówki).
    """
    warnings: list[str] = []
    pos_cols = ["symbol", "type", "volume", "open_time", "open_price", "close_time", "end_price", "gross", "ccy"]
    c = closed.copy()
    c["end_price"] = pd.to_numeric(c["close_price"], errors="coerce") if "close_price" in c.columns else np.nan
    o = open_.copy()
    o["close_time"] = pd.NaT
    o["end_price"] = pd.to_numeric(o["market_price"], errors="coerce") if "market_price" in o.columns else np.nan
    for d in (c, o):
        d["gross"] = pd.to_numeric(d["gross_pl"], errors="coerce") if "gross_pl" in d.columns else np.nan
        d["ccy"] = d["currency"].astype(str) if "currency" in d.columns else account_currency
    pos = pd.concat([c[pos_cols], o[pos_cols]], ignore_index=True) if len(c) or len(o) else \
        pd.DataFrame(columns=pos_cols)

    starts = [t for t in (cash["time"].min() if len(cash) else None,
                          pos["open_time"].min() if len(pos) else None) if t is not None and not pd.isna(t)]
    if not starts:
        raise ValueError("Brak danych do odtworzenia portfela (ani operacji, ani pozycji).")
    start = min(starts).normalize()
    ends = [t for t in (cash["time"].max() if len(cash) else None,
                        pd.to_datetime(pos["close_time"]).max() if len(pos) else None,
                        pos["open_time"].max() if len(pos) else None) if t is not None and not pd.isna(t)]
    end = pd.Timestamp(as_of).normalize() if as_of is not None else max(ends).normalize()
    dates = pd.bdate_range(start, max(end, start))
    if len(dates) == 0:  # start w weekend i brak kolejnych dni
        dates = pd.bdate_range(start, periods=1)
    n = len(dates)
    dvals = dates.values

    def day_idx(ts) -> int:
        return int(np.searchsorted(dvals, np.datetime64(pd.Timestamp(ts).normalize()), side="left"))

    # dzień, do którego odnosi się "cena bieżąca" otwartych pozycji (ostatnia sesja nie późniejsza niż raport)
    report_idx = n - 1
    if report_date is not None:
        report_idx = int(np.searchsorted(dvals, np.datetime64(pd.Timestamp(report_date).normalize()), side="right")) - 1
        report_idx = min(max(report_idx, 0), n - 1)

    symbols = sorted(pos["symbol"].unique().tolist())
    sidx = {s: j for j, s in enumerate(symbols)}
    m = len(symbols)
    qty = np.zeros((n, m))
    cost = np.zeros((n, m))
    for p in pos.itertuples(index=False):
        i0 = day_idx(p.open_time)
        i1 = n if pd.isna(p.close_time) else day_idx(p.close_time)
        if i0 >= n or i1 <= i0:
            continue  # pozycja intraday albo spoza zakresu: jej wynik jest w operacjach gotówkowych
        sgn = -1.0 if p.type == "SELL" else 1.0
        qty[i0:i1, sidx[p.symbol]] += sgn * p.volume
        cost[i0:i1, sidx[p.symbol]] += sgn * p.volume * p.open_price

    meta = meta.reindex(symbols)
    missing_meta = meta.index[meta["asset_class"].isna()].tolist()
    if missing_meta:
        warnings.append("Brak mapowania dla: " + ", ".join(missing_meta) + " (przyjęto akcje w PLN).")
    meta = meta.assign(
        asset_class=meta["asset_class"].fillna("STOCK"),
        currency=meta["currency"].fillna("PLN"),
        multiplier=pd.to_numeric(meta["multiplier"], errors="coerce").fillna(1.0),
    )
    for col, default in (("sector", "Nieznany"), ("country", "Nieznany"), ("name", None)):
        if col not in meta.columns:
            meta[col] = default
    meta["sector"] = meta["sector"].fillna("Nieznany")
    meta["country"] = meta["country"].fillna("Nieznany")
    meta["name"] = meta["name"].where(meta["name"].notna(), pd.Series(meta.index, index=meta.index))

    is_cfd = (meta["asset_class"] == "CFD").values
    mult = np.where(is_cfd, meta["multiplier"].values.astype(float), 1.0)

    # ceny: brak notowań -> pozycja wyceniana po cenie otwarcia (ostrzeżenie)
    px = _align(prices, dates, symbols) if len(symbols) else pd.DataFrame(index=dates)
    with np.errstate(divide="ignore", invalid="ignore"):
        avg_open = np.where(np.abs(qty) > 1e-12, cost / qty, np.nan)
    no_price = []
    for j, s in enumerate(symbols):
        col = px[s]
        if col.isna().all():
            no_price.append(s)
            px[s] = pd.Series(avg_open[:, j], index=dates).ffill().bfill()
        elif col.isna().any():
            px[s] = col.fillna(pd.Series(avg_open[:, j], index=dates)).ffill().bfill()
    if no_price:
        warnings.append("Brak notowań dla: " + ", ".join(no_price) +
                        " - pozycje wyceniono po cenie otwarcia. Popraw ticker w tabeli mapowań.")
    px = px.fillna(0.0)

    cur = meta["currency"].tolist()
    fx_al = _align(fx, dates, sorted(set(cur) | {base})) if len(symbols) else pd.DataFrame(index=dates)
    if len(symbols):
        fx_al[base] = 1.0
        missing_fx = [c for c in set(cur) if fx_al[c].isna().all()]
        if missing_fx:
            warnings.append("Brak kursów dla walut: " + ", ".join(missing_fx) + " (przyjęto 1,00).")
        fx_al = fx_al.fillna(1.0)
        fxs = pd.DataFrame({s: fx_al[c].values for s, c in zip(symbols, cur)}, index=dates)
    else:
        fxs = pd.DataFrame(index=dates)

    pxv, fxv = px.values if m else np.zeros((n, 0)), fxs.values if m else np.zeros((n, 0))

    rate: dict[str, np.ndarray] = {}

    def acct_rate(c: str) -> np.ndarray:
        """Kurs waluty rachunku do waluty bazowej, dzień po dniu."""
        if c not in rate:
            if c == base:
                rate[c] = np.ones(n)
            elif c in fx.columns and fx[c].notna().any():
                rate[c] = _align(fx[[c]], dates, [c])[c].fillna(1.0).values
            else:
                warnings.append(f"Brak kursu {c}/{base} dla rachunku w {c} (przyjęto 1,00).")
                rate[c] = np.ones(n)
        return rate[c]

    # Wycena pozycji. Ceny transakcji z raportu są pewne; notowania dają tylko kształt ścieżki pomiędzy nimi.
    # Gdy poziom notowań nie pasuje do cen transakcji (split, pensy zamiast funtów, inny kontrakt, zły ticker),
    # pozycja jest wyceniana notowaniami przeskalowanymi tak, by w dniu otwarcia i zamknięcia zgadzały się z raportem.
    # CFD z wynikiem w raporcie: niezrealizowany wynik = wynik z raportu x (jaka część drogi od ceny otwarcia do
    # ceny końcowej jest już za nami). Nie zależy to od mnożnika, waluty instrumentu ani poziomu notowań.
    mv = np.zeros((n, m))            # ilość x cena (do ekspozycji i ceny efektywnej)
    mv_val = np.zeros((n, m))        # to samo, ale tylko pozycje wyceniane wzorem cena x ilość
    cost_val = np.zeros((n, m))
    upl_direct = np.zeros((n, m))    # wynik CFD wprost z raportu, już w walucie bazowej
    rescaled: set[str] = set()
    for p in pos.itertuples(index=False):
        i0 = day_idx(p.open_time)
        i1 = n if pd.isna(p.close_time) else day_idx(p.close_time)
        if i0 >= n or i1 <= i0:
            continue
        j = sidx[p.symbol]
        series = pxv[:, j]
        last = min(i1, n) - 1
        anchor = i1 if i1 < n else max(report_idx, i0)
        r0 = p.open_price / series[i0] if series[i0] > 0 and p.open_price > 0 else 1.0
        r1 = p.end_price / series[anchor] if (p.end_price == p.end_price and p.end_price > 0 and series[anchor] > 0) else r0
        tol = CFD_LEVEL_TOLERANCE if is_cfd[j] else STOCK_LEVEL_TOLERANCE
        sgn = -1.0 if p.type == "SELL" else 1.0
        move = (p.end_price - p.open_price) if p.end_price == p.end_price else 0.0
        direct = bool(is_cfd[j] and p.gross == p.gross and abs(p.gross) >= 1.0 and p.open_price > 0
                      and abs(move) > 1e-9 * p.open_price and p.gross * sgn * move > 0)
        if direct or p.symbol in no_price or max(abs(np.log(r0)), abs(np.log(r1))) > tol:
            span = anchor - i0
            # po dniu raportu współczynnik skali zostaje stały - dalej liczy się już tylko ruch notowań
            w = np.clip((np.arange(i0, last + 1) - i0) / span, 0.0, 1.0) if span > 0 else np.zeros(last + 1 - i0)
            k = np.exp((1.0 - w) * np.log(r0) + w * np.log(r1))
            if p.symbol not in no_price and not is_cfd[j]:
                rescaled.add(p.symbol)
        else:
            k = 1.0
        path = series[i0:last + 1] * k
        mv[i0:last + 1, j] += sgn * p.volume * path
        if direct:
            upl_direct[i0:last + 1, j] += p.gross * (path - p.open_price) / move * acct_rate(str(p.ccy))[i0:last + 1]
        else:
            mv_val[i0:last + 1, j] += sgn * p.volume * path
            cost_val[i0:last + 1, j] += sgn * p.volume * p.open_price
    if rescaled:
        warnings.append("Notowania w innej skali niż ceny transakcji z raportu (split, inna waluta lub inny kontrakt) dla: "
                        + ", ".join(sorted(rescaled)) + ". Wycenę dopasowano do cen transakcji; sprawdź ticker w zakładce Tickery.")
    if m:
        with np.errstate(divide="ignore", invalid="ignore"):
            pxv = np.where(np.abs(qty) > 1e-12, mv / qty, pxv)     # cena efektywna: wartość / ilość
        px = pd.DataFrame(pxv, index=dates, columns=symbols)
    nominal = mv * mult * fxv
    upl = (mv_val - cost_val) * mult * fxv + upl_direct
    value = np.where(is_cfd, upl, nominal) if m else np.zeros((n, 0))

    # gotówka i przepływy: saldo prowadzone w walucie każdego rachunku, wyceniane dziennym kursem do waluty bazowej
    has_ccy = "currency" in cash.columns
    ccys = sorted(set(cash["currency"].dropna().astype(str))) if has_ccy and len(cash) else [account_currency]
    for c in ccys:
        acct_rate(c)
    ccy_delta = {c: np.zeros(n) for c in ccys}
    flows = np.zeros(n)
    by_symbol = np.zeros((n, m))
    bucket_cash = np.zeros(n)
    bucket_other = np.zeros(n)
    dropped = 0
    for row in cash.itertuples(index=False):
        i = day_idx(row.time)
        if i >= n:
            dropped += 1
            continue
        c = str(row.currency) if has_ccy else account_currency
        ccy_delta[c][i] += row.amount
        amount = row.amount * rate[c][i]
        if row.kind in EXTERNAL_FLOW_KINDS:
            flows[i] += amount
        elif row.symbol and row.symbol in sidx:
            by_symbol[i, sidx[row.symbol]] += amount
        elif row.symbol:
            bucket_other[i] += amount
        else:
            bucket_cash[i] += amount
    if dropped:
        warnings.append(f"Pominięto {dropped} operacji gotówkowych po dacie raportu.")

    cash_ccy, cash_fx = {}, {}
    for c in ccys:
        bal = np.cumsum(ccy_delta[c])
        cash_ccy[c] = bal * rate[c]
        # zmiana wartości salda z tytułu samego kursu waluty rachunku
        cash_fx[c] = np.concatenate([[0.0], bal[:-1] * np.diff(rate[c])])
        bucket_cash += cash_fx[c]
    cash_bal = np.sum([cash_ccy[c] for c in ccys], axis=0) if ccys else np.zeros(n)
    equity = cash_bal + value.sum(axis=1)

    prev = np.concatenate([[0.0], equity[:-1]])
    start_cap = prev + flows
    # Ujemna wartość portfela oznacza błąd danych (zła wycena, brakująca operacja), a nie prawdziwy wynik.
    # Takie dni nie wchodzą do łańcucha stóp zwrotu - inaczej jeden zły dzień zerowałby TWR na zawsze.
    bad = (equity <= 0) & (np.abs(equity) > 1e-9)
    if bad.any():
        idx = np.flatnonzero(bad)
        worst = int(idx[np.argmin(equity[idx])])
        parts = {s: float(value[worst, j]) for j, s in enumerate(symbols)}
        parts["gotówka"] = float(cash_bal[worst])
        name, amount = min(parts.items(), key=lambda kv: kv[1])
        detail = ""
        if name in sidx:
            j = sidx[name]
            detail = (f" ({'CFD' if is_cfd[j] else 'akcje/ETF'}, ilość {qty[worst, j]:g}, cena {pxv[worst, j]:.2f} "
                      f"{cur[j]}, mnożnik {mult[j]:g})")
        warnings.append(
            f"W {len(idx)} dniach ({dates[idx[0]].date()} - {dates[idx[-1]].date()}) wartość portfela wychodzi ujemna "
            f"(najniżej {equity[worst]:.0f} {base}). Największy ujemny składnik: {name}{detail}: {amount:.0f} {base}. "
            "To błąd danych lub wyceny tej pozycji; te dni pominięto w stopach zwrotu.")
    with np.errstate(divide="ignore", invalid="ignore"):
        after_bad = np.concatenate([[False], bad[:-1]])          # dzień powrotu z ujemnej wartości też nie ma sensownej stopy
        ret = np.where((start_cap > 1e-9) & ~bad & ~after_bad, equity / start_cap - 1.0, 0.0)

    dvalue = np.diff(np.vstack([np.zeros((1, m)), value]), axis=0) if m else np.zeros((n, 0))
    pnl = pd.DataFrame(dvalue + by_symbol, index=dates, columns=symbols)
    pnl[CASH_BUCKET] = bucket_cash
    pnl[OTHER_BUCKET] = bucket_other

    def frame(arr):
        return pd.DataFrame(arr, index=dates, columns=symbols)

    return Portfolio(
        dates=dates, qty=frame(qty), cost_local=frame(cost), prices=px, fx=fxs, value=frame(value),
        exposure=frame(nominal), cash=pd.Series(cash_bal, index=dates, name="cash"),
        equity=pd.Series(equity, index=dates, name="equity"),
        flows=pd.Series(flows, index=dates, name="flows"),
        returns=pd.Series(ret, index=dates, name="portfolio"),
        pnl=pnl, meta=meta, cash_by_symbol=frame(by_symbol), warnings=warnings,
        cash_by_currency=pd.DataFrame(cash_ccy, index=dates), base=base,
        cash_fx_pnl=pd.DataFrame(cash_fx, index=dates),
    )
