"""Kontekst analizy: sparsowany eksport + mapowanie + dane rynkowe + odtworzony portfel.

Jeden kontekst = jedna sesja użytkownika. Trzymany wyłącznie w pamięci procesu.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from core.portfolio import Portfolio
from data.loader import BENCHMARK_KEYS, MarketBundle, build_portfolio, load_market
from data.mapping import build_mapping
from data.sample_generator import ensure_sample
from data.xtb_parser import XTBExport, parse_export

DEFAULT_RF = 0.04
DEFAULT_BENCH = "MSCIW"
BENCH_CHOICES = ["MSCIW", "SP500", "WIG", "WIG20"]


def make_provider(is_demo: bool):
    if is_demo:
        from data.synthetic import SyntheticMarketData
        return SyntheticMarketData()
    from data.providers import LiveMarketData
    return LiveMarketData()


@dataclass
class Ctx:
    export: XTBExport
    mapping: pd.DataFrame
    market: MarketBundle
    pf: Portfolio
    provider: object
    is_demo: bool
    files: list = field(default_factory=list, repr=False)
    _cache: dict = field(default_factory=dict, repr=False)

    def memo(self, key, fn):
        """Wynik drogiego obliczenia liczony raz na sesję."""
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def bench_returns(self) -> pd.DataFrame:
        """Dzienne zwroty benchmarków w PLN na kalendarzu portfela."""
        def build():
            from core.performance import levels_to_returns
            cols = [k for k in BENCHMARK_KEYS if k in self.market.benchmarks_pln.columns]
            return levels_to_returns(self.market.benchmarks_pln[cols], self.pf.dates)
        return self.memo("bench_returns", build)

    def bench_series(self, key: str) -> pd.Series:
        br = self.bench_returns()
        if key in br.columns:
            return br[key]
        return br.iloc[:, 0] if br.shape[1] else pd.Series(dtype=float)

    def asset_returns(self) -> pd.DataFrame:
        return self.memo("asset_returns", lambda: self.market.asset_returns_pln(self.pf.meta))

    def risk_inputs(self, lookback: int = 504):
        """(zwroty instrumentów w PLN, bieżące ekspozycje, uwagi) - wejście do miar ryzyka."""
        def build():
            from core.risk import risk_inputs
            return risk_inputs(self.pf.current_exposure(), self.asset_returns(), lookback)
        return self.memo(("risk_inputs", lookback), build)


def build_context(files: list[tuple[str, bytes]] | None, overrides: pd.DataFrame | None = None,
                  provider=None) -> Ctx:
    """files=None -> dane przykładowe (tryb demo, notowania syntetyczne, bez sieci)."""
    is_demo = files is None
    if is_demo:
        with open(ensure_sample(), "rb") as fh:
            files = [("xtb_sample_export.xlsx", fh.read())]
    export = parse_export(files)
    mapping = build_mapping(export.symbols, overrides)
    provider = provider or make_provider(is_demo)
    market = load_market(export, mapping, provider)
    pf = build_portfolio(export, mapping, market)
    return Ctx(export=export, mapping=mapping, market=market, pf=pf, provider=provider, is_demo=is_demo,
               files=files)
