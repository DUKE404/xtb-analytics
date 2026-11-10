"""Zakładka Przegląd: kluczowe liczby, krzywa kapitału i obsunięcie."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import ui


def render(ctx) -> None:
    pf = ctx.pf
    twr = float((1 + pf.returns).prod() - 1)
    dd = pf.equity * 0  # placeholder, właściwe obsunięcie liczymy z indeksu TWR
    idx = (1 + pf.returns).cumprod()
    dd = idx / idx.cummax() - 1

    ui.metric_row([
        ("Wartość portfela", ui.pln(pf.equity.iloc[-1]), "Wartość portfela"),
        ("Wynik całkowity", ui.pln(pf.total_pnl()), "Wynik całkowity"),
        ("Wpłaty netto", ui.pln(pf.invested_capital()), "Wpłaty netto"),
        ("Stopa zwrotu (TWR)", ui.pct(twr), "Stopa zwrotu (TWR)"),
        ("Max drawdown", ui.pct(dd.min()), "Max drawdown"),
    ])

    eq = pd.DataFrame({"Wartość portfela": pf.equity, "Wpłaty netto": pf.flows.cumsum()})
    ui.plot(ui.line_chart(eq, "Wartość portfela a wpłacony kapitał (PLN)", yfmt=",.0f", ysuffix=" zł",
                          colors={"Wartość portfela": ui.BLUE, "Wpłaty netto": ui.NEUTRAL}), key="ov_equity")

    fig = go.Figure(go.Scatter(x=dd.index, y=dd.values, mode="lines", line=dict(width=2, color=ui.RED),
                               fill="tozeroy", fillcolor="rgba(230,103,103,0.10)",
                               hovertemplate="%{y:.1%}<extra>obsunięcie</extra>"))
    ui.style(fig, 240, "Obsunięcie od szczytu", legend=False)
    fig.update_yaxes(tickformat=".0%")
    ui.plot(fig, key="ov_dd")
