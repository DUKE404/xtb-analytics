"""Symulator "co jeśli": jak planowana transakcja zmieni ryzyko portfela (VaR, zmienność, beta, koncentracja)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import risk


def _metrics(exposure: pd.Series, returns: pd.DataFrame, equity: float, cash: float,
             bench: pd.Series | None) -> dict:
    exp = exposure[exposure.abs() > 1e-9]
    cols = [c for c in exp.index if c in returns.columns]
    r = returns[cols].fillna(0.0)
    pnl = risk.pnl_series(r, exp.reindex(cols))
    port = pnl / equity if equity else pnl * 0.0
    conc = risk.concentration(exp)
    beta = np.nan
    if bench is not None and len(bench):
        df = pd.concat([port.rename("p"), bench.rename("b")], axis=1).dropna()
        if len(df) > 30 and df["b"].var() > 0:
            beta = float(np.cov(df["p"], df["b"], ddof=1)[0, 1] / df["b"].var(ddof=1))
    cov = risk.covariance(r) if len(cols) else pd.DataFrame()
    return {
        "var95": risk.var_historical(pnl, 0.95) if len(pnl) else 0.0,
        "cvar95": risk.cvar_historical(pnl, 0.95) if len(pnl) else 0.0,
        "volatility": float(port.std(ddof=1) * np.sqrt(252)) if len(port) > 1 else 0.0,
        "beta": beta, "top5": conc["top5"], "hhi": conc["hhi"],
        "enb": risk.effective_number_of_bets(exp.reindex(cols), cov) if len(cols) else np.nan,
        "gross_leverage": float(exp.abs().sum() / equity) if equity else np.nan,
        "cash": cash, "positions": int(len(exp)),
    }


def what_if(exposure: pd.Series, returns: pd.DataFrame, equity: float, cash: float, symbol: str, amount: float,
            is_cfd: bool = False, bench: pd.Series | None = None) -> dict:
    """Porównuje portfel przed i po transakcji.

    amount > 0: kupno za tyle PLN (dla CFD: długi nominał), amount < 0: sprzedaż / krótki nominał.
    `returns` musi zawierać kolumnę `symbol` (historia zwrotów w PLN), także dla nowego instrumentu.
    Kapitał (equity) się nie zmienia - zmienia się tylko jego podział między gotówkę i pozycje.
    """
    if symbol not in returns.columns:
        raise ValueError(f"Brak historii notowań dla {symbol}.")
    before = _metrics(exposure, returns, equity, cash, bench)
    new_exp = exposure.copy()
    new_exp.loc[symbol] = float(new_exp.get(symbol, 0.0)) + float(amount)
    new_cash = cash if is_cfd else cash - float(amount)
    after = _metrics(new_exp, returns, equity, new_cash, bench)

    # korelacja instrumentu z resztą portfela (bez niego)
    rest = exposure.drop(index=symbol, errors="ignore")
    rest = rest[rest.abs() > 1e-9]
    corr = np.nan
    if len(rest):
        cols = [c for c in rest.index if c in returns.columns]
        rest_pnl = risk.pnl_series(returns[cols].fillna(0.0), rest.reindex(cols))
        df = pd.concat([returns[symbol], rest_pnl], axis=1).dropna()
        if len(df) > 30 and df.iloc[:, 0].std() > 0 and df.iloc[:, 1].std() > 0:
            corr = float(df.iloc[:, 0].corr(df.iloc[:, 1]))
    return {"before": before, "after": after, "correlation_with_rest": corr,
            "position_before": float(exposure.get(symbol, 0.0)), "position_after": float(new_exp[symbol]),
            "weight_after": float(new_exp[symbol] / equity) if equity else np.nan}
