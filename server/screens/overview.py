"""Ekran Przegląd (6 kluczowych liczb, krzywa kapitału, obsunięcie, skład) i podgląd wczytanych danych."""
from __future__ import annotations

import pandas as pd

from core import performance as perf
from core import risk
from server import blocks as B


def _var95(ctx) -> float:
    r, exp, _ = ctx.risk_inputs()
    if not len(exp) or len(r) < 30:
        return float("nan")
    return risk.var_historical(risk.pnl_series(r, exp), 0.95)


def overview(ctx, p) -> dict:
    pf = ctx.pf
    equity = float(pf.equity.iloc[-1])
    s = perf.summary(pf.returns, ctx.bench_series(p.bench), p.rf, pf.flows, equity)
    var95 = _var95(ctx)

    if var95 == var95:  # nie NaN
        lead = (f"W 19 dni na 20 dzisiejszy portfel nie powinien stracić więcej niż {B.f_pln(var95)}, "
                f"czyli {B.f_pct(var95 / equity, 1)} kapitału.")
    else:
        lead = "Portfel jest w gotówce - nie ma dziś otwartych pozycji, które mogłyby tracić na wartości."
    pnl = pf.total_pnl()
    sub = (f"Od {pf.dates[0].strftime('%d.%m.%Y')} "
           f"{'zarobił' if pnl >= 0 else 'stracił'} {B.f_pln(abs(pnl))} przy {B.f_pln(pf.invested_capital())} wpłat netto.")

    eq = pd.DataFrame({"Portfel": pf.equity, "Wpłaty netto": pf.flows.cumsum()})
    dd = perf.drawdown_series(pf.returns).to_frame("Obsunięcie")
    blocks = [
        B.statement(lead, sub),
        B.chart(B.line(eq, "pln", 0), "Wartość portfela a wpłacony kapitał"),
        B.metrics([
            B.metric("Wartość portfela", equity, "pln"),
            B.metric("Wynik całkowity", pnl, "pln", sign=True, note=B.f_pct(s["total_return"], 1, sign=True) + " stopa zwrotu (TWR)"),
            B.metric("CAGR", s["cagr"], "pct"),
            B.metric("Max drawdown", s["max_drawdown"], "pct"),
            B.metric("Sharpe", s["sharpe"], "num"),
            B.metric("VaR 95% na 1 dzień", var95, "pln", tip="VaR",
                     note=(B.f_pct(var95 / equity, 2) + " kapitału") if var95 == var95 else None),
        ]),
        B.chart(B.line(dd, "pct", 1, colors={"Obsunięcie": "neg"}, area=True, zero=True), "Obsunięcie od szczytu",
                "Liczone na stopie zwrotu, więc wpłaty go nie maskują."),
    ]

    val, exp = pf.current_value(), pf.current_exposure()
    if len(val):
        tbl = pd.DataFrame({
            "name": pf.meta["name"].reindex(val.index), "class": pf.meta["asset_class"].reindex(val.index),
            "qty": pf.qty.iloc[-1].reindex(val.index), "price": pf.prices.iloc[-1].reindex(val.index),
            "currency": pf.meta["currency"].reindex(val.index), "value": val,
            "exposure": exp.reindex(val.index), "share": exp.reindex(val.index) / equity,
        }).sort_values("exposure", ascending=False)
        blocks.append(B.table(tbl, [("name", "Pozycja", "text"), ("share", "Udział", "pct", 1), ("exposure", "Ekspozycja", "pln"),
                                    ("value", "Wartość / wynik CFD", "pln"), ("qty", "Ilość", "num", 4),
                                    ("price", "Cena", "num", 2), ("currency", "Waluta", "text"), ("class", "Klasa", "text")],
                              title="Aktualny skład",
                              caption=f"Gotówka: {B.f_pln(pf.cash.iloc[-1])} ({B.f_pct(pf.cash.iloc[-1] / equity)} kapitału). "
                                      "Dla CFD „wartość” to niezrealizowany wynik, a „ekspozycja” to nominał pozycji."))
    else:
        blocks.append(B.note("Portfel jest obecnie w całości w gotówce."))
    return {"title": "Przegląd", "blocks": blocks}


def data(ctx, p) -> dict:
    """Podgląd tego, co wczytano z eksportu - do sprawdzenia, czy parser dobrze zrozumiał plik."""
    exp, pf = ctx.export, ctx.pf
    blocks = [
        B.metrics([
            B.metric("Gotówka wg operacji", float(pf.cash.iloc[-1]), "pln",
                     tip="Suma wszystkich operacji gotówkowych z raportu. Powinna zgadzać się z saldem w xStation."),
            B.metric("Wartość pozycji", float(pf.value.iloc[-1].sum()), "pln",
                     tip="Akcje i ETF-y po bieżących cenach plus niezrealizowany wynik na CFD."),
            B.metric("Wartość portfela", float(pf.equity.iloc[-1]), "pln",
                     note="Porównaj z polem Equity w xStation z dnia eksportu"),
        ], "Kontrola spójności"),
    ]
    if pf.warnings:
        blocks.append(B.bullets(pf.warnings, "Uwagi do danych"))
    by_kind = exp.cash.groupby("kind")["amount"].agg(["count", "sum"]).reset_index().sort_values("sum")
    blocks.append(B.table(by_kind, [("kind", "Rodzaj operacji", "text"), ("count", "Liczba", "int"), ("sum", "Suma", "pln", 2)],
                          title="Operacje gotówkowe wg rodzaju", limit=20))
    op = exp.open.assign(open_time=exp.open["open_time"].dt.strftime("%Y-%m-%d"))
    blocks.append(B.section("Otwarte pozycje", [B.table(op, [
        ("symbol", "Symbol", "text"), ("type", "Typ", "text"), ("volume", "Wolumen", "num", 4),
        ("open_time", "Otwarcie", "text"), ("open_price", "Cena otw.", "num", 2), ("gross_pl", "Wynik", "pln", 2)], limit=10)],
        open_=False))
    cl = exp.closed.sort_values("close_time", ascending=False).head(300)
    cl = cl.assign(open_time=cl["open_time"].dt.strftime("%Y-%m-%d"), close_time=cl["close_time"].dt.strftime("%Y-%m-%d"))
    blocks.append(B.section("Zamknięte pozycje (ostatnie 300)", [B.table(cl, [
        ("symbol", "Symbol", "text"), ("type", "Typ", "text"), ("volume", "Wolumen", "num", 4),
        ("open_time", "Otwarcie", "text"), ("close_time", "Zamknięcie", "text"), ("gross_pl", "Wynik", "pln", 2)], limit=10)],
        open_=False))
    ca = exp.cash.sort_values("time", ascending=False).head(300)
    ca = ca.assign(time=ca["time"].dt.strftime("%Y-%m-%d"))
    blocks.append(B.section("Operacje gotówkowe (ostatnie 300)", [B.table(ca, [
        ("type_raw", "Typ", "text"), ("time", "Data", "text"), ("symbol", "Symbol", "text"), ("amount", "Kwota", "pln", 2),
        ("comment", "Komentarz", "text")], limit=10)], open_=False))
    return {"title": "Wczytane dane", "blocks": blocks}
