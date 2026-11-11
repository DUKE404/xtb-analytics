"""Ekrany działu Wyniki: statystyki, benchmarki, miesiące, atrybucja."""
from __future__ import annotations

import pandas as pd

from core import attribution as attr
from core import performance as perf
from server import blocks as B

PERIODS = [("all", "Cały okres"), ("ytd", "Ten rok"), ("12m", "12 miesięcy")]


def _period_control(p):
    period = p.str("period", "all", {k for k, _ in PERIODS})
    return period, B.controls([B.control("period", "segmented", "Okres", period, PERIODS)])


def _start(pf, period):
    if period == "ytd":
        return pd.Timestamp(pf.as_of.year, 1, 1)
    if period == "12m":
        return pf.as_of - pd.DateOffset(years=1)
    return None


def _slice(ctx, p):
    pf = ctx.pf
    period, ctl = _period_control(p)
    start = _start(pf, period)
    r = pf.returns if start is None else pf.returns.loc[start:]
    return period, ctl, start, r


def summary(ctx, p) -> dict:
    pf = ctx.pf
    period, ctl, start, r = _slice(ctx, p)
    if len(r) < 5:
        return {"title": "Statystyki", "blocks": [ctl, B.note("Za mało danych w wybranym okresie.")]}
    bench = ctx.bench_series(p.bench).loc[r.index[0]:]
    flows = pf.flows.loc[r.index[0]:].copy()
    if start is not None:
        prev = pf.equity.loc[:r.index[0]].iloc[:-1]
        flows.iloc[0] += float(prev.iloc[-1]) if len(prev) else 0.0     # wartość na początku okresu = "wpłata"
    s = perf.summary(r, bench, p.rf, flows, float(pf.equity.iloc[-1]))
    recovered = s["dd_recovery"] is not None or s["max_drawdown"] == 0
    blocks = [
        ctl,
        B.metrics([
            B.metric("Stopa zwrotu (TWR)", s["total_return"], "pct"),
            B.metric("CAGR", s["cagr"], "pct"),
            B.metric("MWR / XIRR", s["xirr"], "pct"),
            B.metric("Zmienność", s["volatility"], "pct"),
            B.metric("Najlepszy dzień", s["best_day"], "pct", digits=2),
            B.metric("Najgorszy dzień", s["worst_day"], "pct", digits=2),
            B.metric("Dni na plusie", s["positive_days"], "pct", digits=0),
        ], "Stopy zwrotu"),
        B.metrics([
            B.metric("Sharpe", s["sharpe"]),
            B.metric("Sortino", s["sortino"]),
            B.metric("Calmar", s["calmar"]),
            B.metric("Max drawdown", s["max_drawdown"], "pct"),
            B.metric("Najdłuższe obsunięcie", s["max_dd_duration_days"], "days", tip="Czas obsunięcia",
                     note="nadal trwa" if s["dd_longest_ongoing"] else None),
            B.metric("Odrabianie największego spadku", s["max_dd_recovery_days"] if recovered else None, "days",
                     tip="Czas odrabiania", note=None if recovered else "jeszcze nie odrobione"),
        ], "Zwrot w relacji do ryzyka"),
        B.metrics([
            B.metric("Beta", s["beta"]),
            B.metric("Alfa (rocznie)", s["alpha"], "pct", tip="Alfa"),
            B.metric("Tracking error", s["tracking_error"], "pct"),
            B.metric("Information ratio", s["information_ratio"]),
            B.metric("Skośność", s["skew"]),
            B.metric("Kurtoza", s["kurtosis"]),
        ], f"Względem benchmarku: {ctx.bench_label(p.bench)}"),
    ]
    if s["n_days"] < 500:
        blocks.append(B.note("Przy historii krótszej niż ok. 2 lata Sharpe, alfa i information ratio mają dużą "
                             "niepewność. Traktuj je jako orientacyjne."))
    return {"title": "Statystyki", "blocks": blocks}


def benchmarks(ctx, p) -> dict:
    period, ctl, start, r = _slice(ctx, p)
    if len(r) < 5:
        return {"title": "Benchmarki", "blocks": [ctl, B.note("Za mało danych w wybranym okresie.")]}
    b = ctx.bench_returns().loc[r.index[0]:]
    curves = pd.DataFrame({"Portfel": perf.twr_index(r)})
    for k in b.columns:
        curves[ctx.bench_label(k)] = perf.twr_index(b[k])
    cmp = perf.comparison_table(r, b, p.rf).rename(index=ctx.bench_label).reset_index().rename(columns={"index": "name"})
    yr = pd.DataFrame({"Portfel": perf.yearly_returns(ctx.pf.returns)})
    full = ctx.bench_returns()
    for k in full.columns:
        yr[ctx.bench_label(k)] = perf.yearly_returns(full[k])
    blocks = [
        ctl,
        B.chart(B.line(curves, "num", 0), "Portfel a indeksy, start okresu = 100",
                f"Indeksy w innej walucie przeliczone na {_cur_name(ctx)} po kursach NBP, więc zawierają efekt walutowy. "
                "WIG20, S&P 500 i Nasdaq 100 to indeksy cenowe (bez dywidend); MSCI World jest liczony ceną ETF-u."),
        B.table(cmp, [("name", "Seria", "text"), ("Zwrot", "Zwrot", "pct", 1), ("CAGR", "CAGR", "pct", 1),
                      ("Zmienność", "Zmienność", "pct", 1), ("Sharpe", "Sharpe", "num", 2),
                      ("Max drawdown", "Max DD", "pct", 1)], title="Porównanie"),
        B.chart(B.bars(yr, "pct", 1), "Zwroty roczne (cała historia)"),
    ]
    if not len(b.columns):
        blocks.insert(1, B.note("Nie udało się pobrać notowań benchmarków - pokazuję sam portfel.", "warn"))
    return {"title": "Benchmarki", "blocks": blocks}


