"""Ekrany działu Ryzyko: VaR/CVaR, korelacje i udział w ryzyku, ekspozycje, czynniki, CFD."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import cfd, factors, risk
from server import blocks as B
from server.tooltips import TOOLTIPS

NO_POSITIONS = "Brak otwartych pozycji z historią notowań - nie ma czego mierzyć."


def _inputs(ctx):
    r, exp, notes = ctx.risk_inputs()
    ok = len(exp) > 0 and len(r) >= 30
    return r, exp, notes, ok


def _names(ctx, index) -> list[str]:
    n = ctx.pf.meta["name"]
    return [str(n.get(i, i)) for i in index]


def var(ctx, p) -> dict:
    r, exp, notes, ok = _inputs(ctx)
    if not ok:
        return {"title": "VaR", "blocks": [B.note(NO_POSITIONS)]}
    equity = float(ctx.pf.equity.iloc[-1])
    tbl = ctx.memo("var_table", lambda: risk.var_table(r, exp, equity))

    def pick(method, level, horizon, col):
        row = tbl[(tbl["Metoda"] == method) & (tbl["Poziom ufności"] == level) & (tbl["Horyzont (dni)"] == horizon)]
        return float(row[col].iloc[0])

    def tile(label, level, horizon, col, tip):
        v = pick("Historyczny", level, horizon, col)
        return B.metric(label, v, "pln", tip=tip, note=B.f_pct(v / equity, 2) + " kapitału")

    pnl = risk.pnl_series(r, exp)
    v95 = pick("Historyczny", 0.95, 1, "VaR")
    cv95 = pick("Historyczny", 0.95, 1, "CVaR")
    show = tbl.assign(level=(tbl["Poziom ufności"] * 100).round().astype(int).astype(str) + "%",
                      horizon=tbl["Horyzont (dni)"].map(lambda h: "1 dzień" if h == 1 else f"{h} dni"))
    spread = pick("Monte Carlo", 0.99, 1, "VaR") / max(pick("Parametryczny", 0.99, 1, "VaR"), 1e-9)
    blocks = [
        B.statement(f"Raz na 20 sesji strata przekracza {B.f_pln(v95)}. W takie dni wynosi średnio {B.f_pln(cv95)}.",
                    f"Dla dzisiejszego składu portfela, na historii {len(r)} sesji."),
        B.metrics([
            tile("VaR 95% na 1 dzień", 0.95, 1, "VaR", "VaR"),
            tile("CVaR 95% na 1 dzień", 0.95, 1, "CVaR", "CVaR"),
            tile("VaR 99% na 1 dzień", 0.99, 1, "VaR", "VaR"),
            tile("CVaR 99% na 1 dzień", 0.99, 1, "CVaR", "CVaR"),
            tile("VaR 95% na 10 dni", 0.95, 10, "VaR", "VaR"),
            tile("VaR 99% na 10 dni", 0.99, 10, "VaR", "VaR"),
        ], "Metoda historyczna"),
        B.chart(B.hist(pnl.values, 40, "pln", 0, markers=[{"x": -v95, "label": "VaR 95%"}]),
                "Rozkład dziennego wyniku dzisiejszego portfela"),
        B.table(show, [("Metoda", "Metoda", "text"), ("level", "Poziom", "text"), ("horizon", "Horyzont", "text"),
                       ("VaR", "VaR", "pln"), ("CVaR", "CVaR", "pln"),
                       ("VaR % kapitału", "VaR %", "pct", 2), ("CVaR % kapitału", "CVaR %", "pct", 2)],
                title="Trzy metody obok siebie", limit=12,
                caption="Duża różnica między metodami oznacza grube ogony: rzadkie dni są gorsze, niż zakłada rozkład normalny."),
        B.bullets([
            "**Historyczny** - " + TOOLTIPS["VaR historyczny"],
            "**Parametryczny** - " + TOOLTIPS["VaR parametryczny"],
            "**Monte Carlo** - " + TOOLTIPS["VaR Monte Carlo"] + " Gdy dane pokazują grube ogony, losowanie odbywa się "
            "z rozkładu t-Studenta" + (f" - dlatego VaR 99% wychodzi tu {spread:.2f}x wyższy niż parametryczny.".replace(".", ",", 1)
                                       if spread > 1.03 else "."),
        ], "Czym różnią się metody"),
    ]
    if notes:
        blocks.insert(1, B.bullets(notes, "Uwagi do obliczeń"))
    return {"title": "VaR", "blocks": blocks}


def correlation(ctx, p) -> dict:
    r, exp, notes, ok = _inputs(ctx)
    if not ok:
        return {"title": "Korelacje", "blocks": [B.note(NO_POSITIONS)]}
    equity = float(ctx.pf.equity.iloc[-1])
    cov = risk.covariance(r)
    corr = risk.correlation_matrix(r, 90)
    enb = risk.effective_number_of_bets(exp, cov)
    rc = risk.risk_contributions(exp, cov)
    top = rc.head(12)
    paired = pd.DataFrame({"Udział w ryzyku": top["risk_share"].values, "Udział w ekspozycji": top["weight"].abs().values},
                          index=_names(ctx, top.index))
    roll = ctx.memo("rolling_corr", lambda: risk.rolling_average_correlation(r, 90, step=5))
    lead_name = _names(ctx, [rc.index[0]])[0]
    blocks = [
        B.statement(f"{len(exp)} pozycji zachowuje się jak {B.f_num(enb, 1)} niezależnego zakładu.",
                    f"Najwięcej wahań dokłada {lead_name}: {B.f_pct(rc['risk_share'].iloc[0], 0)} ryzyka przy "
                    f"{B.f_pct(abs(rc['weight'].iloc[0]), 0)} ekspozycji."),
        B.metrics([
            B.metric("Efektywna liczba zakładów", enb, "num", digits=1),
            B.metric("Średnia korelacja (90 sesji)", risk.average_correlation(corr), "num", tip="Średnia korelacja"),
            B.metric("Współczynnik dywersyfikacji", risk.diversification_ratio(exp, cov), "num"),
            B.metric("Zmienność dzisiejszego portfela", risk.portfolio_volatility(exp, cov) / equity, "pct", tip="Zmienność"),
        ]),
        B.chart(B.hbar_grouped(paired, "pct", 0, colors={"Udział w ryzyku": 0, "Udział w ekspozycji": "neutral"}),
                "Ile pozycja waży, a ile dokłada do wahań",
                "Ujemny udział w ryzyku oznacza pozycję, która zmniejsza wahania portfela, np. krótki CFD zabezpieczający akcje."),
    ]
    if corr.shape[0] >= 2:
        c = corr.copy()
        c.index, c.columns = list(corr.index), list(corr.columns)
        blocks.append(B.chart(B.heatmap(c, "num", 2, diverging=True), "Macierz korelacji, ostatnie 90 sesji",
                              "Kolejność kolumn jest taka sama jak wierszy. Niebieski: pozycje chodzą razem, czerwony: w przeciwne strony."))
    if len(roll):
        blocks.append(B.chart(B.line(roll.to_frame("Średnia korelacja"), "num", 2), "Średnia korelacja par w czasie",
                              "Gdy rośnie, pozycje zaczynają zachowywać się jak jedna. Dywersyfikacja znika zwykle wtedy, "
                              "gdy jest najbardziej potrzebna."))
    full = rc.copy()
    full.insert(0, "name", _names(ctx, full.index))
    blocks.append(B.table(full, [("name", "Pozycja", "text"), ("risk_share", "Udział w ryzyku", "pct", 1),
                                 ("weight", "Udział w ekspozycji", "pct", 1), ("standalone_vol", "Zmienność własna", "pct", 1),
                                 ("exposure", "Ekspozycja", "pln")], title="Udział pozycji w ryzyku", limit=8))
    return {"title": "Korelacje", "blocks": blocks}


def exposure(ctx, p) -> dict:
    pf = ctx.pf
    equity, cash = float(pf.equity.iloc[-1]), float(pf.cash.iloc[-1])
    exp = pf.current_exposure()
    if not len(exp):
        return {"title": "Ekspozycje", "blocks": [B.note("Portfel jest w całości w gotówce.")]}
    conc = risk.concentration(exp)
    blocks = [
        B.metrics([
            B.metric("Top 5 pozycji", conc["top5"], "pct", tip="Top 5"),
            B.metric("Największa pozycja", conc["top1"], "pct", tip="Jaką część zainwestowanych środków stanowi największa pozycja."),
            B.metric("HHI", conc["hhi"], "num", digits=3),
            B.metric("Równoważna liczba równych pozycji", conc["effective_n"], "num", digits=1,
                     tip="Odwrotność HHI: tylu równych pozycji odpowiada koncentracja Twojego portfela."),
            B.metric("Gotówka", cash / equity if equity else None, "pct", tip="Udział gotówki w wartości portfela."),
        ], "Koncentracja"),
    ]
    has_short = False
    classes = {"STOCK": "Akcje", "ETF": "ETF-y", "CFD": "CFD (nominał)"}
    for col, title in (("sector", "Sektory"), ("country", "Kraje"), ("asset_class", "Klasy aktywów")):
        e = risk.exposure_by(exp, pf.meta, col, equity, cash, "Gotówka").rename(index=classes)
        has_short = has_short or bool((e["short"] < 0).any())
        blocks.append(B.chart(B.hbar(e["net_pct"].sort_values(ascending=False), "pct", 1, signed=True),
                              f"{title}: ekspozycja netto jako % kapitału"))
    if has_short or exp.abs().sum() + cash > equity * 1.02:
        blocks.append(B.note("Wartości ujemne to pozycje krótkie. Przy CFD ekspozycją jest wartość nominalna, dlatego suma "
                             "może przekraczać 100% kapitału."))
    cur = risk.currency_exposure(pf.value.iloc[-1], pf.meta, ctx.cash_by_currency(), equity, ctx.base)
    blocks.append(B.chart(B.hbar(cur["share"], "pct", 1, signed=True), "Waluty: co zależy od którego kursu",
                          "Liczy się waluta notowań akcji i waluta gotówki na każdym rachunku. "
                          "Nominał CFD nie jest ekspozycją walutową - od kursu zależy tylko niezrealizowany wynik. ETF na akcje "
                          "globalne notowany w EUR lub USD ma w praktyce ryzyko walut spółek z indeksu."))
    liq = risk.liquidity_table(pf.qty.iloc[-1][~pf.is_cfd], ctx.market.volumes)
    if len(liq):
        liq.insert(0, "name", _names(ctx, liq.index))
        blocks.append(B.table(liq, [("name", "Pozycja", "text"), ("days_to_exit", "Dni do zamknięcia", "num", 3),
                                    ("qty", "Ilość", "num", 2), ("adv", "Śr. dzienny wolumen", "int")],
                              title="Płynność", caption=TOOLTIPS["Dni do zamknięcia"], limit=8))
    return {"title": "Ekspozycje", "blocks": blocks}


def factor_exposure(ctx, p) -> dict:
    pf = ctx.pf
    freq = p.str("freq", "W", {"W", "D"})
    ctl = B.controls([B.control("freq", "segmented", "Dane", freq, [("W", "Tygodniowe"), ("D", "Dzienne")],
                                tip="Tygodniowe łagodzą problem różnych godzin sesji w Polsce i USA.")])
    try:
        ff = ctx.memo("ff", lambda: ctx.provider.ff_factors(pf.dates[0], pf.dates[-1]))
        res = factors.factor_regression(pf.returns, ctx.market.fx["USD"], ff, freq)
    except Exception as exc:
        return {"title": "Czynniki", "blocks": [ctl, B.note(f"Nie udało się policzyć ekspozycji czynnikowej: {exc}", "warn")]}
    t = res["table"]
    betas = pd.Series(t["beta"].values, index=t["label"].tolist())
    tab = t.assign(sig=np.where(t["significant"], "tak", "nie"))
    blocks = [
        ctl,
        B.bullets(factors.interpret(res), "Co to znaczy dla Twojego portfela"),
        B.chart(B.hbar(betas, "num", 2, signed=True), "Ładunki czynnikowe"),
        B.metrics([
            B.metric("Beta rynkowa", float(t.at["Mkt-RF", "beta"]), tip="Beta"),
            B.metric("Alfa (rocznie)", res["alpha_annual"], "pct", tip="Alfa"),
            B.metric("R²", res["r2"]),
            B.metric("Obserwacje", res["n"], "int", tip="Liczba okresów (tygodni lub dni) użytych w regresji."),
        ]),
        B.table(tab, [("label", "Czynnik", "text"), ("beta", "Ładunek", "num", 2), ("t", "Statystyka t", "num", 1),
                      ("sig", "Istotny", "text")], caption="Istotny = |t| ≥ 2, czyli odróżnialny od zera."),
        B.note(f"Czynniki z biblioteki Kennetha Frencha (rynek USA), okres {res['start'].date()} - {res['end'].date()}. "
               "Zwroty portfela przeliczone na USD. Dla spółek z GPW czynniki amerykańskie są tylko przybliżeniem."
               + (" W trybie demo czynniki są syntetyczne." if ctx.is_demo else "")),
    ]
    return {"title": "Czynniki", "blocks": blocks}


def cfd_screen(ctx, p) -> dict:
    pf, exp = ctx.pf, ctx.export
    s = cfd.leverage_summary(pf, exp.open)
    sw = cfd.swap_costs(pf, exp.cash, exp.closed, exp.open)
    has_open = s["cfd_nominal_gross"] > 0
    blocks = []
    if has_open:
        blocks.append(B.statement(
            f"Pozycje CFD zostaną zamknięte przez brokera po ruchu o {B.f_pct(s['distance_to_stop_out'])} przeciwko Tobie.",
            f"To strata {B.f_pln(s['loss_to_stop_out'])} na CFD. Zabezpieczeniem jest gotówka i wynik na CFD "
            f"({B.f_pln(s['collateral'])}); wartość akcji nie jest tu liczona."))
    else:
        blocks.append(B.note("Brak otwartych pozycji CFD - dźwignia wynika wyłącznie z akcji i ETF-ów."))
    blocks.append(B.metrics([
        B.metric("Efektywna dźwignia", s["gross_leverage"], "x"),
        B.metric("Nominał CFD", s["cfd_nominal_gross"], "pln", tip="Suma wartości nominalnych otwartych pozycji CFD (długich i krótkich)."),
        B.metric("Wymagany depozyt", s["margin_required"], "pln", tip="Kwota zablokowana przez brokera jako zabezpieczenie otwartych CFD."),
        B.metric("Poziom depozytu", s["margin_level"] if has_open else None, "pct", digits=0),
        B.metric("Odległość do stop outu", s["distance_to_stop_out"] if has_open else None, "pct", tip="Odległość do margin call"),
        B.metric("Roczny koszt swapów", sw["annual_rate"], "pct", digits=2,
                 note=(B.f_pln(sw["current_annual_cost"]) + " rocznie przy obecnym nominale") if has_open else None),
        B.metric("Swapy zapłacone dotąd", sw["total"], "pln", tip="Suma punktów swapowych od początku historii rachunku."),
    ]))
    if has_open:
        t = cfd.open_cfd_table(pf, exp.open)
        blocks.append(B.table(t, [("name", "Pozycja", "text"), ("side", "Kierunek", "text"), ("nominal", "Nominał", "pln"),
                                  ("upl", "Wynik", "pln"), ("margin", "Depozyt", "pln"), ("leverage", "Dźwignia", "x", 1),
                                  ("lots", "Loty", "num", 2), ("swap", "Swap", "pln", 2)], title="Otwarte pozycje CFD"))
    if len(sw["by_symbol"]):
        by = sw["by_symbol"].copy()
        by.index = _names(ctx, by.index)
        blocks.append(B.chart(B.hbar(by, "pln", 0, signed=True), "Swapy wg instrumentu (od początku)"))
    return {"title": "CFD", "blocks": blocks}
