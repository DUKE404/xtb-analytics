"""Stress testy: scenariusze historyczne (przybliżone szoki czynników) i scenariusze własne.

Każda pozycja jest przypisana do jednego czynnika akcyjnego/surowcowego i - dla akcji i ETF-ów w walucie
obcej - do jednego czynnika walutowego. Wynik pozycji w scenariuszu:
  akcje/ETF:  wartość x [(1 + beta * szok_czynnika) * (1 + szok_waluty) - 1]
  CFD:        nominał ze znakiem x beta * szok_czynnika
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FACTORS = {
    "SP500": "S&P 500", "NDX": "Nasdaq 100", "WIG20": "WIG20", "EUROPE": "Akcje europejskie (DAX)",
    "WORLD": "Akcje globalne", "GOLD": "Złoto", "OIL": "Ropa", "USDPLN": "USD/PLN", "EURPLN": "EUR/PLN",
}
EQUITY_FACTORS = ["SP500", "NDX", "WIG20", "EUROPE", "WORLD", "GOLD", "OIL"]
FX_FACTORS = ["USDPLN", "EURPLN"]
# czynnik -> klucz serii w danych rynkowych (do szacowania bet)
PROXY = {"SP500": "SP500", "NDX": "NDX", "WIG20": "WIG20", "EUROPE": "DAX", "WORLD": "MSCIW", "GOLD": "GOLD", "OIL": "OIL"}

# Przybliżone ruchy rynków od szczytu do dołka w danym epizodzie (źródło: publicznie znane poziomy indeksów
# i kursów; wartości zaokrąglone). To punkt wyjścia do rozmowy o ryzyku, nie precyzyjna rekonstrukcja.
HISTORICAL = {
    "Kryzys finansowy 2008": {
        "period": "10.2007 – 03.2009",
        "shocks": {"SP500": -0.57, "NDX": -0.53, "WIG20": -0.66, "EUROPE": -0.54, "WORLD": -0.57,
                   "GOLD": 0.24, "OIL": -0.44, "USDPLN": 0.35, "EURPLN": 0.25}},
    "COVID, marzec 2020": {
        "period": "19.02 – 23.03.2020",
        "shocks": {"SP500": -0.34, "NDX": -0.28, "WIG20": -0.36, "EUROPE": -0.39, "WORLD": -0.34,
                   "GOLD": -0.07, "OIL": -0.54, "USDPLN": 0.09, "EURPLN": 0.07}},
    "Podwyżki stóp 2022": {
        "period": "01 – 10.2022",
        "shocks": {"SP500": -0.25, "NDX": -0.35, "WIG20": -0.40, "EUROPE": -0.24, "WORLD": -0.26,
                   "GOLD": -0.07, "OIL": 0.17, "USDPLN": 0.24, "EURPLN": 0.06}},
}

_CFD_FACTOR = {"US500": "SP500", "US30": "SP500", "US2000": "SP500", "US100": "NDX", "DE40": "EUROPE", "EU50": "EUROPE",
               "FRA40": "EUROPE", "UK100": "EUROPE", "W20": "WIG20", "JP225": "WORLD", "GOLD": "GOLD", "SILVER": "GOLD",
               "OIL": "OIL", "OIL.WTI": "OIL", "NATGAS": "OIL"}
_EUROPE = {"Niemcy", "Francja", "Holandia", "Hiszpania", "Włochy", "Belgia", "Portugalia", "Finlandia", "Szwajcaria",
           "Szwecja", "Dania", "Norwegia", "Czechy", "Wielka Brytania", "Strefa euro"}
_TECH = {"Technologia", "Telekomunikacja"}


def equity_factor(symbol: str, row) -> str:
    """Czynnik akcyjny/surowcowy pozycji; pusty napis = pozycja poza modelem."""
    base = symbol.split("_")[0]
    if base in _CFD_FACTOR:
        return _CFD_FACTOR[base]
    sector, country, cls = str(row.get("sector", "")), str(row.get("country", "")), str(row.get("asset_class", "STOCK"))
    if cls == "CFD" and sector in ("Waluty", "Kryptowaluty"):
        return ""                         # CFD walutowe i krypto: poza modelem (pokazane jako 0 z adnotacją)
    low = sector.lower()
    if "złot" in low:
        return "GOLD"
    if "etf" in low:
        if "usa" in low:
            return "SP500"
        if "polsk" in low:
            return "WIG20"
        return "WORLD"
    if country == "Polska":
        return "WIG20"
    if country == "USA":
        return "NDX" if sector in _TECH else "SP500"
    if country in _EUROPE:
        return "EUROPE"
    return "WORLD"


def fx_factor(row) -> str:
    """Czynnik walutowy pozycji; pusty napis = brak ryzyka walutowego."""
    if str(row.get("asset_class", "STOCK")) == "CFD":
        return ""                         # nominał CFD nie niesie ryzyka walutowego
    cur = str(row.get("currency", "PLN"))
    if cur == "PLN":
        return ""
    return "USDPLN" if cur == "USD" else "EURPLN"   # pozostałe waluty europejskie przybliżamy euro


def factor_map(meta: pd.DataFrame, symbols) -> pd.DataFrame:
    rows = {s: {"eq_factor": equity_factor(s, meta.loc[s]) if s in meta.index else "WORLD",
                "fx_factor": fx_factor(meta.loc[s]) if s in meta.index else ""} for s in symbols}
    return pd.DataFrame.from_dict(rows, orient="index", columns=["eq_factor", "fx_factor"])


def estimate_betas(asset_local_returns: pd.DataFrame, proxy_returns: pd.DataFrame, fmap: pd.DataFrame,
                   lookback: int = 504, lo: float = 0.3, hi: float = 2.5, min_obs: int = 60) -> pd.Series:
    """Beta każdej pozycji względem jej czynnika (zwroty w walucie własnej). Brak danych -> 1."""
    out = {}
    for sym, row in fmap.iterrows():
        beta = 1.0
        key = PROXY.get(row["eq_factor"])
        if sym in asset_local_returns.columns and key in proxy_returns.columns:
            df = pd.concat([asset_local_returns[sym], proxy_returns[key]], axis=1).dropna().tail(lookback)
            if len(df) >= min_obs and df.iloc[:, 1].var() > 0:
                beta = float(np.cov(df.iloc[:, 0], df.iloc[:, 1], ddof=1)[0, 1] / df.iloc[:, 1].var(ddof=1))
                beta = float(np.clip(beta, lo, hi))
        out[sym] = beta
    return pd.Series(out, dtype=float)


def apply_scenario(exposure: pd.Series, meta: pd.DataFrame, shocks: dict, betas: pd.Series | None = None) -> pd.DataFrame:
    """Wynik każdej pozycji w scenariuszu. `shocks`: czynnik -> zmiana (ułamek, np. -0.25)."""
    fmap = factor_map(meta, exposure.index)
    rows = []
    for sym, e in exposure.items():
        eqf, fxf = fmap.at[sym, "eq_factor"], fmap.at[sym, "fx_factor"]
        beta = float(betas.get(sym, 1.0)) if betas is not None else 1.0
        eq = max(beta * float(shocks.get(eqf, 0.0)), -1.0) if eqf else 0.0
        fx = float(shocks.get(fxf, 0.0)) if fxf else 0.0
        is_cfd = sym in meta.index and meta.at[sym, "asset_class"] == "CFD"
        change = eq if is_cfd else (1.0 + eq) * (1.0 + fx) - 1.0
        rows.append({"symbol": sym, "exposure": float(e), "eq_factor": eqf, "fx_factor": fxf, "beta": beta,
                     "eq_shock": eq, "fx_shock": fx, "change": change, "pnl": float(e) * change})
    return pd.DataFrame(rows).set_index("symbol") if rows else pd.DataFrame(
        columns=["exposure", "eq_factor", "fx_factor", "beta", "eq_shock", "fx_shock", "change", "pnl"])


def run_scenarios(exposure: pd.Series, meta: pd.DataFrame, equity: float, scenarios: dict,
                  betas: pd.Series | None = None) -> pd.DataFrame:
    """scenarios: nazwa -> {czynnik: szok}. Zwraca wynik w PLN i jako % kapitału."""
    rows = {}
    for name, shocks in scenarios.items():
        pnl = float(apply_scenario(exposure, meta, shocks, betas)["pnl"].sum())
        rows[name] = {"pnl": pnl, "pct": pnl / equity if equity else np.nan}
    return pd.DataFrame.from_dict(rows, orient="index", columns=["pnl", "pct"])
