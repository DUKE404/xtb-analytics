"""Zakładka Przegląd: 6 kluczowych liczb, krzywa kapitału i obsunięcie."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import ui
from core import performance as perf


def key_numbers(ctx) -> list[tuple]:
    pf = ctx.pf
    s = perf.summary(pf.returns, ctx.bench_series(), ctx.rf, pf.flows, float(pf.equity.iloc[-1]))
    return [
        ("Wartość portfela", ui.pln(pf.equity.iloc[-1]), "Wartość portfela"),
        ("Wynik całkowity", ui.pln(pf.total_pnl()), "Wynik całkowity", ui.pct(s["total_return"], sign=True) + " TWR"),
        ("CAGR", ui.pct(s["cagr"]), "CAGR"),
        ("Max drawdown", ui.pct(s["max_drawdown"]), "Max drawdown"),
        ("Sharpe", ui.num(s["sharpe"]), "Sharpe"),
        ("Zmienność", ui.pct(s["volatility"]), "Zmienność"),
    ]


def render(ctx) -> None:
    pf = ctx.pf
    ui.metric_row(key_numbers(ctx))

    eq = pd.DataFrame({"Wartość portfela": pf.equity, "Wpłaty netto": pf.flows.cumsum()})
    ui.plot(ui.line_chart(eq, "Krzywa kapitału: wartość portfela a wpłacony kapitał (PLN)", yfmt=",.0f", ysuffix=" zł",
                          colors={"Wartość portfela": ui.BLUE, "Wpłaty netto": ui.NEUTRAL}, height=400), key="ov_equity")

    dd = perf.drawdown_series(pf.returns)
    fig = go.Figure(go.Scatter(x=dd.index, y=dd.values, mode="lines", line=dict(width=2, color=ui.RED),
                               fill="tozeroy", fillcolor="rgba(230,103,103,0.10)",
                               hovertemplate="%{y:.1%}<extra>obsunięcie</extra>"))
    ui.style(fig, 240, "Obsunięcie od szczytu (liczone na stopie zwrotu, bez wpływu wpłat)", legend=False)
    fig.update_yaxes(tickformat=".0%")
    ui.plot(fig, key="ov_dd")

    st.markdown("#### Aktualny skład")
    val = pf.current_value()
    exp = pf.current_exposure()
    if len(val):
        tbl = pd.DataFrame({
            "Nazwa": pf.meta["name"].reindex(val.index), "Klasa": pf.meta["asset_class"].reindex(val.index),
            "Ilość": pf.qty.iloc[-1].reindex(val.index), "Cena": pf.prices.iloc[-1].reindex(val.index),
            "Waluta": pf.meta["currency"].reindex(val.index), "Wartość / wynik CFD": val,
            "Ekspozycja": exp.reindex(val.index), "Udział w kapitale": exp.reindex(val.index) / pf.equity.iloc[-1],
        }).sort_values("Ekspozycja", ascending=False)
        ui.table_fmt(tbl.reset_index().rename(columns={"index": "Symbol"}), pct=("Udział w kapitale",),
                     money=("Wartość / wynik CFD", "Ekspozycja"), dec=("Cena", "Ilość"), height=360)
        ui.explain(f"Gotówka: {ui.pln(pf.cash.iloc[-1])} ({ui.pct(pf.cash.iloc[-1] / pf.equity.iloc[-1])} kapitału). "
                   "Dla CFD „wartość” to niezrealizowany wynik, a „ekspozycja” to wartość nominalna pozycji.")
    else:
        st.info("Portfel jest obecnie w całości w gotówce.")
