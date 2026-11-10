"""Zakładka Performance: stopy zwrotu, statystyki, porównanie z benchmarkami, atrybucja."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import ui
from core import attribution as attr
from core import performance as perf
from data.providers import BENCHMARK_LABELS

PERIODS = ["Cały okres", "Od początku roku", "Ostatnie 12 miesięcy"]
SERIES_COLORS = {"Portfel": ui.BLUE, "WIG20": ui.ORANGE, "WIG": ui.AQUA, "S&P 500 (w PLN)": ui.YELLOW,
                 "MSCI World ETF (w PLN)": ui.MAGENTA}


def _period_start(pf, period: str):
    if period == PERIODS[1]:
        return pd.Timestamp(pf.as_of.year, 1, 1)
    if period == PERIODS[2]:
        return pf.as_of - pd.DateOffset(years=1)
    return None


def render(ctx) -> None:
    pf = ctx.pf
    period = st.radio("Okres analizy", PERIODS, horizontal=True, key="perf_period")
    start = _period_start(pf, period)
    r = pf.returns if start is None else pf.returns.loc[start:]
    if len(r) < 5:
        st.info("Za mało danych w wybranym okresie.")
        return
    bench_all = ctx.bench_returns().loc[r.index[0]:]
    bench = ctx.bench_series().loc[r.index[0]:]
    flows = pf.flows.loc[r.index[0]:].copy()
    if start is not None:
        prev = pf.equity.loc[:r.index[0]].iloc[:-1]
        flows.iloc[0] += float(prev.iloc[-1]) if len(prev) else 0.0   # wartość początkowa okresu = "wpłata"
    s = perf.summary(r, bench, ctx.rf, flows, float(pf.equity.iloc[-1]))
    bench_label = BENCHMARK_LABELS.get(ctx.bench, ctx.bench)

    st.markdown("#### Stopy zwrotu")
    ui.metric_row([
        ("Stopa zwrotu (TWR)", ui.pct(s["total_return"]), "Stopa zwrotu (TWR)"),
        ("CAGR", ui.pct(s["cagr"]), "CAGR"),
        ("MWR / XIRR", ui.pct(s["xirr"]), "MWR / XIRR"),
        ("Zmienność", ui.pct(s["volatility"]), "Zmienność"),
        ("Najlepszy dzień", ui.pct(s["best_day"], 2), "Najlepszy dzień"),
        ("Najgorszy dzień", ui.pct(s["worst_day"], 2), "Najgorszy dzień"),
    ])
    st.markdown("#### Zwrot w relacji do ryzyka")
    rec = ui.days(s["max_dd_recovery_days"]) if s["dd_recovery"] is not None or s["max_drawdown"] == 0 else "nie odrobione"
    dur = ui.days(s["max_dd_duration_days"]) + (" (trwa)" if s["dd_longest_ongoing"] else "")
    ui.metric_row([
        ("Sharpe", ui.num(s["sharpe"]), "Sharpe"),
        ("Sortino", ui.num(s["sortino"]), "Sortino"),
        ("Calmar", ui.num(s["calmar"]), "Calmar"),
        ("Max drawdown", ui.pct(s["max_drawdown"]), "Max drawdown"),
        ("Najdłuższe obsunięcie", dur, "Czas obsunięcia"),
        ("Odrabianie max DD", rec, "Czas odrabiania"),
    ])
    st.markdown(f"#### Względem benchmarku: {bench_label}")
    ui.metric_row([
        ("Beta", ui.num(s["beta"]), "Beta"),
        ("Alfa (rocznie)", ui.pct(s["alpha"]), "Alfa"),
        ("Tracking error", ui.pct(s["tracking_error"]), "Tracking error"),
        ("Information ratio", ui.num(s["information_ratio"]), "Information ratio"),
        ("Skośność", ui.num(s["skew"]), "Skośność"),
        ("Kurtoza", ui.num(s["kurtosis"]), "Kurtoza"),
    ])
    if s["n_days"] < 500:
        ui.explain("Uwaga: przy historii krótszej niż ok. 2 lata Sharpe, alfa i information ratio mają dużą "
                   "niepewność - traktuj je jako orientacyjne.")

    # --- krzywa kapitału vs benchmarki (wszystko przeliczone do PLN, start = 100)
    curves = pd.DataFrame({"Portfel": perf.twr_index(r)})
    for k in bench_all.columns:
        curves[BENCHMARK_LABELS.get(k, k)] = perf.twr_index(bench_all[k])
    ui.plot(ui.line_chart(curves, "Portfel (TWR) a benchmarki - start okresu = 100", yfmt=",.0f",
                          colors=SERIES_COLORS, height=420), key="perf_curves")
    ui.explain("Indeksy zagraniczne są przeliczone na złote po kursie NBP, więc widać w nich także efekt walutowy. "
               "WIG jest indeksem dochodowym (z dywidendami), WIG20 i S&P 500 cenowymi.")
    cmp = perf.comparison_table(r, bench_all, ctx.rf).rename(index=BENCHMARK_LABELS)
    ui.table_fmt(cmp.reset_index().rename(columns={"index": "Seria"}), pct=("Zwrot", "CAGR", "Zmienność", "Max drawdown"),
                 dec=("Sharpe",))

    # --- zwroty miesięczne i roczne
    st.markdown("#### Zwroty miesięczne i roczne")
    tbl = perf.monthly_table(pf.returns)
    if len(tbl):
        ui.plot(ui.heatmap(tbl * 100, None, height=110 + 46 * len(tbl), fmt=".1f", suffix="%"), key="perf_heat")
    yr = perf.yearly_returns(pf.returns)
    yb = pd.DataFrame({"Portfel": yr})
    full_bench = ctx.bench_returns()
    for k in full_bench.columns:
        yb[BENCHMARK_LABELS.get(k, k)] = perf.yearly_returns(full_bench[k])
    fig = go.Figure()
    for name in yb.columns:
        fig.add_trace(go.Bar(x=[str(y) for y in yb.index], y=yb[name].values, name=name,
                             marker=dict(color=SERIES_COLORS.get(name, ui.NEUTRAL), line=dict(width=0)),
                             hovertemplate="%{y:.1%}<extra>" + name + "</extra>"))
    ui.style(fig, 320, "Zwroty roczne: portfel a benchmarki")
    fig.update_layout(barmode="group", bargap=0.35, bargroupgap=0.12)
    fig.update_yaxes(tickformat=".0%")
    ui.plot(fig, key="perf_years")

    # --- atrybucja
    st.markdown("#### Atrybucja wyniku - skąd wziął się wynik")
    a = attr.decompose(pf, ctx.export.cash, start=start)
    total = a["pnl"].sum()
    ui.metric_row([
        ("Wynik w okresie", ui.pln(total), "Wynik całkowity"),
        ("Efekt cen", ui.pln(a["price_effect"].sum()), None),
        ("Efekt walutowy", ui.pln(a["fx_effect"].sum()), "Ekspozycja walutowa"),
        ("Dywidendy netto", ui.pln(a["income"].sum()), None),
        ("Koszty i pozostałe", ui.pln(a["other"].sum()), "Koszty"),
    ])
    t1, t2, t3, t4, t5 = st.tabs(["Pozycje", "Sektory", "Kraje", "Waluty", "Tabela"])
    with t1:
        top = a.set_index("name")["pnl"]
        show = pd.concat([top.head(10), top.tail(10)]) if len(top) > 20 else top
        ui.plot(ui.hbar(show[~show.index.duplicated()], "Wkład pozycji w wynik (PLN) - najlepsze i najgorsze",
                        signed=True, suffix=" zł"), key="attr_pos")
    with t2:
        ui.plot(ui.hbar(attr.by_group(a, "sector")["pnl"], "Wkład sektorów (PLN)", signed=True, suffix=" zł"), key="attr_sec")
    with t3:
        ui.plot(ui.hbar(attr.by_group(a, "country")["pnl"], "Wkład krajów (PLN)", signed=True, suffix=" zł"), key="attr_cty")
    with t4:
        fx = attr.fx_effect_by_currency(a)
        if len(fx):
            ui.plot(ui.hbar(fx.sort_values(ascending=False), "Efekt walutowy wg waluty (PLN)", signed=True, suffix=" zł"),
                    key="attr_fx")
            ui.explain("Efekt walutowy to część wyniku, która wynika wyłącznie ze zmiany kursu waluty do złotego - "
                       "gdy złoty się umacnia, zagraniczne aktywa tracą na wartości w PLN, nawet jeśli ich ceny stoją.")
        else:
            st.info("Brak pozycji w walutach obcych w tym okresie.")
    with t5:
        tab = a.reset_index().rename(columns={
            "index": "Symbol", "name": "Nazwa", "pnl": "Wynik", "price_effect": "Efekt cen", "fx_effect": "Efekt walutowy",
            "income": "Dywidendy netto", "other": "Koszty i pozostałe", "contribution": "Wkład w TWR",
            "sector": "Sektor", "country": "Kraj"})
        ui.table_fmt(tab[["Symbol", "Nazwa", "Sektor", "Kraj", "Wynik", "Efekt cen", "Efekt walutowy",
                          "Dywidendy netto", "Koszty i pozostałe", "Wkład w TWR"]],
                     pct=("Wkład w TWR",), pct_digits=2,
                     money=("Wynik", "Efekt cen", "Efekt walutowy", "Dywidendy netto", "Koszty i pozostałe"), height=420)
        ui.explain("„Koszty i pozostałe” obejmują koszt przewalutowania i spreadu, swapy, prowizje, wynik transakcji "
                   "intraday (otwartych i zamkniętych tego samego dnia) oraz różnicę między ceną transakcji a ceną "
                   "zamknięcia dnia. Wkłady w TWR sumują się do stopy zwrotu portfela.")
