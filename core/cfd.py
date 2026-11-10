"""Metryki CFD: efektywna dźwignia, poziom depozytu i odległość do stop outu, koszt swapów."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.portfolio import Portfolio

STOP_OUT_LEVEL = 0.50    # XTB (klient detaliczny): automatyczne zamykanie pozycji przy poziomie depozytu 50%
MARGIN_CALL_LEVEL = 1.00


def open_cfd_table(pf: Portfolio, open_positions: pd.DataFrame) -> pd.DataFrame:
    """Otwarte pozycje CFD: nominał, depozyt, wynik, dźwignia pozycji."""
    cols = ["symbol", "name", "side", "lots", "open_price", "price", "nominal", "margin", "upl", "swap", "leverage"]
    if not len(open_positions):
        return pd.DataFrame(columns=cols)
    is_cfd = pf.is_cfd
    rows = []
    for p in open_positions.itertuples(index=False):
        if p.symbol not in is_cfd.index or not bool(is_cfd[p.symbol]):
            continue
        m = pf.meta.loc[p.symbol]
        mult, price, fx = float(m["multiplier"]), float(pf.prices[p.symbol].iloc[-1]), float(pf.fx[p.symbol].iloc[-1])
        sgn = -1.0 if p.type == "SELL" else 1.0
        nominal = p.volume * mult * price * fx
        rate = float(m.get("margin_rate", np.nan)) if "margin_rate" in m.index else np.nan
        margin = float(p.margin) if getattr(p, "margin", 0) and p.margin > 0 else (
            nominal * rate if np.isfinite(rate) and rate < 1 else nominal * 0.05)
        upl = sgn * p.volume * mult * (price - p.open_price) * fx
        rows.append({"symbol": p.symbol, "name": m["name"], "side": "długa" if sgn > 0 else "krótka",
                     "lots": p.volume, "open_price": p.open_price, "price": price, "nominal": sgn * nominal,
                     "margin": margin, "upl": upl, "swap": float(getattr(p, "swap", 0.0) or 0.0),
                     "leverage": nominal / margin if margin > 0 else np.nan})
    return pd.DataFrame(rows, columns=cols)


def leverage_summary(pf: Portfolio, open_positions: pd.DataFrame, stop_out: float = STOP_OUT_LEVEL) -> dict:
    """Dźwignia całego rachunku i odległość do automatycznego zamknięcia pozycji.

    Ostrożne założenie: zabezpieczeniem CFD jest tylko gotówka i niezrealizowany wynik na CFD
    (wartość akcji i ETF-ów nie jest wliczana do depozytu).
    """
    equity = float(pf.equity.iloc[-1])
    exp = pf.exposure.iloc[-1]
    is_cfd = pf.is_cfd.values
    stock = float(exp[~is_cfd].sum())
    cfd_net = float(exp[is_cfd].sum())
    cfd_gross = float(exp[is_cfd].abs().sum())
    tbl = open_cfd_table(pf, open_positions)
    margin = float(tbl["margin"].sum()) if len(tbl) else 0.0
    upl = float(pf.value.iloc[-1][is_cfd].sum())
    collateral = float(pf.cash.iloc[-1]) + upl
    out = {
        "equity": equity, "stock_exposure": stock, "cfd_nominal_gross": cfd_gross, "cfd_nominal_net": cfd_net,
        "gross_leverage": (abs(stock) + cfd_gross) / equity if equity > 0 else np.nan,
        "net_leverage": (stock + cfd_net) / equity if equity > 0 else np.nan,
        "margin_required": margin, "collateral": collateral, "cfd_upl": upl,
        "margin_level": collateral / margin if margin > 0 else np.nan,
        "free_margin": collateral - margin,
        "distance_to_margin_call": np.nan, "distance_to_stop_out": np.nan, "loss_to_stop_out": np.nan,
    }
    if margin > 0 and cfd_gross > 0:
        out["loss_to_stop_out"] = collateral - stop_out * margin
        out["distance_to_stop_out"] = out["loss_to_stop_out"] / cfd_gross
        out["distance_to_margin_call"] = (collateral - MARGIN_CALL_LEVEL * margin) / cfd_gross
    return out


def swap_costs(pf: Portfolio, cash: pd.DataFrame, closed: pd.DataFrame | None = None,
               open_positions: pd.DataFrame | None = None) -> dict:
    """Koszt swapów: suma historyczna, stawka roczna względem utrzymywanego nominału, bieżące tempo."""
    is_cfd = pf.is_cfd.values
    sw = cash[cash["kind"].isin(["SWAP", "ROLLOVER"])] if len(cash) else cash
    total = float(sw["amount"].sum()) if len(sw) else 0.0
    by_symbol = sw.groupby("symbol")["amount"].sum() if len(sw) else pd.Series(dtype=float)
    if total == 0.0:  # raport bez osobnych operacji swap: bierzemy kolumnę Swap z pozycji
        parts = [df[["symbol", "swap"]] for df in (closed, open_positions) if df is not None and len(df)]
        if parts:
            allp = pd.concat(parts)
            by_symbol = allp.groupby("symbol")["swap"].sum()
            by_symbol = by_symbol[by_symbol != 0]
            total = float(by_symbol.sum())
    gross = pf.exposure.loc[:, is_cfd].abs().sum(axis=1)
    # dni kalendarzowe do następnej sesji (piątek = 3 noce)
    gaps = np.append(np.diff(pf.dates.values).astype("timedelta64[D]").astype(float), 1.0)
    nominal_years = float((gross.values * gaps).sum() / 365.0)
    rate = -total / nominal_years if nominal_years > 0 else np.nan
    current_gross = float(gross.iloc[-1]) if len(gross) else 0.0
    return {
        "total": total, "annual_rate": rate, "nominal_years": nominal_years,
        "current_nominal": current_gross,
        "current_annual_cost": rate * current_gross if np.isfinite(rate) else np.nan,
        "by_symbol": by_symbol.sort_values(),
    }