def periods(ctx, p) -> dict:
    r = ctx.pf.returns
    tbl = perf.monthly_table(r)
    months = tbl.drop(columns=["Rok"])
    yr = perf.yearly_returns(r)
    m = perf.monthly_returns(r)
    best, worst = (m.idxmax(), m.max()), (m.idxmin(), m.min())
    name = lambda ym: f"{perf.MONTHS_PL[ym[1] - 1]} {ym[0]}"  # noqa: E731
    blocks = [
        B.chart(B.heatmap(months, "pct", 1, diverging=True, pnl=True), "Zwroty miesięczne",
                "Zielony to zysk, czerwony strata. Im mocniejszy kolor, tym większy ruch. Stopy dzienne: Więcej → Kalendarz."),
        B.metrics([
            B.metric("Najlepszy miesiąc", best[1], "pct", note=name(best[0]), tip="Najwyższa miesięczna stopa zwrotu portfela."),
            B.metric("Najgorszy miesiąc", worst[1], "pct", note=name(worst[0]), tip="Najniższa miesięczna stopa zwrotu portfela."),
            B.metric("Miesiące na plusie", float((m > 0).mean()), "pct", digits=0,
                     tip="Odsetek miesięcy zakończonych zyskiem."),
        ]),
        B.chart(B.hbar(yr.rename(index=str), "pct", 1, signed=True), "Zwroty roczne"),
    ]
    return {"title": "Miesiące", "blocks": blocks}


def _cur_name(ctx) -> str:
    return {"PLN": "złote", "USD": "dolary", "EUR": "euro"}.get(ctx.base, ctx.base)


def attribution(ctx, p) -> dict:
    pf = ctx.pf
    period, ctl, start, _ = _slice(ctx, p)
    a = attr.decompose(pf, ctx.export.cash, start=start)
    pos = a.set_index("name")["pnl"]
    pos = pos[~pos.index.duplicated()]
    show = pd.concat([pos.head(8), pos.tail(8)]) if len(pos) > 16 else pos
    show = show[~show.index.duplicated()]
    cash_fx = pf.cash_fx_pnl.loc[start:].sum() if pf.cash_fx_pnl is not None and start is not None else \
        (pf.cash_fx_pnl.sum() if pf.cash_fx_pnl is not None else None)
    fx = attr.fx_effect_by_currency(a, ctx.base, cash_fx).sort_values(ascending=False)
    blocks = [
        ctl,
        B.metrics([
            B.metric("Wynik w okresie", float(a["pnl"].sum()), "pln", tip="Wynik całkowity", sign=True),
            B.metric("Efekt cen", float(a["price_effect"].sum()), "pln", sign=True,
                     tip="Ile dały same zmiany cen instrumentów, przy kursach walut zamrożonych z dnia poprzedniego."),
            B.metric("Efekt walutowy", float(a["fx_effect"].sum()), "pln", sign=True,
                     tip=f"Część wyniku, która wynika wyłącznie ze zmiany kursów walut wobec waluty wyświetlania ({ctx.base}): "
                         "akcji w innych walutach oraz gotówki na rachunku w innej walucie. Ceny mogą stać, a wynik "
                         "i tak się zmienia."),
            B.metric("Dywidendy netto", float(a["income"].sum()), "pln", sign=True,
                     tip="Dywidendy po potrąceniu podatku u źródła."),
            B.metric("Koszty i pozostałe", float(a["other"].sum()), "pln", sign=True,
                     tip="Koszt przewalutowania i spreadu, swapy, prowizje, wynik transakcji intraday oraz różnica "
                         "między ceną transakcji a ceną zamknięcia dnia."),
        ], "Skąd wziął się wynik"),
        B.chart(B.hbar(show, "pln", 0, signed=True), "Wkład pozycji: najlepsze i najgorsze"),
        B.chart(B.hbar(attr.by_group(a, "sector")["pnl"], "pln", 0, signed=True), "Wkład sektorów"),
        B.chart(B.hbar(attr.by_group(a, "country")["pnl"], "pln", 0, signed=True), "Wkład krajów"),
    ]
    if len(fx):
        blocks.append(B.chart(B.hbar(fx, "pln", 0, signed=True), "Efekt walutowy wg waluty"))
    tab = a.reset_index().rename(columns={"index": "symbol"})
    blocks.append(B.table(tab, [("name", "Pozycja", "text"), ("pnl", "Wynik", "pln"), ("contribution", "Wkład w TWR", "pct", 2),
                                ("price_effect", "Ceny", "pln"), ("fx_effect", "Waluta", "pln"), ("income", "Dywidendy", "pln"),
                                ("other", "Koszty i inne", "pln"), ("sector", "Sektor", "text"), ("country", "Kraj", "text")],
                          title="Pełna tabela", caption="Wkłady w TWR sumują się do stopy zwrotu portfela.", limit=10))
    return {"title": "Atrybucja", "blocks": blocks}
