"""Optymalizacja wag - wyłącznie jako analiza porównawcza (nie rekomendacja).

Własna implementacja czterech klasycznych portfeli na aktywach, które użytkownik już ma:
minimalna wariancja, maksymalny Sharpe, parytet ryzyka i HRP (Hierarchical Risk Parity, López de Prado).
Wszystkie są długie (wagi >= 0), sumują się do 100% i respektują limit wagi na pozycję.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import optimize
from scipy.cluster.hierarchy import leaves_list, linkage
from scipy.spatial.distance import squareform

TRADING_DAYS = 252


def shrunk_cov(returns: pd.DataFrame, shrink: float = 0.2) -> pd.DataFrame:
    """Kowariancja roczna ściągnięta w stronę macierzy diagonalnej (odporniejsza na szum przy krótkiej historii)."""
    c = np.atleast_2d(np.cov(returns.values, rowvar=False, ddof=1)) * TRADING_DAYS
    c = (1 - shrink) * c + shrink * np.diag(np.diag(c))
    return pd.DataFrame(c, index=returns.columns, columns=returns.columns)


def shrunk_means(returns: pd.DataFrame, shrink: float = 0.5) -> pd.Series:
    """Średnie zwroty roczne ściągnięte do średniej wszystkich aktywów - historia to słaby prognostyk zwrotów."""
    mu = returns.mean() * TRADING_DAYS
    return (1 - shrink) * mu + shrink * float(mu.mean())


def _bounds(n: int, max_weight: float) -> list[tuple[float, float]]:
    cap = max(max_weight, 1.0 / n + 1e-9)       # limit musi dać się spełnić
    return [(0.0, cap)] * n


def _solve(objective, n: int, max_weight: float, x0: np.ndarray | None = None) -> np.ndarray:
    x0 = np.full(n, 1.0 / n) if x0 is None else x0
    res = optimize.minimize(objective, x0, method="SLSQP", bounds=_bounds(n, max_weight),
                            constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
                            options={"maxiter": 500, "ftol": 1e-12})
    w = np.clip(res.x, 0.0, None)
    return w / w.sum()


def min_variance(cov: pd.DataFrame, max_weight: float = 1.0) -> pd.Series:
    c = cov.values
    return pd.Series(_solve(lambda w: w @ c @ w, len(c), max_weight), index=cov.index)


def max_sharpe(mu: pd.Series, cov: pd.DataFrame, rf: float = 0.0, max_weight: float = 1.0) -> pd.Series:
    c, m = cov.values, mu.reindex(cov.index).values - rf
    if not np.any(m > 0):                       # żadne aktywo nie bije stopy wolnej od ryzyka
        return min_variance(cov, max_weight)

    def neg_sharpe(w):
        vol = np.sqrt(max(w @ c @ w, 1e-18))
        return -(w @ m) / vol

    best, best_val = None, np.inf
    starts = [np.full(len(c), 1.0 / len(c)), min_variance(cov, max_weight).values]
    for x0 in starts:
        w = _solve(neg_sharpe, len(c), max_weight, x0)
        val = neg_sharpe(w)
        if val < best_val:
            best, best_val = w, val
    return pd.Series(best, index=cov.index)


def risk_parity(cov: pd.DataFrame, max_weight: float = 1.0) -> pd.Series:
    """Równy udział każdej pozycji w ryzyku. Rozwiązanie wypukłe: min 0.5 w'Cw - (1/n) sum ln(w)."""
    c = cov.values
    n = len(c)
    scale = float(np.sqrt(np.mean(np.diag(c)))) or 1.0

    def f(y):
        return 0.5 * y @ c @ y / scale ** 2 - np.log(y).sum() / n

    def grad(y):
        return c @ y / scale ** 2 - 1.0 / (n * y)

    res = optimize.minimize(f, np.ones(n), jac=grad, method="L-BFGS-B", bounds=[(1e-8, None)] * n,
                            options={"maxiter": 2000, "ftol": 1e-15, "gtol": 1e-10})
    w = res.x / res.x.sum()
    return _cap(pd.Series(w, index=cov.index), max_weight)


