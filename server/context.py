"""Kontekst analizy: sparsowany eksport + mapowanie + dane rynkowe + odtworzony portfel.

Jeden kontekst = jedna sesja użytkownika. Trzymany wyłącznie w pamięci procesu.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from core.portfolio import Portfolio
from data.loader import BASE_CHOICES, BENCHMARK_KEYS, MarketBundle, build_portfolio, convert_export, load_market
from data.mapping import build_mapping
from data.sample_generator import ensure_sample
from data.xtb_parser import XTBExport, merge_exports, parse_accounts

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
    export: XTBExport             # kwoty przeliczone na walutę wyświetlania (bazową)
    mapping: pd.DataFrame
    market: MarketBundle
    pf: Portfolio
    provider: object
    is_demo: bool
    files: list = field(default_factory=list, repr=False)
    export_raw: XTBExport | None = None       # scalone rachunki, kwoty w walutach rachunków (jak w plikach)
    export_pln: XTBExport | None = None       # to samo w złotych - do podatków
    parsed: list = field(default_factory=list, repr=False)   # wszystkie wgrane rachunki, także odznaczone
    overrides: pd.DataFrame | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def base(self) -> str:
        return self.market.base

    @property
    def accounts(self) -> list[dict]:
        return list(self.export_raw.accounts) if self.export_raw is not None else []

    @property
    def account_currencies(self) -> tuple:
        cur = {a["currency"] for a in self.accounts if a.get("selected")}
        return tuple(sorted(cur)) or ("PLN",)

    def cash_by_currency(self) -> dict:
        """Dzisiejsza gotówka per waluta rachunku, wyrażona w walucie bazowej."""
        cc = self.pf.cash_by_currency
        if cc is None or not len(cc):
            return {self.base: float(self.pf.cash.iloc[-1])}
        return {str(k): float(v) for k, v in cc.iloc[-1].items()}

    def memo(self, key, fn):
        """Wynik drogiego obliczenia liczony raz na sesję."""
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def bench_returns(self) -> pd.DataFrame:
        """Dzienne zwroty benchmarków w walucie bazowej na kalendarzu portfela."""
        def build():
            from core.performance import levels_to_returns
            cols = [k for k in BENCHMARK_KEYS if k in self.market.benchmarks_base.columns]
            return levels_to_returns(self.market.benchmarks_base[cols], self.pf.dates)
        return self.memo("bench_returns", build)

    def bench_label(self, key: str) -> str:
        from data.providers import benchmark_label
        return benchmark_label(key, self.base, self.market.benchmark_currency.get(key))

    def bench_series(self, key: str) -> pd.Series:
        br = self.bench_returns()
        if key in br.columns:
            return br[key]
        return br.iloc[:, 0] if br.shape[1] else pd.Series(dtype=float)

    def asset_returns(self) -> pd.DataFrame:
        return self.memo("asset_returns", lambda: self.market.asset_returns(self.pf.meta))

    def risk_inputs(self, lookback: int = 504):
        """(zwroty instrumentów w walucie bazowej, bieżące ekspozycje, uwagi) - wejście do miar ryzyka."""
        def build():
            from core.risk import risk_inputs
            return risk_inputs(self.pf.current_exposure(), self.asset_returns(), lookback)
        return self.memo(("risk_inputs", lookback), build)


def build_context(files: list[tuple[str, bytes]] | None, overrides: pd.DataFrame | None = None,
                  provider=None, base: str = "PLN", accounts: list[str] | None = None, parsed: list | None = None,
                  exclude: list[str] | None = None) -> Ctx:
    """files=None -> dane przykładowe (tryb demo, notowania syntetyczne, bez sieci).

    base     - waluta wyświetlania (PLN / USD / EUR),
    accounts - numery rachunków do wliczenia (None albo nic pasującego = wszystkie wgrane),
    exclude  - numery rachunków do pominięcia (gdy nie podano `accounts`); nowo wgrany rachunek jest wliczany,
    parsed   - już sparsowane rachunki (zmiana waluty lub zaznaczenia nie wymaga ponownego czytania plików).
    """
    is_demo = files is None
    if is_demo:
        with open(ensure_sample(), "rb") as fh:
            files = [("xtb_sample_export.xlsx", fh.read())]
    if parsed is None:
        parsed = parse_accounts(files)
    ids = {e.account for e in parsed}
    chosen = [a for a in (accounts or []) if a in ids] or None
    if chosen is None and exclude:
        chosen = [e.account for e in parsed if e.account not in set(exclude)] or None
    raw = merge_exports(parsed, chosen)
    base = base if base in BASE_CHOICES else "PLN"
    mapping = build_mapping(raw.symbols, overrides, hints=raw.instruments)
    provider = provider or make_provider(is_demo)
    market = load_market(raw, mapping, provider, base=base)
    pf = build_portfolio(raw, mapping, market)
    export = convert_export(raw, market.fx, market.base)
    export_pln = export if market.base == "PLN" else convert_export(raw, market.fx_pln, "PLN")
    return Ctx(export=export, mapping=mapping, market=market, pf=pf, provider=provider, is_demo=is_demo,
               files=files, export_raw=raw, export_pln=export_pln, parsed=parsed, overrides=overrides)
