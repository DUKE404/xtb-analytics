"""Miary używane w funduszach long/short: ekspozycja brutto i netto, beta portfela, przechwytywanie rynku,
epizody obsunięć, statystyki kroczące, limity ryzyka. Czysta arytmetyka na zwrotach i ekspozycjach."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def monthly(returns: pd.Series) -> pd.Series:
    r = returns.dropna()
    return (1.0 + r).groupby([r.index.year, r.index.month]).prod() - 1.0 if len(r) else pd.Series(dtype=float)


def capture_ratios(returns: pd.Series, bench: pd.Series) -> dict:
    """Przechwytywanie rynku na miesiącach: średni wynik portfela w miesiącach wzrostu (spadku) benchmarku
    podzielony przez średni wynik benchmarku w tych miesiącach. 1,0 = tak samo jak rynek."""
    df = pd.DataFrame({"p": monthly(returns), "b": monthly(bench)}).dropna()
    up, down = df[df["b"] > 0], df[df["b"] < 0]
    ratio = lambda d: float(d["p"].mean() / d["b"].mean()) if len(d) and abs(d["b"].mean()) > 1e-12 else float("nan")
    return {"up": ratio(up), "down": ratio(down), "up_months": int(len(up)), "down_months": int(len(down))}


def month_stats(returns: pd.Series) -> dict:
    m = monthly(returns)
    if not len(m):
        return {"positive": np.nan, "best": np.nan, "worst": np.nan, "avg_up": np.nan, "avg_down": np.nan, "n": 0}
    return {"positive": float((m > 0).mean()), "best": float(m.max()), "worst": float(m.min()),
            "avg_up": float(m[m > 0].mean()) if (m > 0).any() else np.nan,
            "avg_down": float(m[m < 0].mean()) if (m < 0).any() else np.nan, "n": int(len(m))}


def tail_ratio(returns: pd.Series) -> float:
    """95. percentyl dziennych zwrotów do wartości bezwzględnej 5. percentyla: >1 = dobre dni większe niż złe."""
    r = returns.dropna()
    if len(r) < 20:
        return float("nan")
    lo = abs(float(np.percentile(r, 5)))
    return float(np.percentile(r, 95)) / lo if lo > 1e-12 else float("nan")


def omega(returns: pd.Series, threshold: float = 0.0) -> float:
    """Suma zysków ponad próg do sumy strat poniżej progu."""
    r = returns.dropna() - threshold
    loss = float(-r[r < 0].sum())
    return float(r[r > 0].sum()) / loss if loss > 1e-12 else float("nan")


def drawdown_episodes(returns: pd.Series, top: int = 5) -> pd.DataFrame:
    """Największe obsunięcia: szczyt, dołek, odrobienie (albo brak), głębokość, długość w dniach kalendarzowych."""
    cols = ["peak", "trough", "recovery", "depth", "days_down", "days_total", "ongoing"]
    r = returns.dropna()
    if not len(r):
        return pd.DataFrame(columns=cols)
    idx = (1.0 + r).cumprod()
    peak = idx.cummax()
    under = idx < peak - 1e-12
    rows, start = [], None
    dates = idx.index
    for i, flag in enumerate(under.values):
        if flag and start is None:
            start = i
        if start is not None and (not flag or i == len(dates) - 1):
            end = i if not flag else i + 1
            seg = idx.iloc[start:end]
            top_date = dates[start - 1] if start > 0 else dates[start]
            trough = seg.idxmin()
            ongoing = bool(flag)
            rec = None if ongoing else dates[i]
            rows.append({"peak": top_date, "trough": trough, "recovery": rec,
                         "depth": float(seg.min() / peak.iloc[start] - 1.0), "days_down": int((trough - top_date).days),
                         "days_total": int(((dates[-1] if ongoing else rec) - top_date).days), "ongoing": ongoing})
            start = None
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values("depth").head(top).reset_index(drop=True)


def rolling_stats(returns: pd.Series, bench: pd.Series | None = None, window: int = 63, rf_annual: float = 0.0) -> pd.DataFrame:
    """Kroczące (okno w dniach sesyjnych): zmienność roczna, Sharpe, beta do benchmarku."""
    r = returns.dropna()
    rf = (1.0 + rf_annual) ** (1.0 / TRADING_DAYS) - 1.0
    sd = r.rolling(window).std(ddof=1)
    out = pd.DataFrame({"vol": sd * np.sqrt(TRADING_DAYS),
                        "sharpe": (r - rf).rolling(window).mean() / sd.where(sd > 1e-12) * np.sqrt(TRADING_DAYS)})
    if bench is not None:
        b = bench.reindex(r.index)
        var = b.rolling(window).var(ddof=1)
        out["beta"] = r.rolling(window).cov(b) / var.where(var > 1e-14)
    return out.dropna(how="all")


def asset_betas(asset_returns: pd.DataFrame, bench: pd.Series, min_obs: int = 40) -> pd.Series:
    """Beta każdego instrumentu do benchmarku (kowariancja / wariancja na wspólnych dniach)."""
    out = {}
    for c in asset_returns.columns:
        df = pd.DataFrame({"a": asset_returns[c], "b": bench}).dropna()
        v = float(df["b"].var(ddof=1)) if len(df) >= min_obs else 0.0
        out[c] = float(df["a"].cov(df["b"]) / v) if v > 1e-14 else np.nan
    return pd.Series(out, dtype=float)


def long_short(exposure: pd.Series, equity: float, betas: pd.Series | None = None) -> dict:
    """Ekspozycja długa, krótka, brutto i netto jako część kapitału (NAV) oraz beta portfela.

    Beta portfela = suma (ekspozycja x beta instrumentu) / NAV; pozycje bez bety liczone z betą 1.
    """
    e = exposure[exposure.abs() > 1e-9]
    long_, short = float(e[e > 0].sum()), float(-e[e < 0].sum())
    nav = equity if equity > 0 else np.nan
    out = {"long": long_ / nav, "short": short / nav, "gross": (long_ + short) / nav, "net": (long_ - short) / nav,
           "n_long": int((e > 0).sum()), "n_short": int((e < 0).sum()), "cash": 1.0 - (long_ - short) / nav}
    if betas is not None:
        b = betas.reindex(e.index).fillna(1.0)
        out["beta_adjusted"] = float((e * b).sum()) / nav
    return out


def hedge_notional(beta_adjusted: float, equity: float, target_beta: float = 0.0) -> float:
    """Nominał krótkiej pozycji na benchmarku, który sprowadza betę portfela do `target_beta` (ujemny = dokupić)."""
    return (beta_adjusted - target_beta) * equity


def check_limits(rows: list[dict]) -> pd.DataFrame:
    """rows: {"name", "value", "limit", "kind": "max" | "min"}. Wykorzystanie limitu i status."""
    out = []
    for r in rows:
        v, lim = r["value"], r["limit"]
        if v is None or v != v or not lim:
            use, status = np.nan, "brak danych"
        else:
            use = abs(v) / abs(lim) if r.get("kind", "max") == "max" else (abs(lim) / abs(v) if abs(v) > 1e-12 else np.inf)
            status = "przekroczony" if use > 1.0 + 1e-12 else ("blisko" if use >= 0.8 else "w normie")
        out.append({**r, "usage": use, "status": status})
    return pd.DataFrame(out)
