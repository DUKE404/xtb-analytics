"""Monte Carlo przyszłości portfela: bootstrap blokowy (zachowuje skupiska zmienności z historii)
oraz model z grubymi ogonami (t-Studenta). Wynik: rozkład wartości w czasie, prawdopodobieństwo celu
i prawdopodobieństwo obsunięcia większego niż zadany próg.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

TRADING_DAYS = 252
DAYS_PER_MONTH = 21


def target_daily_mean(annual_return: float, sigma_daily: float) -> float:
    """Średnia arytmetyczna dzienna, przy której mediana wzrostu rocznego ~ `annual_return`."""
    return float(np.log1p(annual_return) / TRADING_DAYS + 0.5 * sigma_daily ** 2)


def bootstrap_index(n: int, horizon: int, n_paths: int, block: int = 20, seed: int = 42) -> np.ndarray:
    """Indeksy dni historii dla każdej ścieżki: całe bloki kolejnych dni (z zawijaniem), sklejone do `horizon`."""
    block = max(1, min(block, n))
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(horizon / block))
    starts = rng.integers(0, n, size=(n_paths, n_blocks), dtype=np.int32)
    idx = (starts[:, :, None] + np.arange(block, dtype=np.int32)[None, None, :]) % n
    return idx.reshape(n_paths, -1)[:, :horizon]


def block_bootstrap(returns: np.ndarray, horizon: int, n_paths: int, block: int = 20, seed: int = 42) -> np.ndarray:
    """Losuje z historii całe bloki kolejnych dni (z zawijaniem) i skleja je w ścieżki o długości `horizon`."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return np.zeros((n_paths, horizon))
    return r[bootstrap_index(len(r), horizon, n_paths, block, seed)]


def student_t_paths(mu_daily: float, sigma_daily: float, df: float, horizon: int, n_paths: int, seed: int = 42) -> np.ndarray:
    """Niezależne dzienne zwroty z rozkładu t o zadanej średniej i odchyleniu (df > 2)."""
    rng = np.random.default_rng(seed)
    df = max(float(df), 2.5)
    z = rng.standard_t(df, size=(n_paths, horizon)) / np.sqrt(df / (df - 2.0))
    return mu_daily + sigma_daily * z


