"""Spina parser, mapowanie i dostawcę notowań w jeden obiekt gotowy do analizy.

Waluta bazowa (wyświetlania) jest parametrem: wszystkie kursy w `MarketBundle.fx` mówią, ile waluty bazowej
kosztuje jednostka danej waluty. Kursy do PLN (`fx_pln`) zostają osobno - potrzebują ich podatki.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.portfolio import Portfolio, reconstruct
from data.xtb_parser import XTBExport

HISTORY_PAD_DAYS = 400          # ile historii przed pierwszą transakcją pobieramy (ryzyko, bety)
BENCHMARK_KEYS = ["WIG20", "WIG", "SP500", "MSCIW"]
FACTOR_PROXY_KEYS = ["NDX", "DAX", "GOLD", "OIL"]
BASE_CHOICES = ["PLN", "USD", "EUR"]
MONEY_COLS = ("purchase_value", "sale_value", "margin", "commission", "swap", "rollover", "gross_pl")


@dataclass
class MarketBundle:
    """Dane rynkowe w pełnym zakresie historii (szerszym niż życie portfela)."""
    prices: pd.DataFrame                 # ceny lokalne, kolumny = symbole XTB
    volumes: pd.DataFrame                # wolumeny
    fx: pd.DataFrame                     # waluta bazowa za jednostkę waluty, kolumny = waluty (baza = 1)
    benchmarks_base: pd.DataFrame        # poziomy benchmarków i czynników w walucie bazowej
    benchmarks_local: pd.DataFrame       # poziomy w walucie własnej
    warnings: list[str] = field(default_factory=list)
    source: str = ""
    base: str = "PLN"
    fx_pln: pd.DataFrame | None = None   # PLN za jednostkę waluty (kursy NBP) - do podatków
    benchmark_currency: dict = field(default_factory=dict)

    def asset_returns(self, meta: pd.DataFrame, symbols=None) -> pd.DataFrame:
        """Dzienne zwroty instrumentów widziane z waluty bazowej (akcje/ETF: cena x kurs; CFD: sama cena)."""
        symbols = list(symbols if symbols is not None else self.prices.columns)
        out = {}
        for s in symbols:
            if s not in self.prices.columns:
                continue
            p = self.prices[s].dropna()
            if s in meta.index and meta.at[s, "asset_class"] != "CFD":
                cur = meta.at[s, "currency"]
                if cur in self.fx.columns and cur != self.base:
                    p = (p * self.fx[cur].reindex(p.index).ffill()).dropna()
            out[s] = p.pct_change()
        return pd.DataFrame(out).iloc[1:]

    asset_returns_pln = asset_returns     # dawna nazwa (gdy bazą był zawsze PLN)


def _first_event(export: XTBExport, as_of):
    first = [t for t in (export.cash["time"].min() if len(export.cash) else None,
                         export.closed["open_time"].min() if len(export.closed) else None,
                         export.open["open_time"].min() if len(export.open) else None)
             if t is not None and not pd.isna(t)]
    return min(first).normalize() if first else as_of


def _account_currencies(export: XTBExport) -> set:
    out = set()
    for df in (export.cash, export.closed, export.open):
        if "currency" in df.columns and len(df):
            out |= set(df["currency"].dropna().astype(str))
    if export.currency and export.currency != "MIX":
        out.add(export.currency)
    return out


def load_market(export: XTBExport, mapping: pd.DataFrame, provider, as_of=None, extra_symbols=(),
                base: str = "PLN") -> MarketBundle:
    as_of = pd.Timestamp(as_of or export.as_of or pd.Timestamp.today()).normalize()
    start = _first_event(export, as_of) - pd.Timedelta(days=HISTORY_PAD_DAYS)
    warnings: list[str] = []
    mp = mapping.set_index("symbol")
    symbols = list(dict.fromkeys(list(export.symbols) + list(extra_symbols)))

    prices, volumes = {}, {}

    def fetch(sym):
        row = mp.loc[sym].to_dict() | {"symbol": sym}
        try:
            df = provider.price_history(row, start, as_of)
            return sym, df, None if len(df) else f"{sym}: źródło nie zwróciło notowań."
        except Exception as exc:
            return sym, None, f"{sym}: nie udało się pobrać notowań ({exc})."

    todo = []
    for sym in symbols:
        if sym in mp.index:
            todo.append(sym)
        else:
            warnings.append(f"{sym}: brak w tabeli mapowań.")
    workers = int(getattr(provider, "max_workers", 1) or 1)
    if workers > 1 and len(todo) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:    # pobieranie to czekanie na sieć - wątki wystarczą
            results = list(pool.map(fetch, todo))
    else:
        results = [fetch(sym) for sym in todo]
    for sym, df, warn in results:
        if warn:
            warnings.append(warn)
        if df is not None and len(df):
            prices[sym] = df["close"]
            volumes[sym] = df["volume"] if "volume" in df.columns else np.nan

    currencies = sorted(set(mp.reindex(symbols)["currency"].dropna()) | {"USD", "EUR", "PLN", base}
                        | _account_currencies(export))
    fx = {}
    for c in currencies:
        try:
            fx[c] = provider.fx_rates(c, start, as_of)
        except Exception as exc:
            warnings.append(f"Kurs {c}/PLN: nie udało się pobrać ({exc}).")
    fx_pln = pd.DataFrame(fx).sort_index().ffill()
    fx_pln["PLN"] = 1.0
    if base != "PLN" and (base not in fx_pln.columns or fx_pln[base].dropna().empty):
        warnings.append(f"Brak kursów {base}/PLN - pokazuję wartości w PLN.")
        base = "PLN"
    fx_base = fx_pln.div(fx_pln[base], axis=0) if base != "PLN" else fx_pln.copy()
    fx_base[base] = 1.0

    b_local, b_base, b_cur = {}, {}, {}
    for key in BENCHMARK_KEYS + FACTOR_PROXY_KEYS:
        try:
            s, cur = provider.benchmark(key, start, as_of)
            s = s.dropna()
            if not len(s):
                raise ValueError("brak danych")
            b_local[key], b_cur[key] = s, cur
            if cur == base or cur not in fx_base.columns:
                b_base[key] = s
            else:
                b_base[key] = s * fx_base[cur].reindex(s.index.union(fx_base.index)).ffill().reindex(s.index)
        except Exception as exc:
            warnings.append(f"Benchmark {key}: nie udało się pobrać ({exc}).")

    warnings += list(getattr(provider, "warnings", []))
    return MarketBundle(
        prices=pd.DataFrame(prices).sort_index(), volumes=pd.DataFrame(volumes).sort_index(), fx=fx_base,
        benchmarks_base=pd.DataFrame(b_base).sort_index().ffill(),
        benchmarks_local=pd.DataFrame(b_local).sort_index().ffill(),
        warnings=warnings, source=getattr(provider, "name", ""), base=base, fx_pln=fx_pln, benchmark_currency=b_cur,
    )


def convert_export(export: XTBExport, fx: pd.DataFrame, target: str) -> XTBExport:
    """Przelicza kwoty z waluty rachunku (kolumna `currency` wiersza albo waluta eksportu) na `target`.

    `fx`: ile waluty docelowej za jednostkę waluty. Kurs z dnia operacji (dla zamkniętych pozycji: z dnia
    zamknięcia; dla otwartych: wartość zakupu z dnia otwarcia, bieżący wynik z daty raportu).
    """
    def rates(df, times):
        cur = df["currency"].astype(str) if "currency" in df.columns else pd.Series(export.currency, index=df.index)
        out = pd.Series(1.0, index=df.index)
        days = pd.DatetimeIndex(pd.to_datetime(times).dt.normalize())
        for c in cur.unique():
            if c == target or c not in fx.columns:
                continue
            r = fx[c].dropna().sort_index()
            r = r[~r.index.duplicated(keep="last")]
            if not len(r):
                continue
            mask = (cur == c).values
            # kurs z dnia operacji, a gdy go brak (weekend, święto) - ostatni wcześniejszy
            pos = np.clip(r.index.searchsorted(days[mask], side="right") - 1, 0, len(r) - 1)
            out.loc[mask] = r.values[pos]
        return out

    out = XTBExport(closed=export.closed.copy(), open=export.open.copy(), cash=export.cash.copy(), as_of=export.as_of,
                    currency=target, notes=list(export.notes), instruments=export.instruments,
                    original_currency=export.original_currency or export.currency, account=export.account,
                    accounts=export.accounts)
    if len(out.cash):
        out.cash["amount"] = out.cash["amount"] * rates(out.cash, out.cash["time"])
    if len(out.closed):
        r = rates(out.closed, out.closed["close_time"])
        for c in MONEY_COLS:
            if c in out.closed.columns:
                out.closed[c] = out.closed[c] * r
    if len(out.open):
        r_open = rates(out.open, out.open["open_time"])
        as_of = pd.Series(export.as_of or pd.Timestamp.today(), index=out.open.index)
        r_now = rates(out.open, as_of)
        for c in ("purchase_value", "margin", "commission"):
            if c in out.open.columns:
                out.open[c] = out.open[c] * r_open
        for c in ("swap", "rollover", "gross_pl"):
            if c in out.open.columns:
                out.open[c] = out.open[c] * r_now
    for df in (out.cash, out.closed, out.open):
        if "currency" in df.columns:
            df["currency"] = target
    return out


def to_pln(export: XTBExport, fx_pln: pd.DataFrame) -> XTBExport:
    return convert_export(export, fx_pln, "PLN")


PRICE_CHECK_TOLERANCE = 0.08


def price_check(export: XTBExport, market: MarketBundle) -> list[str]:
    """Porównuje ostatnią cenę z notowań z "Current price" z raportu XTB dla otwartych pozycji.

    Duża różnica prawie zawsze oznacza zły ticker w tabeli mapowań (inna giełda, inna waluta, pensy zamiast funtów).
    """
    o = export.open
    if not len(o) or "market_price" not in o.columns:
        return []
    bad = []
    for sym, grp in o.groupby("symbol"):
        ref = pd.to_numeric(grp["market_price"], errors="coerce").dropna()
        if not len(ref) or sym not in market.prices.columns:
            continue
        ours = market.prices[sym].dropna()
        if not len(ours) or ref.iloc[-1] <= 0:
            continue
        if abs(float(ours.iloc[-1]) / float(ref.iloc[-1]) - 1.0) > PRICE_CHECK_TOLERANCE:
            pl = lambda x: f"{float(x):.2f}".replace(".", ",")
            bad.append(f"{sym} (notowania {pl(ours.iloc[-1])}, raport XTB {pl(ref.iloc[-1])})")
    if not bad:
        return []
    return ["Cena z notowań odbiega od ceny w raporcie XTB dla: " + "; ".join(bad[:6]) + (" i innych" if len(bad) > 6 else "")
            + ". Sprawdź ticker i walutę w zakładce Tickery."]


def build_portfolio(export: XTBExport, mapping: pd.DataFrame, market: MarketBundle) -> Portfolio:
    """`export` z kwotami w walutach rachunków (tak jak w plikach XTB)."""
    acc = export.currency if export.currency and export.currency != "MIX" else market.base
    pf = reconstruct(export.closed, export.open, export.cash, mapping.set_index("symbol"),
                     market.prices, market.fx, as_of=export.as_of, account_currency=acc, base=market.base)
    pf.warnings = list(market.warnings) + pf.warnings + price_check(export, market)
    return pf
