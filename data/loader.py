"""Spina parser, mapowanie i dostawcę notowań w jeden obiekt gotowy do analizy."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core.portfolio import Portfolio, reconstruct
from data.xtb_parser import XTBExport

HISTORY_PAD_DAYS = 400          # ile historii przed pierwszą transakcją pobieramy (ryzyko, bety)
BENCHMARK_KEYS = ["WIG20", "WIG", "SP500", "MSCIW"]
FACTOR_PROXY_KEYS = ["NDX", "DAX", "GOLD", "OIL"]


@dataclass
class MarketBundle:
    """Dane rynkowe w pełnym zakresie historii (szerszym niż życie portfela)."""
    prices: pd.DataFrame                 # ceny lokalne, kolumny = symbole XTB
    volumes: pd.DataFrame                # wolumeny
    fx: pd.DataFrame                     # PLN za jednostkę waluty, kolumny = waluty
    benchmarks_pln: pd.DataFrame         # poziomy benchmarków i czynników w PLN
    benchmarks_local: pd.DataFrame       # poziomy w walucie własnej
    warnings: list[str] = field(default_factory=list)
    source: str = ""

    def asset_returns_pln(self, meta: pd.DataFrame, symbols=None) -> pd.DataFrame:
        """Dzienne zwroty instrumentów widziane z PLN (akcje/ETF: cena x kurs; CFD: sama cena)."""
        symbols = list(symbols if symbols is not None else self.prices.columns)
        out = {}
        for s in symbols:
            if s not in self.prices.columns:
                continue
            p = self.prices[s].dropna()
            if s in meta.index and meta.at[s, "asset_class"] != "CFD":
                cur = meta.at[s, "currency"]
                if cur in self.fx.columns and cur != "PLN":
                    p = (p * self.fx[cur].reindex(p.index).ffill()).dropna()
            out[s] = p.pct_change()
        return pd.DataFrame(out).iloc[1:]


def load_market(export: XTBExport, mapping: pd.DataFrame, provider, as_of=None, extra_symbols=()) -> MarketBundle:
    as_of = pd.Timestamp(as_of or export.as_of or pd.Timestamp.today()).normalize()
    first = [t for t in (export.cash["time"].min() if len(export.cash) else None,
                         export.closed["open_time"].min() if len(export.closed) else None,
                         export.open["open_time"].min() if len(export.open) else None)
             if t is not None and not pd.isna(t)]
    start = (min(first).normalize() if first else as_of) - pd.Timedelta(days=HISTORY_PAD_DAYS)
    warnings: list[str] = []
    mp = mapping.set_index("symbol")
    symbols = list(dict.fromkeys(list(export.symbols) + list(extra_symbols)))

    prices, volumes = {}, {}
    for s in symbols:
        if s not in mp.index:
            warnings.append(f"{s}: brak w tabeli mapowań.")
            continue
        row = mp.loc[s].to_dict() | {"symbol": s}
        try:
            df = provider.price_history(row, start, as_of)
            if len(df):
                prices[s] = df["close"]
                volumes[s] = df["volume"] if "volume" in df.columns else np.nan
            else:
                warnings.append(f"{s}: źródło nie zwróciło notowań.")
        except Exception as exc:
            warnings.append(f"{s}: nie udało się pobrać notowań ({exc}).")

    currencies = sorted(set(mp.reindex(symbols)["currency"].dropna()) | {"USD", "EUR", "PLN"}
                        | ({export.currency} if export.currency else set()))
    fx = {}
    for c in currencies:
        try:
            fx[c] = provider.fx_rates(c, start, as_of)
        except Exception as exc:
            warnings.append(f"Kurs {c}/PLN: nie udało się pobrać ({exc}).")
    fx_df = pd.DataFrame(fx).sort_index().ffill()
    fx_df["PLN"] = 1.0

    b_local, b_pln = {}, {}
    for key in BENCHMARK_KEYS + FACTOR_PROXY_KEYS:
        try:
            s, cur = provider.benchmark(key, start, as_of)
            s = s.dropna()
            if not len(s):
                raise ValueError("brak danych")
            b_local[key] = s
            rate = fx_df[cur].reindex(s.index.union(fx_df.index)).ffill().reindex(s.index) if cur != "PLN" else 1.0
            b_pln[key] = s * rate
        except Exception as exc:
            warnings.append(f"Benchmark {key}: nie udało się pobrać ({exc}).")

    warnings += list(getattr(provider, "warnings", []))
    return MarketBundle(
        prices=pd.DataFrame(prices).sort_index(), volumes=pd.DataFrame(volumes).sort_index(), fx=fx_df,
        benchmarks_pln=pd.DataFrame(b_pln).sort_index().ffill(),
        benchmarks_local=pd.DataFrame(b_local).sort_index().ffill(),
        warnings=warnings, source=getattr(provider, "name", ""),
    )


def to_pln(export: XTBExport, fx: pd.DataFrame) -> XTBExport:
    """Konto w walucie innej niż PLN: przelicza kwoty na PLN po kursie z dnia operacji."""
    if export.currency == "PLN" or export.currency not in fx.columns:
        return export
    rate = fx[export.currency].sort_index()

    def conv(times, values):
        r = rate.reindex(rate.index.union(pd.DatetimeIndex(times.dt.normalize()))).ffill().bfill()
        return values * r.reindex(times.dt.normalize()).values

    out = XTBExport(closed=export.closed.copy(), open=export.open.copy(), cash=export.cash.copy(),
                    as_of=export.as_of, currency="PLN", notes=list(export.notes))
    if len(out.cash):
        out.cash["amount"] = conv(out.cash["time"], out.cash["amount"])
    for df, tcol in ((out.closed, "close_time"), (out.open, "open_time")):
        if len(df):
            for c in ("purchase_value", "sale_value", "margin", "commission", "swap", "rollover", "gross_pl"):
                if c in df.columns:
                    df[c] = conv(df[tcol], df[c])
    return out


def build_portfolio(export: XTBExport, mapping: pd.DataFrame, market: MarketBundle) -> Portfolio:
    export = to_pln(export, market.fx)
    pf = reconstruct(export.closed, export.open, export.cash, mapping.set_index("symbol"),
                     market.prices, market.fx, as_of=export.as_of)
    pf.warnings = list(market.warnings) + pf.warnings
    return pf
