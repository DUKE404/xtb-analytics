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

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

EXTERNAL_FLOW_KINDS = ("DEPOSIT", "WITHDRAWAL", "TRANSFER")   # "INTERNAL" (przelew między własnymi rachunkami) nie jest przepływem
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
                base: str = "PLN") -> Portfolio:
    """Odtwarza portfel dzień po dniu.

    closed / open_ / cash - ramki z parsera (data/xtb_parser.py),
    meta   - mapowanie instrumentów z indeksem = symbol (asset_class, currency, multiplier, ...),
    prices - ceny zamknięcia w walucie instrumentu (kolumny = symbole),
    fx     - kursy: ile waluty bazowej za jednostkę waluty (kolumny = kody walut; baza = 1),
    account_currency - waluta kwot w `cash`, gdy ramka nie ma własnej kolumny `currency` (kilka rachunków),
    base   - waluta bazowa wyniku. Gotówka jest prowadzona osobno w każdej walucie rachunku i codziennie
             przeliczana po bieżącym kursie; zmiana jej wartości z tytułu kursu trafia do wyniku (koszyk gotówki).
    """
    warnings: list[str] = []
    pos_cols = ["symbol", "type", "volume", "open_time", "open_price", "close_time"]
    o = open_.copy()
    o["close_time"] = pd.NaT
    pos = pd.concat([closed[pos_cols], o[pos_cols]], ignore_index=True) if len(closed) or len(o) else \
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
    nominal = qty * pxv * mult * fxv
    upl = (qty * pxv - cost) * mult * fxv
    value = np.where(is_cfd, upl, nominal) if m else np.zeros((n, 0))

    # gotówka i przepływy: saldo prowadzone w walucie każdego rachunku, wyceniane dziennym kursem do waluty bazowej
    has_ccy = "currency" in cash.columns
    ccys = sorted(set(cash["currency"].dropna().astype(str))) if has_ccy and len(cash) else [account_currency]
    rate = {}
    for c in ccys:
        if c == base:
            rate[c] = np.ones(n)
        elif c in fx.columns and fx[c].notna().any():
            rate[c] = _align(fx[[c]], dates, [c])[c].fillna(1.0).values
        else:
            warnings.append(f"Brak kursu {c}/{base} dla gotówki na rachunku (przyjęto 1,00).")
            rate[c] = np.ones(n)
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
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.where(start_cap > 1e-9, equity / start_cap - 1.0, 0.0)

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