def fit_df(returns: np.ndarray) -> float:
    """Stopnie swobody z kurtozy (metoda momentów), ograniczone do [4.5, 30]."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 30:
        return 6.0
    k = float(stats.kurtosis(r, fisher=True, bias=False))
    if not np.isfinite(k) or k <= 0.05:
        return 30.0
    return float(np.clip(4.0 + 6.0 / k, 4.5, 30.0))


def simulate(returns: pd.Series | np.ndarray, start_value: float, years: int = 10, n_paths: int = 2000,
             method: str = "bootstrap", annual_return: float | None = None, monthly_contribution: float = 0.0,
             block: int = 20, seed: int = 42) -> dict:
    """Symuluje wartość portfela co miesiąc przez `years` lat.

    method: 'bootstrap' (bloki z historii) albo 't' (grube ogony).
    annual_return: jeśli podany, zastępuje średnią z historii (historia 2 lat to bardzo słaby szacunek średniej).
    Zwraca ścieżki w siatce miesięcznej (`values`: n_paths x (miesiące+1)), wpłacony kapitał i najgłębsze
    obsunięcie każdej ścieżki (liczone na stopie zwrotu, bez wpływu dopłat).
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    horizon = int(years * TRADING_DAYS)
    sigma = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    hist_mean = float(r.mean()) if len(r) else 0.0
    mu = target_daily_mean(annual_return, sigma) if annual_return is not None else hist_mean
    months = horizon // DAYS_PER_MONTH
    # zwroty losujemy miesiąc po miesiącu - pełna macierz dni x ścieżki zajęłaby kilkadziesiąt MB
    if method == "t" or len(r) == 0:
        df = fit_df(r)
        rng = np.random.default_rng(seed)
        scale = sigma / np.sqrt(df / (df - 2.0))

        def month_returns(m: int) -> np.ndarray:
            return mu + scale * rng.standard_t(df, size=(n_paths, DAYS_PER_MONTH))
    else:
        idx = bootstrap_index(len(r), months * DAYS_PER_MONTH, n_paths, block, seed)
        shift = mu - hist_mean

        def month_returns(m: int) -> np.ndarray:
            return r[idx[:, m * DAYS_PER_MONTH:(m + 1) * DAYS_PER_MONTH]] + shift

    values = np.empty((n_paths, months + 1))
    values[:, 0] = start_value
    v = np.full(n_paths, float(start_value))
    growth = np.ones(n_paths)
    peak = np.ones(n_paths)
    max_dd = np.zeros(n_paths)
    dd_by_month = np.zeros((n_paths, months + 1))
    for m in range(months):
        if m > 0 and monthly_contribution:
            v = v + monthly_contribution
        chunk = np.maximum(month_returns(m), -0.99)
        for d in range(DAYS_PER_MONTH):
            step = 1.0 + chunk[:, d]
            v = v * step
            growth = growth * step
            peak = np.maximum(peak, growth)
            max_dd = np.minimum(max_dd, growth / peak - 1.0)
        values[:, m + 1] = v
        dd_by_month[:, m + 1] = max_dd
    contributed = start_value + monthly_contribution * np.maximum(np.arange(months + 1) - 1, 0)
    contributed[0] = start_value
    return {"values": values, "months": months, "contributed": contributed, "max_drawdown": max_dd,
            "max_drawdown_by_month": dd_by_month, "mu_daily": mu, "sigma_daily": sigma, "method": method,
            "hist_annual_return": float((1 + hist_mean) ** TRADING_DAYS - 1) if len(r) else np.nan}


def percentiles(sim: dict, qs=(5, 25, 50, 75, 95), upto_month: int | None = None) -> pd.DataFrame:
    """Percentyle wartości w kolejnych miesiącach (indeks = numer miesiąca)."""
    v = sim["values"] if upto_month is None else sim["values"][:, :upto_month + 1]
    return pd.DataFrame(np.percentile(v, qs, axis=0).T, columns=[f"p{q}" for q in qs])


def at_horizon(sim: dict, years: float) -> np.ndarray:
    m = min(int(round(years * 12)), sim["months"])
    return sim["values"][:, m]


def prob_target(sim: dict, target: float, years: float) -> float:
    return float((at_horizon(sim, years) >= target).mean())


def prob_drawdown(sim: dict, threshold: float, years: float | None = None) -> float:
    """Odsetek ścieżek, które choć raz spadły od szczytu o więcej niż `threshold` (np. 0.2) do końca horyzontu."""
    if years is None:
        dd = sim["max_drawdown"]
    else:
        dd = sim["max_drawdown_by_month"][:, min(int(round(years * 12)), sim["months"])]
    return float((dd <= -abs(threshold)).mean())


def prob_loss(sim: dict, years: float) -> float:
    """Odsetek ścieżek, w których na koniec jest mniej niż wpłacono."""
    m = min(int(round(years * 12)), sim["months"])
    return float((sim["values"][:, m] < sim["contributed"][m]).mean())


def horizon_table(sim: dict, horizons=(1, 5, 10)) -> pd.DataFrame:
    rows = []
    for h in horizons:
        if h * 12 > sim["months"]:
            continue
        v = at_horizon(sim, h)
        m = h * 12
        rows.append({"horizon": h, "p5": np.percentile(v, 5), "p25": np.percentile(v, 25), "p50": np.percentile(v, 50),
                     "p75": np.percentile(v, 75), "p95": np.percentile(v, 95), "contributed": sim["contributed"][m],
                     "prob_loss": prob_loss(sim, h)})
    return pd.DataFrame(rows)
