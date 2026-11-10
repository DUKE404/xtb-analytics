"""Miary wyników: TWR, MWR/XIRR, statystyki ryzyka i zwrotu, obsunięcia, porównanie z benchmarkiem."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import optimize, stats

TRADING_DAYS = 252


def _clean(returns: pd.Series) -> pd.Series:
    return pd.Series(returns).astype(float).replace([np.inf, -np.inf], np.nan).dropna()


def twr_index(returns: pd.Series, base: float = 100.0) -> pd.Series:
    """Indeks łańcuchowany z dziennych zwrotów (start = base)."""
    return base * (1.0 + _clean(returns)).cumprod()


def total_return(returns: pd.Series) -> float:
    r = _clean(returns)
    return float((1.0 + r).prod() - 1.0) if len(r) else np.nan


def cagr(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    r = _clean(returns)
    if len(r) < 2:
        return np.nan
    growth = float((1.0 + r).prod())
    if growth <= 0:
        return -1.0
    return growth ** (periods / len(r)) - 1.0


def volatility(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    r = _clean(returns)
    return float(r.std(ddof=1) * np.sqrt(periods)) if len(r) > 1 else np.nan


def sharpe(returns: pd.Series, rf_annual: float = 0.0, periods: int = TRADING_DAYS) -> float:
    r = _clean(returns) - rf_annual / periods
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(periods)) if len(r) > 1 and sd > 0 else np.nan


def downside_deviation(returns: pd.Series, target_annual: float = 0.0, periods: int = TRADING_DAYS) -> float:
    r = _clean(returns) - target_annual / periods
    if not len(r):
        return np.nan
    return float(np.sqrt(np.mean(np.minimum(r, 0.0) ** 2)) * np.sqrt(periods))


def sortino(returns: pd.Series, rf_annual: float = 0.0, periods: int = TRADING_DAYS) -> float:
    r = _clean(returns) - rf_annual / periods
    dd = downside_deviation(returns, rf_annual, periods)
    return float(r.mean() * periods / dd) if dd and dd > 0 else np.nan


# --------------------------------------------------------------------------- #
# Obsunięcia
# --------------------------------------------------------------------------- #

def drawdown_series(returns: pd.Series) -> pd.Series:
    idx = (1.0 + _clean(returns)).cumprod()
    peak = np.maximum(idx.cummax(), 1.0)  # szczytem jest też poziom startowy
    return idx / peak - 1.0


def drawdown_stats(returns: pd.Series) -> dict:
    """Największe obsunięcie, daty szczytu/dołka/odrobienia, najdłuższe obsunięcie (dni kalendarzowe)."""
    dd = drawdown_series(returns)
    out = {"max_drawdown": np.nan, "peak_date": None, "trough_date": None, "recovery_date": None,
           "recovery_days": np.nan, "max_duration_days": np.nan, "current_drawdown": np.nan,
           "longest_ongoing": False}
    if not len(dd):
        return out
    out["max_drawdown"] = float(dd.min())
    out["current_drawdown"] = float(dd.iloc[-1])
    if dd.min() >= 0:
        out.update(max_duration_days=0.0, recovery_days=0.0)
        return out
    trough = dd.idxmin()
    before = dd.loc[:trough]
    at_peak = before[before >= -1e-12]
    peak = at_peak.index[-1] if len(at_peak) else dd.index[0]
    after = dd.loc[trough:]
    rec = after[after >= -1e-12]
    out.update(peak_date=peak, trough_date=trough)
    if len(rec):
        out["recovery_date"] = rec.index[0]
        out["recovery_days"] = float((rec.index[0] - trough).days)
    # najdłuższy okres pod wodą
    under = (dd < -1e-12).values
    longest, start, ongoing = 0, None, False
    dates = dd.index
    for i, u in enumerate(under):
        if u and start is None:
            start = dates[i - 1] if i > 0 else dates[0]
        if start is not None and (not u or i == len(under) - 1):
            end = dates[i]
            dur = (end - start).days
            if dur > longest:
                longest, ongoing = dur, bool(u)
            if not u:
                start = None
    out["max_duration_days"] = float(longest)
    out["longest_ongoing"] = ongoing
    return out


def max_drawdown(returns: pd.Series) -> float:
    dd = drawdown_series(returns)
    return float(dd.min()) if len(dd) else np.nan


def calmar(returns: pd.Series) -> float:
    mdd = max_drawdown(returns)
    return float(cagr(returns) / abs(mdd)) if mdd and mdd < 0 else np.nan


# --------------------------------------------------------------------------- #
# MWR / XIRR
# --------------------------------------------------------------------------- #

def xirr(dates, amounts) -> float:
    """Wewnętrzna stopa zwrotu dla nieregularnych przepływów (konwencja ACT/365, jak XIRR w Excelu).

    Znak z perspektywy inwestora: wpłata na rachunek ujemna, wypłata i wartość końcowa dodatnie.
    """
    d = pd.to_datetime(pd.Series(list(dates))).reset_index(drop=True)
    a = np.asarray(list(amounts), dtype=float)
    mask = a != 0
    d, a = d[mask].reset_index(drop=True), a[mask]
    if len(a) < 2 or (a > 0).all() or (a < 0).all():
        return np.nan
    t = (d - d.min()).dt.days.values / 365.0

    def npv(rate):
        return float(np.sum(a / (1.0 + rate) ** t))

    lo, hi = -0.9999, 10.0
    try:
        f_lo, f_hi = npv(lo), npv(hi)
        while f_lo * f_hi > 0 and hi < 1e6:
            hi *= 10
            f_hi = npv(hi)
        if f_lo * f_hi > 0:
            return np.nan
        return float(optimize.brentq(npv, lo, hi, xtol=1e-12, maxiter=500))
    except (ValueError, OverflowError, FloatingPointError):
        return np.nan


def portfolio_xirr(flows: pd.Series, final_value: float) -> float:
    """MWR portfela: przepływy zewnętrzne (wpłaty +) i wartość końcowa w ostatnim dniu."""
    f = flows[flows != 0]
    dates = list(f.index) + [flows.index[-1]]
    amounts = list(-f.values) + [float(final_value)]
    return xirr(dates, amounts)


# --------------------------------------------------------------------------- #
# Relacja do benchmarku
# --------------------------------------------------------------------------- #

def _pair(returns: pd.Series, bench: pd.Series) -> pd.DataFrame:
    df = pd.concat([_clean(returns).rename("p"), _clean(bench).rename("b")], axis=1).dropna()
    return df


def beta_alpha(returns: pd.Series, bench: pd.Series, rf_annual: float = 0.0, periods: int = TRADING_DAYS) -> tuple[float, float]:
    """Beta i alfa Jensena (alfa w skali roku)."""
    df = _pair(returns, bench)
    if len(df) < 20 or df["b"].var(ddof=1) == 0:
        return np.nan, np.nan
    rf = rf_annual / periods
    ep, eb = df["p"] - rf, df["b"] - rf
    beta = float(np.cov(ep, eb, ddof=1)[0, 1] / eb.var(ddof=1))
    alpha = float((ep.mean() - beta * eb.mean()) * periods)
    return beta, alpha


def tracking_error(returns: pd.Series, bench: pd.Series, periods: int = TRADING_DAYS) -> float:
    df = _pair(returns, bench)
    return float((df["p"] - df["b"]).std(ddof=1) * np.sqrt(periods)) if len(df) > 1 else np.nan


def information_ratio(returns: pd.Series, bench: pd.Series, periods: int = TRADING_DAYS) -> float:
    df = _pair(returns, bench)
    if len(df) < 2:
        return np.nan
    active = df["p"] - df["b"]
    sd = active.std(ddof=1)
    return float(active.mean() / sd * np.sqrt(periods)) if sd > 0 else np.nan


def correlation(returns: pd.Series, bench: pd.Series) -> float:
    df = _pair(returns, bench)
    return float(df["p"].corr(df["b"])) if len(df) > 2 else np.nan


def levels_to_returns(levels: pd.DataFrame | pd.Series, dates: pd.DatetimeIndex) -> pd.DataFrame | pd.Series:
    """Poziomy (np. indeksy w PLN) -> dzienne zwroty na kalendarzu portfela."""
    lv = levels.sort_index()
    lv = lv.reindex(lv.index.union(dates)).ffill().reindex(dates)
    return lv.pct_change().fillna(0.0)


# --------------------------------------------------------------------------- #
# Tabele okresowe i podsumowanie
# --------------------------------------------------------------------------- #

MONTHS_PL = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"]


def monthly_returns(returns: pd.Series) -> pd.Series:
    r = _clean(returns)
    return (1.0 + r).groupby([r.index.year, r.index.month]).prod() - 1.0


def monthly_table(returns: pd.Series) -> pd.DataFrame:
    """Lata w wierszach, miesiące w kolumnach + kolumna 'Rok'."""
    m = monthly_returns(returns)
    if not len(m):
        return pd.DataFrame(columns=MONTHS_PL + ["Rok"])
    tbl = m.unstack(level=1).reindex(columns=range(1, 13))
    tbl.columns = MONTHS_PL
    tbl["Rok"] = yearly_returns(returns).reindex(tbl.index).values
    tbl.index.name = None
    return tbl


def yearly_returns(returns: pd.Series) -> pd.Series:
    r = _clean(returns)
    return (1.0 + r).groupby(r.index.year).prod() - 1.0


def summary(returns: pd.Series, bench: pd.Series | None = None, rf_annual: float = 0.0,
            flows: pd.Series | None = None, final_value: float | None = None) -> dict:
    r = _clean(returns)
    dd = drawdown_stats(r)
    out = {
        "total_return": total_return(r), "cagr": cagr(r), "volatility": volatility(r),
        "sharpe": sharpe(r, rf_annual), "sortino": sortino(r, rf_annual), "calmar": calmar(r),
        "max_drawdown": dd["max_drawdown"], "max_dd_duration_days": dd["max_duration_days"],
        "max_dd_recovery_days": dd["recovery_days"], "current_drawdown": dd["current_drawdown"],
        "dd_peak": dd["peak_date"], "dd_trough": dd["trough_date"], "dd_recovery": dd["recovery_date"],
        "dd_longest_ongoing": dd["longest_ongoing"],
        "skew": float(stats.skew(r, bias=False)) if len(r) > 2 else np.nan,
        "kurtosis": float(stats.kurtosis(r, fisher=True, bias=False)) if len(r) > 3 else np.nan,
        "best_day": float(r.max()) if len(r) else np.nan, "worst_day": float(r.min()) if len(r) else np.nan,
        "positive_days": float((r > 0).mean()) if len(r) else np.nan, "n_days": int(len(r)),
        "xirr": np.nan, "beta": np.nan, "alpha": np.nan, "tracking_error": np.nan,
        "information_ratio": np.nan, "correlation": np.nan,
    }
    if flows is not None and final_value is not None:
        out["xirr"] = portfolio_xirr(flows, final_value)
    if bench is not None and len(bench):
        b, a = beta_alpha(r, bench, rf_annual)
        out.update(beta=b, alpha=a, tracking_error=tracking_error(r, bench),
                   information_ratio=information_ratio(r, bench), correlation=correlation(r, bench))
    return out


def comparison_table(portfolio_returns: pd.Series, bench_returns: pd.DataFrame, rf_annual: float = 0.0) -> pd.DataFrame:
    """Portfel i benchmarki obok siebie: zwrot, CAGR, zmienność, Sharpe, max drawdown."""
    rows = {}
    for name, r in [("Portfel", portfolio_returns)] + [(c, bench_returns[c]) for c in bench_returns.columns]:
        rows[name] = {"Zwrot": total_return(r), "CAGR": cagr(r), "Zmienność": volatility(r),
                      "Sharpe": sharpe(r, rf_annual), "Max drawdown": max_drawdown(r)}
    return pd.DataFrame(rows).T
