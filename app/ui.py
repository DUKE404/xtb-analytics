"""Wspólne elementy UI: paleta, styl wykresów, formatowanie liczb, kafelki z tooltipami."""
from __future__ import annotations

import inspect
import math

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app.tooltips import TOOLTIPS

# Paleta kategorialna (wariant na ciemne tło, sprawdzona pod kątem daltonizmu - kolejność ma znaczenie)
PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET, RED = PALETTE
NEUTRAL = "#898781"
SURFACE = "#1a1a19"
GRID = "#2c2c2a"
AXIS = "#383835"
INK = "#f0efec"
INK2 = "#c3c2b7"
# skala rozbieżna: ujemne = czerwony, zero = neutralny szary, dodatnie = niebieski
DIVERGING = [[0.0, "#e66767"], [0.5, "#383835"], [1.0, "#3987e5"]]
SEQUENTIAL = [[0.0, "#0d366b"], [0.5, "#2a78d6"], [1.0, "#cde2fb"]]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def _supports(func, param: str) -> bool:
    try:
        return param in inspect.signature(func).parameters
    except (TypeError, ValueError):
        return False


def _stretch(func) -> dict:
    """Nowe wersje Streamlit używają width='stretch', starsze use_container_width=True."""
    return {"width": "stretch"} if _supports(func, "width") else {"use_container_width": True}


def style(fig: go.Figure, height: int = 380, title: str | None = None, legend: bool = True,
          unified: bool = True) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, color=INK2, size=13), colorway=PALETTE, height=height,
        margin=dict(l=10, r=10, t=56 if title else 28, b=10), showlegend=legend,
        hovermode="x unified" if unified else "closest", bargap=0.25,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0, title_text=""),
        hoverlabel=dict(bgcolor=SURFACE, font=dict(color=INK, family=FONT)),
    )
    if title:
        fig.update_layout(title=dict(text=title, font=dict(size=15, color=INK), x=0, xanchor="left"))
    fig.update_xaxes(gridcolor=GRID, linecolor=AXIS, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, linecolor=AXIS, zerolinecolor=AXIS)
    return fig


def plot(fig: go.Figure, key: str | None = None) -> None:
    kwargs = _stretch(st.plotly_chart)
    if key:
        kwargs["key"] = key
    st.plotly_chart(fig, theme=None, config={"displaylogo": False}, **kwargs)


def table(df: pd.DataFrame, height: int | None = None, hide_index: bool = True, column_config=None) -> None:
    kwargs = _stretch(st.dataframe)
    if height:
        kwargs["height"] = height
    if column_config:
        kwargs["column_config"] = column_config
    st.dataframe(df, hide_index=hide_index, **kwargs)


def editor(df: pd.DataFrame, key: str, **kwargs) -> pd.DataFrame:
    return st.data_editor(df, key=key, **(_stretch(st.data_editor) | kwargs))


# --------------------------------------------------------------------------- #
# Formatowanie po polsku
# --------------------------------------------------------------------------- #

def _bad(x) -> bool:
    return x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def num(x, digits: int = 2) -> str:
    if _bad(x):
        return "–"
    s = f"{float(x):,.{digits}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def pln(x, digits: int = 0) -> str:
    return "–" if _bad(x) else f"{num(x, digits)} zł"


def pct(x, digits: int = 1, sign: bool = False) -> str:
    if _bad(x):
        return "–"
    s = num(float(x) * 100, digits) + "%"
    return "+" + s if sign and float(x) > 0 else s


def days(x) -> str:
    return "–" if _bad(x) else f"{int(round(float(x)))} dni"


def tip(key: str) -> str | None:
    return TOOLTIPS.get(key)


def metric(container, label: str, value: str, key: str | None = None, delta: str | None = None,
           delta_color: str = "normal") -> None:
    """Kafelek z liczbą i podpowiedzią 'co to znaczy' (prostym językiem)."""
    container.metric(label, value, delta=delta, delta_color=delta_color, help=tip(key or label))


