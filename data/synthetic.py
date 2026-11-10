"""Syntetyczny, deterministyczny rynek do trybu demo i testów (działa offline).

Notowania powstają z modelu czynnikowego: zwrot aktywa = RF + beta * rynek regionu
+ ładunki na czynniki (SMB, HML, RMW, CMA, MOM) + szum o grubych ogonach. Rynek ma
dwa reżimy zmienności (spokojny / stres), dzięki czemu demo sensownie pokazuje
detekcję reżimów, regresję czynnikową i stress testy. To NIE są prawdziwe notowania.
"""
from __future__ import annotations

import zlib

import numpy as np
import pandas as pd

FACTOR_COLS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM", "RF"]

# symbol: (cena startowa, region, beta, ładunki czynnikowe, zmienność specyficzna, śr. wolumen, alfa roczna)
ASSET_SPECS: dict[str, tuple] = {
    "PKN.PL": (62.0, "PL", 1.00, {"HML": 0.3}, 0.20, 2.5e6, 0.00),
    "PKO.PL": (38.0, "PL", 1.20, {"HML": 0.4}, 0.18, 3.0e6, 0.03),
    "PZU.PL": (44.0, "PL", 0.90, {"HML": 0.3, "RMW": 0.2}, 0.15, 1.5e6, 0.02),
    "KGH.PL": (115.0, "PL", 1.30, {"HML": 0.2}, 0.25, 6.0e5, 0.00),
    "CDR.PL": (110.0, "PL", 0.90, {"HML": -0.5, "MOM": 0.2}, 0.33, 4.0e5, 0.02),
    "DNP.PL": (400.0, "PL", 0.70, {"RMW": 0.3}, 0.26, 1.5e5, 0.00),
    "ALE.PL": (30.0, "PL", 1.00, {"HML": -0.3}, 0.28, 3.0e6, 0.00),
    "AAPL.US": (175.0, "US", 1.15, {"HML": -0.4, "RMW": 0.3, "SMB": -0.2}, 0.17, 5.5e7, 0.02),
    "MSFT.US": (330.0, "US", 1.05, {"HML": -0.5, "RMW": 0.3, "SMB": -0.2}, 0.15, 2.2e7, 0.03),
    "NVDA.US": (45.0, "US", 1.70, {"HML": -0.8, "MOM": 0.5, "SMB": -0.1}, 0.35, 3.0e8, 0.12),
    "AMZN.US": (130.0, "US", 1.25, {"HML": -0.5, "CMA": -0.3}, 0.22, 4.5e7, 0.03),
    "KO.US": (55.0, "US", 0.55, {"HML": 0.2, "RMW": 0.3, "CMA": 0.3}, 0.12, 1.4e7, 0.00),
    "JNJ.US": (155.0, "US", 0.50, {"HML": 0.2, "RMW": 0.3, "CMA": 0.3}, 0.13, 7.0e6, 0.00),
    "XOM.US": (105.0, "US", 0.80, {"HML": 0.6, "CMA": 0.3}, 0.20, 1.6e7, 0.00),
    "CSPX.UK": (470.0, "US", 1.00, {}, 0.01, 1.5e5, 0.00),
    "EUNL.DE": (78.0, "WORLD_EUR", 1.00, {}, 0.01, 6.0e5, 0.00),
    "VWCE.DE": (105.0, "WORLD_EUR", 0.98, {}, 0.015, 4.0e5, 0.00),
    "ETFBW20TR.PL": (60.0, "PL", 1.05, {}, 0.03, 4.0e4, 0.03),
    "US500": (4300.0, "US", 1.00, {}, 0.0, 0.0, -0.013),
    "US100": (15000.0, "US", 1.20, {"HML": -0.4}, 0.05, 0.0, -0.008),
    "DE40": (15200.0, "EU", 1.00, {}, 0.03, 0.0, 0.0),
    "W20": (2150.0, "PL", 1.05, {}, 0.03, 0.0, -0.02),
    "GOLD": (1950.0, "NONE", 0.05, {}, 0.14, 0.0, 0.07),
}

