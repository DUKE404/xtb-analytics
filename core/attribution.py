"""Atrybucja wyniku: wkład pozycji, sektorów, krajów i efektu walutowego.

Dzienny wynik instrumentu rozkładamy na:
- efekt ceny:   ilość z wczoraj x zmiana ceny x kurs z wczoraj,
- efekt waluty: wartość w walucie obcej x zmiana kursu (z członem mieszanym),
- dochód:       dywidendy netto (po podatku u źródła),
- pozostałe:    reszta - koszty przewalutowania i spreadu, swapy, prowizje, różnica między ceną
                transakcji a ceną zamknięcia dnia.
Wkłady w stopę zwrotu są łańcuchowane tak, że sumują się dokładnie do TWR portfela.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.portfolio import CASH_BUCKET, OTHER_BUCKET, Portfolio

LABELS = {CASH_BUCKET: "Gotówka i odsetki", OTHER_BUCKET: "Pozostałe operacje"}


def linked_contributions(pf: Portfolio) -> pd.Series:
    """Wkład każdego instrumentu w TWR (suma = całkowity TWR portfela)."""
    prev = pf.equity.shift(1).fillna(0.0)
    base = (prev + pf.flows).values
    growth_prev = np.concatenate([[1.0], (1.0 + pf.returns.values).cumprod()[:-1]])
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(base > 1e-9, growth_prev / base, 0.0)
    return pf.pnl.mul(scale, axis=0).sum()


def decompose(pf: Portfolio, cash: pd.DataFrame | None = None, start=None, end=None) -> pd.DataFrame:
    """Tabela atrybucji per instrument (PLN + wkład w TWR w punktach procentowych)."""
    sl = slice(pd.Timestamp(start) if start is not None else None, pd.Timestamp(end) if end is not None else None)
    syms = pf.symbols
    q, px, fx, cost = pf.qty.values, pf.prices.values, pf.fx.values, pf.cost_local.values
    is_cfd = pf.is_cfd.values
    mult = np.where(is_cfd, pf.meta["multiplier"].reindex(syms).values.astype(float), 1.0)

    q_prev = np.vstack([np.zeros((1, len(syms))), q[:-1]])
    cost_prev = np.vstack([np.zeros((1, len(syms))), cost[:-1]])
    px_prev = np.vstack([px[:1], px[:-1]])
    fx_prev = np.vstack([fx[:1], fx[:-1]])

    price_eff = q_prev * (px - px_prev) * mult * fx_prev
    held_local = np.where(is_cfd, (q_prev * px - cost_prev) * mult, q_prev * px)
    fx_eff = held_local * (fx - fx_prev)

    idx = pf.dates
    price_eff = pd.DataFrame(price_eff, index=idx, columns=syms).loc[sl].sum()
    fx_eff = pd.DataFrame(fx_eff, index=idx, columns=syms).loc[sl].sum()
    pnl = pf.pnl.loc[sl].sum()

    income = pd.Series(0.0, index=syms)
    if cash is not None and len(cash):
        c = cash[cash["kind"].isin(["DIVIDEND", "WHT"]) & cash["symbol"].isin(syms)]
        if start is not None:
            c = c[c["time"] >= pd.Timestamp(start)]
        if end is not None:
            c = c[c["time"] <= pd.Timestamp(end) + pd.Timedelta(days=1)]
        income = income.add(c.groupby("symbol")["amount"].sum(), fill_value=0.0)

    out = pd.DataFrame({"pnl": pnl.reindex(syms), "price_effect": price_eff, "fx_effect": fx_eff, "income": income})
    out["other"] = out["pnl"] - out["price_effect"] - out["fx_effect"] - out["income"]
    for bucket in (CASH_BUCKET, OTHER_BUCKET):
        v = float(pnl.get(bucket, 0.0))
        fxv = 0.0
        if bucket == CASH_BUCKET and getattr(pf, "cash_fx_pnl", None) is not None:
            fxv = float(pf.cash_fx_pnl.loc[sl].values.sum())    # przeliczenie gotówki trzymanej w obcej walucie
        if abs(v) > 1e-9 or abs(fxv) > 1e-9:
            out.loc[bucket] = {"pnl": v, "price_effect": 0.0, "fx_effect": fxv, "income": 0.0, "other": v - fxv}

    if start is None and end is None:
        contrib = linked_contributions(pf)
    else:
        sub = pf.returns.loc[sl]
        prev = pf.equity.shift(1).fillna(0.0)
        base = (prev + pf.flows).loc[sl].values
        g = np.concatenate([[1.0], (1.0 + sub.values).cumprod()[:-1]])
        with np.errstate(divide="ignore", invalid="ignore"):
            scale = np.where(base > 1e-9, g / base, 0.0)
        contrib = pf.pnl.loc[sl].mul(scale, axis=0).sum()
    out["contribution"] = contrib.reindex(out.index).fillna(0.0)

    meta = pf.meta.reindex(out.index)
    out["name"] = [LABELS.get(i, meta.at[i, "name"] if i in pf.meta.index else i) for i in out.index]
    out["sector"] = meta["sector"].fillna("Gotówka i inne").values
    out["country"] = meta["country"].fillna("Gotówka i inne").values
    out["currency"] = meta["currency"].fillna(pf.base).values
    out["asset_class"] = meta["asset_class"].fillna("Gotówka").values
    return out.sort_values("pnl", ascending=False)


def by_group(attr: pd.DataFrame, column: str) -> pd.DataFrame:
    cols = ["pnl", "price_effect", "fx_effect", "income", "other", "contribution"]
    return attr.groupby(column)[cols].sum().sort_values("pnl", ascending=False)


def fx_effect_by_currency(attr: pd.DataFrame, base: str = "PLN", cash_fx: pd.Series | None = None) -> pd.Series:
    """Efekt walutowy per waluta; `cash_fx` (waluta -> kwota) to przeliczenie gotówki trzymanej w obcej walucie."""
    s = attr[~attr.index.isin([CASH_BUCKET, OTHER_BUCKET])].groupby("currency")["fx_effect"].sum()
    if cash_fx is not None:
        s = s.add(pd.Series(cash_fx, dtype=float), fill_value=0.0)
    return s[(s.index != base) & (s.abs() > 1e-9)].sort_values()
