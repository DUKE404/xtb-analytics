"""Ekrany aplikacji. Każdy ekran to funkcja (ctx, params) -> {"title": ..., "blocks": [...]}.

Rejestr SCREENS łączy nazwę ekranu z funkcją; nawigację (zakładki i pod-zakładki) opisuje NAV -
front pobiera ją z /api/health i nie ma własnej listy ekranów.
"""
from __future__ import annotations

from server.context import BENCH_CHOICES, DEFAULT_BENCH, DEFAULT_RF


class Params:
    """Parametry zapytania z bezpiecznym rzutowaniem i ograniczeniami zakresu."""

    def __init__(self, raw: dict | None = None):
        self.raw = dict(raw or {})
        self.rf = self.float("rf", DEFAULT_RF, 0.0, 0.25)
        bench = str(self.raw.get("bench", DEFAULT_BENCH))
        self.bench = bench if bench in BENCH_CHOICES else DEFAULT_BENCH

    def str(self, name: str, default: str, allowed=None) -> str:
        v = self.raw.get(name, default)
        v = default if v is None else str(v)
        return v if allowed is None or v in allowed else default

    def float(self, name: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
        try:
            v = float(str(self.raw.get(name, default)).replace(",", "."))
        except (TypeError, ValueError):
            v = default
        if v != v:  # NaN
            v = default
        if lo is not None:
            v = max(lo, v)
        if hi is not None:
            v = min(hi, v)
        return v

    def int(self, name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
        return int(round(self.float(name, default, lo, hi)))

    def bool(self, name: str, default: bool = False) -> bool:
        v = self.raw.get(name, default)
        if isinstance(v, bool):
            return v
        return str(v).lower() in ("1", "true", "tak", "on", "yes")


from server.screens import companies, fund, markets, overview, watchlist, performance, risk, sim, tax, trader  # noqa: E402

SCREENS = {
    "companies-list": watchlist.companies_list,
    "companies": companies.companies,
    "fund-longshort": fund.longshort,
    "fund-limits": fund.limits,
    "overview": overview.overview,
    "data": overview.data,
    "perf-summary": fund.tearsheet,
    "perf-benchmarks": performance.benchmarks,
    "perf-periods": performance.periods,
    "perf-attribution": performance.attribution,
    "risk-var": risk.var,
    "risk-correlation": risk.correlation,
    "risk-exposure": risk.exposure,
    "risk-factors": risk.factor_exposure,
    "risk-cfd": risk.cfd_screen,
    "sim-stress": sim.stress_screen,
    "sim-montecarlo": sim.montecarlo_screen,
    "sim-whatif": sim.whatif_screen,
    "sim-optimize": sim.optimize_screen,
    "trader-stats": trader.stats,
    "trader-patterns": trader.patterns,
    "trader-sizing": trader.sizing,
    "trader-costs": trader.costs,
    "trader-regimes": trader.regime_screen,
    "tax": tax.screen,
    "markets-rates": markets.rates,
    "markets-quotes": markets.quotes,
    "markets-calendar": markets.calendar,
}

# Dolny pasek: maks. 5 pozycji. "more" to menu z pozostałymi działami.
# "noperiod": ekran (albo cały dział) nie zależy od okresu analizy - bez paska „Okres”.
# Kolejność pod-zakładek: od najczęściej oglądanych do narzędzi.
NAV = [
    {"id": "overview", "label": "Przegląd", "icon": "home", "screens": [{"id": "overview", "label": "Przegląd"}]},
    {"id": "companies", "label": "Pozycje", "icon": "companies", "screens": [
        {"id": "companies-list", "label": "Lista", "noperiod": True},
        {"id": "companies", "label": "Wycena spółek", "noperiod": True}]},
    {"id": "perf", "label": "Wyniki", "icon": "chart", "screens": [
        {"id": "perf-summary", "label": "Statystyki"}, {"id": "perf-benchmarks", "label": "Benchmarki"},
        {"id": "perf-periods", "label": "Miesiące"}, {"id": "perf-attribution", "label": "Atrybucja"}]},
    {"id": "risk", "label": "Ryzyko", "icon": "shield", "screens": [
        {"id": "risk-var", "label": "VaR"}, {"id": "risk-exposure", "label": "Ekspozycje"},
        {"id": "fund-longshort", "label": "Long / short"}, {"id": "risk-correlation", "label": "Korelacje"},
        {"id": "risk-factors", "label": "Czynniki"}, {"id": "fund-limits", "label": "Limity"},
        {"id": "risk-cfd", "label": "CFD", "needs": "cfd"}]},      # "needs": ekran widoczny tylko, gdy w portfelu są CFD
]
# "group": nagłówek w menu „Więcej” (na komputerze - w bocznym panelu)
MORE = [
    {"id": "markets", "group": "Rynek", "label": "Rynki", "noperiod": True,
     "hint": "Rentowności i krzywa USA, indeksy, VIX, waluty, kalendarz danych makro",
     "screens": [{"id": "markets-rates", "label": "Stopy USA"}, {"id": "markets-quotes", "label": "Notowania"},
                 {"id": "markets-calendar", "label": "Kalendarz"}]},
    {"id": "sim", "group": "Analizy", "label": "Symulacje",
     "hint": "Scenariusze stresowe, Monte Carlo, symulator transakcji, optymalizacja", "screens": [
        {"id": "sim-stress", "label": "Stres"}, {"id": "sim-montecarlo", "label": "Monte Carlo"},
        {"id": "sim-whatif", "label": "Co jeśli"}, {"id": "sim-optimize", "label": "Optymalizacja"}]},
    {"id": "trader", "group": "Analizy", "label": "Trader",
     "hint": "Skuteczność, wzorce zachowań, koszty, reżimy rynku, wielkość pozycji", "screens": [
        {"id": "trader-stats", "label": "Statystyki"}, {"id": "trader-patterns", "label": "Wzorce"},
        {"id": "trader-costs", "label": "Koszty"}, {"id": "trader-regimes", "label": "Reżimy"},
        {"id": "trader-sizing", "label": "Wielkość pozycji"}]},
    {"id": "tax", "group": "Analizy", "label": "Podatki", "hint": "Zestawienie do PIT-38: FIFO, kursy NBP, dywidendy zagraniczne",
     "screens": [{"id": "tax", "label": "PIT-38", "noperiod": True}]},
    {"id": "ai", "group": "Analizy", "label": "AI", "hint": "Memo ryzyka i pytania o portfel (model Claude)",
     "screens": [{"id": "ai", "label": "AI"}]},
    {"id": "import", "group": "Konto", "label": "Dane i ustawienia",
     "hint": "Wgraj eksport z xStation 5, popraw tickery, benchmark i stopa wolna od ryzyka",
     "screens": [{"id": "import", "label": "Import", "noperiod": True}, {"id": "mapping", "label": "Tickery", "noperiod": True},
                 {"id": "data", "label": "Wczytane dane"}, {"id": "settings", "label": "Ustawienia", "noperiod": True}]},
]
CLIENT_SCREENS = {"import", "mapping", "settings", "ai"}   # rysowane w całości po stronie telefonu (web/js/client.js)


def nav_config(ai_enabled: bool = False) -> dict:
    return {"tabs": NAV, "more": [m for m in MORE if m.get("id") != "ai" or ai_enabled]}