# benchmark: (symbol bazowy lub None, cena startowa, region, beta, zmienność specyficzna, alfa, waluta)
BENCHMARK_SPECS = {
    "WIG20": ("W20", None, None, None, None, None, "PLN"),
    "WIG": (None, 68000.0, "PL", 0.95, 0.02, 0.03, "PLN"),
    "SP500": ("US500", None, None, None, None, None, "USD"),
    "NDX": ("US100", None, None, None, None, None, "USD"),
    "DAX": ("DE40", None, None, None, None, None, "EUR"),
    "MSCIW": (None, 130.0, "WORLD", 1.0, 0.01, 0.0, "USD"),
    "GOLD": ("GOLD", None, None, None, None, None, "USD"),
    "OIL": (None, 85.0, "NONE", 0.35, 0.30, -0.02, "USD"),
}

FX_START = {"USD": 4.20, "EUR": 4.50, "GBP": 5.15, "CHF": 4.65, "SEK": 0.385, "NOK": 0.385,
            "DKK": 0.603, "CZK": 0.179, "JPY": 0.028}


def _seed(*parts) -> int:
    return zlib.crc32("|".join(map(str, parts)).encode()) & 0x7FFFFFFF


class SyntheticMarket:
    def __init__(self, start="2023-10-02", end="2026-11-06", seed: int = 6):
        self.seed = seed
        self.dates = pd.bdate_range(start, end)
        n = len(self.dates)
        rng = np.random.default_rng(seed)

        # reżimy: 0 = spokojny, 1 = stres (łańcuch Markowa)
        state = np.zeros(n, dtype=int)
        for t in range(1, n):
            p_switch = 0.012 if state[t - 1] == 0 else 0.05
            state[t] = 1 - state[t - 1] if rng.random() < p_switch else state[t - 1]
        self.regime = pd.Series(state, index=self.dates, name="regime")
        sig = np.where(state == 0, 0.11, 0.27) / np.sqrt(252)
        mu = np.where(state == 0, 0.14, -0.24) / 252
        shock = rng.standard_t(6, n) / np.sqrt(1.5)
        mkt = mu + sig * shock

        def fac(vol, corr_mkt=0.0, mean=0.0):
            return mean / 252 + corr_mkt * mkt + rng.normal(0, vol / np.sqrt(252), n)

        f = pd.DataFrame(index=self.dates)
        f["Mkt-RF"] = mkt
        f["SMB"] = fac(0.08, 0.05, 0.01)
        f["HML"] = fac(0.09, -0.10, 0.02)
        f["RMW"] = fac(0.06, -0.05, 0.02)
        f["CMA"] = fac(0.06, -0.08, 0.01)
        f["MOM"] = fac(0.11, -0.10, 0.03)
        f["RF"] = 0.045 / 252
        self.factors = f

        self._regional = {
            "US": mkt,
            "PL": 0.55 * mkt + 0.04 / 252 + rng.normal(0, 0.15 / np.sqrt(252), n),
            "EU": 0.80 * mkt + rng.normal(0, 0.08 / np.sqrt(252), n),
            "NONE": mkt,
        }
        self._regional["WORLD"] = 0.72 * self._regional["US"] + 0.22 * self._regional["EU"] + 0.06 * self._regional["PL"]

        fx_ret = {
            "USD": -0.18 * mkt + rng.normal(0, 0.085 / np.sqrt(252), n),
            "EUR": -0.07 * mkt + rng.normal(0, 0.040 / np.sqrt(252), n),
            "GBP": -0.10 * mkt + rng.normal(0, 0.055 / np.sqrt(252), n),
        }
        self._fx_ret = fx_ret
        fx = pd.DataFrame(index=self.dates)
        fx["PLN"] = 1.0
        for cur, start_level in FX_START.items():
            r = fx_ret.get(cur)
            if r is None:
                r = -0.05 * mkt + np.random.default_rng(_seed(seed, "fx", cur)).normal(0, 0.06 / np.sqrt(252), n)
            path = np.cumprod(1 + r)
            fx[cur] = start_level * path / path[0]
        self.fx = fx.round(4)
        self._cache: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------ #
    def _path(self, key, p0, region, beta, loads, idio, alpha) -> np.ndarray:
        n = len(self.dates)
        rng = np.random.default_rng(_seed(self.seed, key))
        f = self.factors
        if region == "WORLD_EUR":
            # ETF na świat notowany w EUR: zwrot w USD przeliczony na EUR
            base = self._regional["WORLD"] * beta
            r = f["RF"].values + base + (self._fx_ret["USD"] - self._fx_ret["EUR"])
        else:
            r = f["RF"].values + beta * self._regional.get(region, self._regional["US"])
        for name, load in (loads or {}).items():
            r = r + load * f[name].values
        eps = rng.standard_t(5, n) / np.sqrt(5 / 3)
        r = r + alpha / 252 + idio / np.sqrt(252) * eps
        r[0] = 0.0
        return p0 * np.cumprod(1 + r)

    def asset(self, symbol: str, country: str | None = None) -> pd.DataFrame:
        """Notowania (close, volume) w walucie instrumentu."""
        sym = symbol.upper()
        if sym in self._cache:
            return self._cache[sym]
        if sym in ASSET_SPECS:
            p0, region, beta, loads, idio, adv, alpha = ASSET_SPECS[sym]
        else:
            g = np.random.default_rng(_seed(self.seed, "spec", sym))
            region = {"Polska": "PL", "USA": "US", "Niemcy": "EU", "Francja": "EU"}.get(country or "", "US")
            p0 = float(np.round(g.uniform(20, 300), 2))
            beta, idio, adv, alpha = g.uniform(0.6, 1.4), g.uniform(0.15, 0.35), g.uniform(2e5, 5e6), 0.0
            loads = {"HML": g.uniform(-0.4, 0.4), "SMB": g.uniform(-0.2, 0.5)}
        close = self._path(sym, p0, region, beta, loads, idio, alpha)
        g = np.random.default_rng(_seed(self.seed, "vol", sym))
        vol = adv * g.lognormal(0.0, 0.4, len(self.dates)) if adv else np.zeros(len(self.dates))
        digits = 2 if close.mean() < 5000 else 1
        df = pd.DataFrame({"close": np.round(close, digits), "volume": np.round(vol)}, index=self.dates)
        self._cache[sym] = df
        return df

    def benchmark(self, key: str) -> tuple[pd.Series, str]:
        base, p0, region, beta, idio, alpha, cur = BENCHMARK_SPECS[key]
        if base:
            return self.asset(base)["close"].rename(key), cur
        ck = f"__bench_{key}"
        if ck not in self._cache:
            close = self._path(ck, p0, region, beta, {}, idio, alpha)
            self._cache[ck] = pd.DataFrame({"close": np.round(close, 2)}, index=self.dates)
        return self._cache[ck]["close"].rename(key), cur


class SyntheticMarketData:
    """Dostawca danych w trybie demo - ten sam interfejs co LiveMarketData."""

    name = "demo (dane syntetyczne, offline)"
    is_synthetic = True

    def __init__(self, market: SyntheticMarket | None = None, end=None):
        self.market = market or SyntheticMarket(**({"end": end} if end is not None else {}))
        self.warnings: list[str] = []

    def price_history(self, row, start, end) -> pd.DataFrame:
        df = self.market.asset(row["symbol"], row.get("country"))
        return df.loc[pd.Timestamp(start):pd.Timestamp(end)]

    def fx_rates(self, currency: str, start, end) -> pd.Series:
        cur = currency.upper()
        fx = self.market.fx
        if cur not in fx.columns:
            raise KeyError(f"Brak kursu {cur} w danych demo")
        return fx[cur].loc[pd.Timestamp(start):pd.Timestamp(end)]

    def benchmark(self, key: str, start, end) -> tuple[pd.Series, str]:
        s, cur = self.market.benchmark(key)
        return s.loc[pd.Timestamp(start):pd.Timestamp(end)], cur

    def ff_factors(self, start, end) -> pd.DataFrame:
        return self.market.factors.loc[pd.Timestamp(start):pd.Timestamp(end)]