def _cap(w: pd.Series, max_weight: float) -> pd.Series:
    """Przycina wagi do limitu i rozdaje nadwyżkę proporcjonalnie pozostałym."""
    cap = max(max_weight, 1.0 / len(w) + 1e-9)
    w = w.copy()
    for _ in range(len(w)):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = float((w[over] - cap).sum())
        w[over] = cap
        free = ~over & (w < cap)
        if not free.any() or w[free].sum() <= 0:
            break
        w[free] += excess * w[free] / w[free].sum()
    return w / w.sum()


def hrp(cov: pd.DataFrame, max_weight: float = 1.0) -> pd.Series:
    """Hierarchical Risk Parity: klasteryzacja po korelacjach + rekurencyjny podział ryzyka między gałęzie."""
    n = len(cov)
    if n == 1:
        return pd.Series([1.0], index=cov.index)
    sd = np.sqrt(np.diag(cov.values))
    corr = cov.values / np.outer(sd, sd)
    dist = np.sqrt(np.clip(0.5 * (1.0 - corr), 0.0, 1.0))
    np.fill_diagonal(dist, 0.0)
    order = leaves_list(linkage(squareform(dist, checks=False), method="single")).tolist()
    w = np.ones(n)

    def cluster_var(items):
        sub = cov.values[np.ix_(items, items)]
        ivp = 1.0 / np.diag(sub)
        ivp /= ivp.sum()
        return float(ivp @ sub @ ivp)

    stack = [order]
    while stack:
        items = stack.pop()
        if len(items) < 2:
            continue
        half = len(items) // 2
        left, right = items[:half], items[half:]
        vl, vr = cluster_var(left), cluster_var(right)
        alpha = 1.0 - vl / (vl + vr)
        w[left] *= alpha
        w[right] *= 1.0 - alpha
        stack += [left, right]
    return _cap(pd.Series(w / w.sum(), index=cov.index), max_weight)


def portfolio_stats(w: pd.Series, mu: pd.Series, cov: pd.DataFrame, rf: float = 0.0) -> dict:
    x = w.reindex(cov.index).fillna(0.0).values
    vol = float(np.sqrt(max(x @ cov.values @ x, 0.0)))
    ret = float(x @ mu.reindex(cov.index).values)
    rc = x * (cov.values @ x)
    share = rc / rc.sum() if rc.sum() > 0 else rc
    return {"return": ret, "volatility": vol, "sharpe": (ret - rf) / vol if vol > 0 else np.nan,
            "max_weight": float(x.max()), "effective_n": float(1.0 / (x ** 2).sum()) if (x ** 2).sum() > 0 else np.nan,
            "max_risk_share": float(share.max()) if len(share) else np.nan}


METHODS = {"current": "Obecny portfel", "min_variance": "Minimalna wariancja", "max_sharpe": "Maksymalny Sharpe",
           "risk_parity": "Parytet ryzyka", "hrp": "HRP"}


def compare(returns: pd.DataFrame, current: pd.Series, rf: float = 0.0, max_weight: float = 0.35) -> dict:
    """Wagi i statystyki obecnego portfela oraz czterech portfeli modelowych na tych samych aktywach."""
    cols = [c for c in current.index if c in returns.columns]
    r = returns[cols].dropna(how="all").fillna(0.0)
    cur = current.reindex(cols).clip(lower=0.0)
    cur = cur / cur.sum()
    cov, mu = shrunk_cov(r), shrunk_means(r)
    weights = pd.DataFrame({
        "current": cur, "min_variance": min_variance(cov, max_weight), "max_sharpe": max_sharpe(mu, cov, rf, max_weight),
        "risk_parity": risk_parity(cov, max_weight), "hrp": hrp(cov, max_weight)})
    stats = pd.DataFrame({k: portfolio_stats(weights[k], mu, cov, rf) for k in weights.columns}).T
    stats["turnover"] = [float((weights[k] - cur).abs().sum() / 2.0) for k in weights.columns]
    return {"weights": weights, "stats": stats, "mu": mu, "cov": cov}
