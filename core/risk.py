"""Ryzyko bieżącego portfela: VaR/CVaR (historyczny, parametryczny, Monte Carlo), korelacje,
efektywna liczba zakładów, udział pozycji w ryzyku, ekspozycje, koncentracja, płynność.

Podejście "bieżące pozycje x historia rynku": dzisiejsze ekspozycje w PLN mnożymy przez historyczne
dzienne zwroty instrumentów (widziane z PLN), więc wynik opisuje portfel taki, jaki jest dziś.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

TRADING_DAYS = 252


# --------------------------------------------------------------------------- #
# Dane wejściowe
# --------------------------------------------------------------------------- #

def risk_inputs(exposure: pd.Series, asset_returns: pd.DataFrame, lookback: int = 504,
                min_obs: int = 60) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """Dopasowuje historię zwrotów do bieżących ekspozycji. Zwraca (zwroty, ekspozycje, ostrzeżenia)."""
    notes: list[str] = []
    exp = exposure[exposure.abs() > 1e-9]
    have = [s for s in exp.index if s in asset_returns.columns]
    missing = [s for s in exp.index if s not in have]
    if missing:
        notes.append("Pominięto w ryzyku (brak historii notowań): " + ", ".join(missing))
    r = asset_returns[have].tail(lookback)
    short = [s for s in have if r[s].notna().sum() < min_obs]
    if short:
        notes.append("Krótka historia (braki zastąpiono zerem): " + ", ".join(short))
    r = r.dropna(how="all").fillna(0.0)
    return r, exp.reindex(have), notes


def pnl_series(returns: pd.DataFrame, exposure: pd.Series) -> pd.Series:
    """Hipotetyczny dzienny wynik (PLN) dzisiejszego portfela w każdym dniu historii."""
    cols = [c for c in exposure.index if c in returns.columns]
    return (returns[cols] * exposure.reindex(cols).values).sum(axis=1)


# --------------------------------------------------------------------------- #
# VaR / CVaR - wszystkie funkcje zwracają stratę jako liczbę dodatnią (PLN)
# --------------------------------------------------------------------------- #

def _horizon_pnl(pnl: pd.Series, horizon: int) -> np.ndarray:
    x = np.asarray(pnl, dtype=float)
    if horizon <= 1:
        return x
    if len(x) >= 6 * horizon:
        return pd.Series(x).rolling(horizon).sum().dropna().values   # nakładające się okna
    return x * np.sqrt(horizon)


def var_historical(pnl: pd.Series, level: float = 0.95, horizon: int = 1) -> float:
    x = _horizon_pnl(pnl, horizon)
    return float(max(-np.quantile(x, 1.0 - level), 0.0)) if len(x) else np.nan


def cvar_historical(pnl: pd.Series, level: float = 0.95, horizon: int = 1) -> float:
    x = _horizon_pnl(pnl, horizon)
    if not len(x):
        return np.nan
    q = np.quantile(x, 1.0 - level)
    tail = x[x <= q]
    return float(max(-tail.mean(), 0.0)) if len(tail) else float(max(-q, 0.0))


def var_parametric(sigma_pln: float, level: float = 0.95, horizon: int = 1, mu_pln: float = 0.0) -> float:
    """VaR normalny: z * sigma * sqrt(h) - mu * h (domyślnie średnia = 0, co jest ostrożniejsze)."""
    z = stats.norm.ppf(level)
    return float(max(z * sigma_pln * np.sqrt(horizon) - mu_pln * horizon, 0.0))


def cvar_parametric(sigma_pln: float, level: float = 0.95, horizon: int = 1, mu_pln: float = 0.0) -> float:
    z = stats.norm.ppf(level)
    es = sigma_pln * np.sqrt(horizon) * stats.norm.pdf(z) / (1.0 - level)
    return float(max(es - mu_pln * horizon, 0.0))


def fit_t_df(pnl: pd.Series) -> float | None:
    """Stopnie swobody rozkładu t-Studenta dopasowane metodą momentów z kurtozy (None = ogony jak w normalnym)."""
    x = np.asarray(pnl, dtype=float)
    if len(x) < 30:
        return None
    k = float(stats.kurtosis(x, fisher=True, bias=False))
    if not np.isfinite(k) or k <= 0.05:
        return None
    return float(np.clip(4.0 + 6.0 / k, 4.5, 30.0))


def simulate_pnl(returns: pd.DataFrame, exposure: pd.Series, horizon: int = 1, n_sims: int = 20000,
                 seed: int = 42, df_t: float | None = None) -> np.ndarray:
    """Monte Carlo wyniku portfela (PLN).

    Losuje skorelowane dzienne zwroty wszystkich pozycji z kowariancji historycznej - z rozkładu normalnego
    albo wielowymiarowego t-Studenta o `df_t` stopniach swobody (grube ogony, wspólne dla całego rynku) -
    składa je przez `horizon` dni i przelicza na wynik dzisiejszych ekspozycji.
    """
    cols = [c for c in exposure.index if c in returns.columns]
    if not cols:
        return np.zeros(n_sims)
    cov = np.atleast_2d(np.cov(returns[cols].values, rowvar=False, ddof=1))
    w = exposure.reindex(cols).values.astype(float)
    lam, vec = np.linalg.eigh(cov)
    root = vec * np.sqrt(np.clip(lam, 0.0, None))          # cov = root @ root.T
    rng = np.random.default_rng(seed)
    growth = np.ones((n_sims, len(cols)))
    for _ in range(horizon):
        z = rng.standard_normal((n_sims, len(cols))) @ root.T
        if df_t and df_t > 2:
            chi = rng.chisquare(df_t, size=(n_sims, 1)) / df_t
            z = z / np.sqrt(chi) * np.sqrt((df_t - 2.0) / df_t)   # skalowanie zachowuje kowariancję
        growth *= 1.0 + z
    return (growth - 1.0) @ w


def var_monte_carlo(returns: pd.DataFrame, exposure: pd.Series, level: float = 0.95, horizon: int = 1,
                    n_sims: int = 20000, seed: int = 42, df_t: float | None = None) -> tuple[float, float]:
    sims = simulate_pnl(returns, exposure, horizon, n_sims, seed, df_t)
    q = np.quantile(sims, 1.0 - level)
    tail = sims[sims <= q]
    return float(max(-q, 0.0)), float(max(-tail.mean(), 0.0))


def var_table(returns: pd.DataFrame, exposure: pd.Series, equity: float, levels=(0.95, 0.99),
              horizons=(1, 10), n_sims: int = 20000, seed: int = 42) -> pd.DataFrame:
    """Zestawienie VaR i CVaR trzema metodami. Wartości w PLN i jako % kapitału."""
    pnl = pnl_series(returns, exposure)
    sigma = float(pnl.std(ddof=1)) if len(pnl) > 1 else np.nan
    df = fit_t_df(pnl)   # Monte Carlo używa grubych ogonów, jeśli widać je w danych
    rows = []
    for h in horizons:
        for lv in levels:
            mc_var, mc_cvar = var_monte_carlo(returns, exposure, lv, h, n_sims, seed, df)
            for method, v, c in (
                ("Historyczny", var_historical(pnl, lv, h), cvar_historical(pnl, lv, h)),
                ("Parametryczny", var_parametric(sigma, lv, h), cvar_parametric(sigma, lv, h)),
                ("Monte Carlo", mc_var, mc_cvar),
            ):
                rows.append({"Metoda": method, "Poziom ufności": lv, "Horyzont (dni)": h, "VaR": v, "CVaR": c,
                             "VaR % kapitału": v / equity if equity else np.nan,
                             "CVaR % kapitału": c / equity if equity else np.nan})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Korelacje, dywersyfikacja, udział w ryzyku
# --------------------------------------------------------------------------- #

def correlation_matrix(returns: pd.DataFrame, window: int = 90) -> pd.DataFrame:
    r = returns.tail(window)
    r = r.loc[:, r.std(ddof=1) > 0]
    return r.corr()


def average_correlation(corr: pd.DataFrame) -> float:
    n = corr.shape[0]
    if n < 2:
        return np.nan
    v = corr.values
    return float((v.sum() - np.trace(v)) / (n * (n - 1)))


def rolling_average_correlation(returns: pd.DataFrame, window: int = 90, step: int = 5) -> pd.Series:
    """Średnia korelacja par w oknie kroczącym (co `step` dni, żeby było szybko)."""
    r = returns.loc[:, returns.std(ddof=1) > 0]
    out = {}
    for end in range(window, len(r) + 1, step):
        out[r.index[end - 1]] = average_correlation(r.iloc[end - window:end].corr())
    return pd.Series(out, dtype=float)


def covariance(returns: pd.DataFrame, shrink: float = 0.0) -> pd.DataFrame:
    """Kowariancja dzienna; `shrink` w [0,1] ściąga ją do macierzy diagonalnej (stabilniejsza przy krótkiej historii)."""
    c = np.atleast_2d(np.cov(returns.values, rowvar=False, ddof=1))
    if shrink > 0:
        c = (1 - shrink) * c + shrink * np.diag(np.diag(c))
    return pd.DataFrame(c, index=returns.columns, columns=returns.columns)


def portfolio_volatility(exposure: pd.Series, cov: pd.DataFrame, annualize: bool = True) -> float:
    w = exposure.reindex(cov.index).fillna(0.0).values
    v = float(np.sqrt(max(w @ cov.values @ w, 0.0)))
    return v * np.sqrt(TRADING_DAYS) if annualize else v


def risk_contributions(exposure: pd.Series, cov: pd.DataFrame) -> pd.DataFrame:
    """Udział każdej pozycji w zmienności portfela (suma udziałów = 100%)."""
    w = exposure.reindex(cov.index).fillna(0.0).values.astype(float)
    sigma = float(np.sqrt(max(w @ cov.values @ w, 0.0)))
    marginal = cov.values @ w / sigma if sigma > 0 else np.zeros_like(w)
    rc = w * marginal
    gross = np.abs(w).sum()
    return pd.DataFrame({
        "exposure": w, "weight": w / gross if gross else 0.0,
        "standalone_vol": np.sqrt(np.diag(cov.values)) * np.sqrt(TRADING_DAYS),
        "risk_contribution": rc * np.sqrt(TRADING_DAYS),
        "risk_share": rc / sigma if sigma > 0 else 0.0,
    }, index=cov.index).sort_values("risk_share", ascending=False)


def effective_number_of_bets(exposure: pd.Series, cov: pd.DataFrame) -> float:
    """ENB Meucciego: entropia rozkładu wariancji po niezależnych składowych głównych."""
    w = exposure.reindex(cov.index).fillna(0.0).values.astype(float)
    if not np.any(w):
        return np.nan
    lam, vec = np.linalg.eigh(cov.values)
    lam = np.clip(lam, 0.0, None)
    v = (vec.T @ w) ** 2 * lam
    total = v.sum()
    if total <= 0:
        return np.nan
    p = v / total
    p = p[p > 1e-15]
    return float(np.exp(-(p * np.log(p)).sum()))


def diversification_ratio(exposure: pd.Series, cov: pd.DataFrame) -> float:
    w = exposure.reindex(cov.index).fillna(0.0).values.astype(float)
    sigma = float(np.sqrt(max(w @ cov.values @ w, 0.0)))
    return float((np.abs(w) * np.sqrt(np.diag(cov.values))).sum() / sigma) if sigma > 0 else np.nan


# --------------------------------------------------------------------------- #
# Ekspozycje, koncentracja, płynność
# --------------------------------------------------------------------------- #

def exposure_by(exposure: pd.Series, meta: pd.DataFrame, column: str, equity: float, cash: float = 0.0,
                cash_label: str | None = None) -> pd.DataFrame:
    """Ekspozycja długa / krótka / netto w podziale na sektor, kraj, walutę lub klasę aktywów (% kapitału)."""
    grp = meta[column].reindex(exposure.index).fillna("Nieznany")
    df = pd.DataFrame({"g": grp.values, "e": exposure.values})
    out = pd.DataFrame({
        "long": df[df["e"] > 0].groupby("g")["e"].sum(),
        "short": df[df["e"] < 0].groupby("g")["e"].sum(),
    }).fillna(0.0)
    if cash_label and abs(cash) > 1e-9:
        out.loc[cash_label, "long" if cash > 0 else "short"] = out.get("long", pd.Series(dtype=float)).get(cash_label, 0.0) + cash
        out = out.fillna(0.0)
    out["net"] = out["long"] + out["short"]
    out["gross"] = out["long"] - out["short"]
    for c in ("long", "short", "net", "gross"):
        out[c + "_pct"] = out[c] / equity if equity else np.nan
    out.index.name = column
    return out.sort_values("gross", ascending=False)


def currency_exposure(value: pd.Series, meta: pd.DataFrame, cash: float, equity: float) -> pd.DataFrame:
    """Co faktycznie zależy od kursu waluty: wartość akcji/ETF i niezrealizowany wynik CFD + gotówka w PLN."""
    cur = meta["currency"].reindex(value.index).fillna("PLN")
    s = value.groupby(cur.values).sum()
    s.loc["PLN"] = s.get("PLN", 0.0) + cash
    out = pd.DataFrame({"value": s, "share": s / equity if equity else np.nan})
    out.index.name = "currency"
    return out.sort_values("value", ascending=False)


def concentration(exposure: pd.Series) -> dict:
    w = exposure.abs()
    total = w.sum()
    if total <= 0:
        return {"top1": np.nan, "top5": np.nan, "hhi": np.nan, "effective_n": np.nan, "n": 0}
    w = (w / total).sort_values(ascending=False)
    hhi = float((w ** 2).sum())
    return {"top1": float(w.iloc[0]), "top5": float(w.head(5).sum()), "hhi": hhi,
            "effective_n": 1.0 / hhi, "n": int((w > 0).sum())}


def liquidity_table(qty: pd.Series, volumes: pd.DataFrame, participation: float = 0.20, window: int = 30) -> pd.DataFrame:
    """Dni potrzebne do zamknięcia pozycji przy sprzedaży `participation` średniego dziennego wolumenu."""
    rows = {}
    for sym, q in qty.items():
        if abs(q) < 1e-12:
            continue
        adv = np.nan
        if sym in volumes.columns:
            v = volumes[sym].dropna().tail(window)
            v = v[v > 0]
            adv = float(v.mean()) if len(v) else np.nan
        days = abs(q) / (participation * adv) if adv and adv > 0 else np.nan
        rows[sym] = {"qty": float(q), "adv": adv, "days_to_exit": days}
    return pd.DataFrame.from_dict(rows, orient="index", columns=["qty", "adv", "days_to_exit"]).sort_values(
        "days_to_exit", ascending=False, na_position="last")