def metric_row(items: list[tuple]) -> None:
    """items: (etykieta, wartość, klucz tooltipa[, delta])."""
    cols = st.columns(len(items))
    for col, item in zip(cols, items):
        label, value, key, *rest = item
        metric(col, label, value, key, rest[0] if rest else None)


def explain(text: str) -> None:
    st.caption(text)


def warn_list(items: list[str], title: str = "Uwagi do danych") -> None:
    if items:
        with st.expander(f"⚠️ {title} ({len(items)})", expanded=False):
            for w in items:
                st.markdown(f"- {w}")


# --------------------------------------------------------------------------- #
# Gotowe wykresy
# --------------------------------------------------------------------------- #

def line_chart(df: pd.DataFrame, title: str | None = None, height: int = 380, yfmt: str | None = None,
               colors: dict | None = None, ysuffix: str = "") -> go.Figure:
    """Linie na jednej osi; kolor idzie za nazwą serii (stała kolejność palety)."""
    fig = go.Figure()
    for i, col in enumerate(df.columns):
        color = (colors or {}).get(col, PALETTE[i % len(PALETTE)])
        fig.add_trace(go.Scatter(x=df.index, y=df[col], name=str(col), mode="lines",
                                 line=dict(width=2, color=color),
                                 hovertemplate="%{y:" + (yfmt or ",.2f") + "}" + ysuffix + "<extra>" + str(col) + "</extra>"))
    style(fig, height, title, legend=len(df.columns) > 1)
    if yfmt:
        fig.update_yaxes(tickformat=yfmt)
    return fig


def hbar(series: pd.Series, title: str | None = None, height: int | None = None, fmt: str = ",.0f",
         suffix: str = "", signed: bool = False, color: str = BLUE) -> go.Figure:
    """Poziome słupki posortowane; przy `signed` kolor koduje znak (niebieski + / czerwony -)."""
    s = series.dropna()
    s = s.iloc[::-1]
    colors = [BLUE if v >= 0 else RED for v in s.values] if signed else color
    fig = go.Figure(go.Bar(x=s.values, y=[str(i) for i in s.index], orientation="h", marker=dict(color=colors, line=dict(width=0)),
                           text=[f"{v:{fmt}}{suffix}".replace(",", " ") for v in s.values],
                           textposition="outside", textfont=dict(color=INK2), cliponaxis=False,
                           hovertemplate="%{y}: %{x:" + fmt + "}" + suffix + "<extra></extra>"))
    style(fig, height or max(220, 26 * len(s) + 80), title, legend=False, unified=False)
    fig.update_yaxes(showgrid=False)
    fig.update_xaxes(tickformat=fmt)
    fig.update_layout(margin=dict(l=10, r=60, t=56 if title else 20, b=10))
    return fig


def heatmap(z: pd.DataFrame, title: str | None = None, height: int = 420, diverging: bool = True,
            fmt: str = ".2f", zmid: float | None = 0.0, text: bool = True, suffix: str = "") -> go.Figure:
    kwargs = dict(colorscale=DIVERGING, zmid=zmid) if diverging else dict(colorscale=SEQUENTIAL)
    fig = go.Figure(go.Heatmap(
        z=z.values, x=[str(c) for c in z.columns], y=[str(i) for i in z.index], xgap=2, ygap=2,
        texttemplate=("%{z:" + fmt + "}" + suffix) if text else None, textfont=dict(color=INK, size=11),
        hovertemplate="%{y} / %{x}: %{z:" + fmt + "}" + suffix + "<extra></extra>",
        colorbar=dict(thickness=10, outlinewidth=0, tickformat=fmt), **kwargs))
    style(fig, height, title, legend=False, unified=False)
    fig.update_xaxes(showgrid=False, side="bottom")
    fig.update_yaxes(showgrid=False, autorange="reversed")
    return fig
