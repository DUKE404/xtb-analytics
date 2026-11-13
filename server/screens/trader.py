"""Ekrany działu Trader: statystyki transakcji, wzorce zachowań, wielkość pozycji, koszty, reżimy rynku."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import performance as perf
from core import regimes, trader
from server import blocks as B

SCOPES = [("all", "Wszystko"), ("cfd", "CFD"), ("stock", "Akcje i ETF")]


def _trades(ctx) -> pd.DataFrame:
    return ctx.memo("trades", lambda: trader.trades_table(ctx.export.closed, ctx.pf.meta, ctx.market.fx))


def _scoped(ctx, p):
    scope = p.str("scope", "all", {k for k, _ in SCOPES})
    t = _trades(ctx)
    if scope == "cfd":
        t = t[t["asset_class"] == "CFD"]
    elif scope == "stock":
        t = t[t["asset_class"] != "CFD"]
    return scope, t, B.controls([B.control("scope", "segmented", "Zakres", scope, SCOPES)])


def _names(ctx, index) -> list[str]:
    n = ctx.pf.meta["name"]
    return [str(n.get(i, i)) for i in index]


def _hold(days: float) -> str:
    if days != days:
        return "–"
    if days < 1:
        return f"{B.f_num(days * 24, 1)} godz."
    return f"{B.f_num(days, 1)} dnia" if days < 2 else f"{B.f_num(days, 1)} dni"


def stats(ctx, p) -> dict:
    scope, t, ctl = _scoped(ctx, p)
    if len(t) < 3:
        return {"title": "Statystyki", "blocks": [ctl, B.note("Za mało zamkniętych transakcji w tym zakresie.")]}
    s = trader.summary(t)
    if s["payoff"] == s["payoff"]:
        rel = (f"średni zysk jest {B.f_num(s['payoff'], 1)} raza większy od średniej straty" if s["payoff"] >= 1
               else f"średnia strata jest {B.f_num(1 / s['payoff'], 1)} raza większa od średniego zysku")
        lead = f"Zarabiasz na {B.f_pct(s['hit_rate'], 0)} transakcji, a {rel}."
    else:
        lead = f"Zarabiasz na {B.f_pct(s['hit_rate'], 0)} transakcji."
    verdict = ("Razem to dodatnia przewaga" if s["expectancy"] > 0 else "Razem to ujemna przewaga") + \
        f": {B.f_pln(s['expectancy'])} na transakcję."
    cum = t.set_index("close_time")["net_pl"].cumsum()
    cum = cum[~cum.index.duplicated(keep="last")].to_frame("Skumulowany wynik")
    wd = trader.by_weekday(t)
    hr = trader.by_hour(t)
    sym = trader.by_symbol(t)
    sym.insert(0, "name", _names(ctx, sym.index))
    blocks = [
        ctl,
        B.statement(lead, verdict),
        B.metrics([
            B.metric("Liczba transakcji", s["n"], "int", tip="Liczba zamkniętych pozycji w wybranym zakresie."),
            B.metric("Hit rate", s["hit_rate"], "pct", digits=0),
            B.metric("Średni zysk", s["avg_win"], "pln"),
            B.metric("Średnia strata", s["avg_loss"], "pln"),
            B.metric("Payoff ratio", s["payoff"]),
            B.metric("Profit factor", s["profit_factor"]),
            B.metric("Expectancy", s["expectancy"], "pln", sign=True),
            B.metric("Wynik netto", s["total"], "pln", sign=True, tip="Suma wyników wszystkich transakcji po prowizjach i swapach."),
        ]),
        B.chart(B.line(cum, "pln", 0, delta="abs"), "Skumulowany wynik zamkniętych transakcji"),
        B.metrics([
            B.metric("Trzymanie zyskownych", _hold(s["hold_win"]), "text", tip="Czas trzymania"),
            B.metric("Trzymanie stratnych", _hold(s["hold_loss"]), "text", tip="Czas trzymania"),
            B.metric("Najdłuższa seria strat", s["max_losing_streak"], "int", tip="Ile stratnych transakcji z rzędu zdarzyło się najwięcej."),
            B.metric("Najlepsza transakcja", s["best"], "pln", tip="Najwyższy wynik pojedynczej transakcji."),
            B.metric("Najgorsza transakcja", s["worst"], "pln", tip="Najniższy wynik pojedynczej transakcji."),
        ], "Czas i skrajności"),
        B.chart(B.hbar(wd["net"], "pln", 0, signed=True), "Wynik wg dnia tygodnia otwarcia"),
        B.chart(B.bars(pd.DataFrame({"Wynik": hr["net"].values}, index=[f"{h}" for h in hr.index]), "pln", 0),
                "Wynik wg godziny otwarcia", "Godzina z raportu XTB (czas serwera brokera)."),
        B.table(sym, [("name", "Instrument", "text"), ("net", "Wynik", "pln"), ("n", "Transakcje", "int"),
                      ("hit_rate", "Hit rate", "pct", 0), ("avg", "Średnio", "pln")], title="Wg instrumentu", limit=8),
    ]
    return {"title": "Statystyki", "blocks": blocks}


def patterns(ctx, p) -> dict:
    scope, t, ctl = _scoped(ctx, p)
    if len(t) < 8:
        return {"title": "Wzorce", "blocks": [ctl, B.note("Do szukania wzorców potrzeba co najmniej 8 zamkniętych transakcji.")]}
    size = trader.size_after_loss(t)
    disp = trader.disposition_effect(t)
    early = trader.early_exit(t, ctx.market.prices)
    found = [name for name, r in (("większe pozycje po stracie", size), ("strat nie ucinasz tak szybko jak zysków", disp),
                                  ("zyski realizowane za wcześnie", early)) if r["detected"]]
    lead = ("W historii transakcji widać: " + "; ".join(found) + "." if found
            else "W historii transakcji nie widać żadnego z trzech typowych błędów.")

    def verdict(r):
        return "wykryto" if r["detected"] else "nie wykryto"

    def pval(r):
        return "–" if r["p_value"] != r["p_value"] else ("poniżej 0,001" if r["p_value"] < 0.001 else B.f_num(r["p_value"], 3))

    blocks = [
        ctl,
        B.statement(lead, "Wzorzec uznaję za wykryty dopiero wtedy, gdy różnica jest wyraźna i mało prawdopodobne, żeby była przypadkiem."),
        B.metrics([
            B.metric("Werdykt", verdict(size), "text",
                     tip="Sprawdzam, czy pozycja otwarta po stratnej transakcji jest większa niż po zyskownej. Wielkość "
                         "porównuję z typową wielkością na tym samym instrumencie. Podbijanie stawki po stracie to próba "
                         "szybkiego odrobienia - najkrótsza droga do dużego obsunięcia."),
            B.metric("Wielkość po stracie", size["after_loss"], "x", tip="Średnia wielkość pozycji po stratnej transakcji, jako krotność typowej wielkości na danym instrumencie."),
            B.metric("Wielkość po zysku", size["after_win"], "x", tip="Średnia wielkość pozycji po zyskownej transakcji, jako krotność typowej wielkości."),
            B.metric("Stosunek", size["ratio"], "x", tip="Powyżej 1,2 przy małym prawdopodobieństwie przypadku uznaję za wzorzec.",
                     note=f"prawdopodobieństwo przypadku: {pval(size)}"),
        ], "Większe pozycje po stracie"),
        B.metrics([
            B.metric("Werdykt", verdict(disp), "text",
                     tip="Efekt dyspozycji: zyski zamykane szybko („żeby nie uciekły”), a straty trzymane w nadziei na "
                         "odbicie. Skutek to małe zyski i duże straty, nawet przy wysokiej skuteczności."),
            B.metric("Typowy czas trzymania zysku", _hold(disp["hold_win"]), "text", tip="Mediana czasu trzymania transakcji zyskownych."),
            B.metric("Typowy czas trzymania straty", _hold(disp["hold_loss"]), "text", tip="Mediana czasu trzymania transakcji stratnych."),
            B.metric("Stosunek", disp["ratio"], "x", tip="Straty trzymane co najmniej 1,5 raza dłużej niż zyski uznaję za wzorzec.",
                     note=f"prawdopodobieństwo przypadku: {pval(disp)}"),
        ], "Straty trzymane dłużej niż zyski"),
        B.metrics([
            B.metric("Werdykt", verdict(early) if early["n"] >= 6 else "za mało danych", "text",
                     tip="Dla każdej zyskownej transakcji sprawdzam, co cena zrobiła przez kolejne 20 sesji po zamknięciu, "
                         "liczone w kierunku pozycji. Jeśli zwykle szła dalej na Twoją korzyść, zyski były ucinane za wcześnie."),
            B.metric("Zbadane zyskowne transakcje", early["n"], "int", tip="Transakcje zyskowne, po których jest co najmniej 20 sesji notowań."),
            B.metric("Ruch dalej na korzyść", early["share_continued"], "pct", digits=0,
                     tip="W ilu przypadkach cena po 20 sesjach była dalej po Twojej stronie niż w chwili zamknięcia."),
            B.metric("Średni dalszy ruch", early["avg_move"], "pct", digits=1, sign=True,
                     tip="Średnia zmiana ceny w kierunku pozycji przez 20 sesji po zamknięciu."),
            B.metric("Zostawione na stole", early["left_on_table"], "pln", sign=True,
                     tip="Ile dałoby trzymanie każdej zyskownej pozycji o 20 sesji dłużej. To miara skali, nie obietnica: "
                         "dłuższe trzymanie zwiększa też ryzyko."),
        ], "Zyski realizowane za wcześnie"),
        B.note("To statystyka przeszłych decyzji, nie rekomendacja. Przy kilkudziesięciu transakcjach wnioski są "
               "orientacyjne; sprawdź też osobno zakres CFD i akcji."),
    ]
    return {"title": "Wzorce", "blocks": blocks}


def sizing(ctx, p) -> dict:
    pf = ctx.pf
    equity = float(pf.equity.iloc[-1])
    t = _trades(ctx)
    s = trader.summary(t)
    returns = ctx.market.prices.pct_change().tail(120)
    symbols = [x for x in ctx.mapping["symbol"] if x in returns.columns]
    if not symbols:
        return {"title": "Wielkość pozycji", "blocks": [B.note("Brak notowań instrumentów do policzenia zmienności.")]}
    sym = p.str("symbol", symbols[0], set(symbols))
    risk_pct = p.float("risk", 1.0, 0.05, 20.0)
    stop = p.float("stop", 2.0, 0.25, 10.0)
    horizon = p.float("days", 5.0, 0.1, 250.0)
    hit = p.float("hit", round((s["hit_rate"] if s["hit_rate"] == s["hit_rate"] else 0.5) * 100, 0), 1.0, 99.0)
    payoff = p.float("payoff", round(s["payoff"], 2) if s["payoff"] == s["payoff"] else 1.0, 0.05, 50.0)

    vol = float(returns[sym].std(ddof=1))
    size = trader.volatility_position_size(equity, risk_pct / 100.0, vol, stop, horizon)
    meta = pf.meta.loc[sym] if sym in pf.meta.index else ctx.mapping.set_index("symbol").loc[sym]
    price = float(ctx.market.prices[sym].dropna().iloc[-1])
    cur = str(meta["currency"])
    fx = float(ctx.market.fx[cur].dropna().iloc[-1]) if cur in ctx.market.fx.columns else 1.0
    is_cfd = meta["asset_class"] == "CFD"
    unit_value = price * fx * (float(meta["multiplier"]) if is_cfd else 1.0)
    units = size / unit_value if unit_value > 0 else float("nan")
    full, half = trader.kelly_fraction(hit / 100.0, payoff), trader.half_kelly(hit / 100.0, payoff)
    stop_pct = stop * vol * np.sqrt(horizon)
    name = str(meta["name"])

    blocks = [
        B.statement(f"Przy ryzyku {B.f_num(risk_pct, 2).rstrip('0').rstrip(',')}% kapitału na transakcję pozycja w {name} to około {B.f_pln(size)}.",
                    f"To {B.f_pct(size / equity)} kapitału, czyli {B.f_num(units, 2 if is_cfd else 1)} {'lota' if is_cfd else 'szt.'} "
                    f"Stop wypada {B.f_pct(stop_pct)} od ceny wejścia."),
        B.controls([
            B.control("symbol", "select", "Instrument", sym, [(x, f"{ctx.mapping.set_index('symbol').at[x, 'name']} ({x})") for x in symbols]),
            B.control("risk", "number", "Ryzyko na transakcję", f"{risk_pct:g}".replace(".", ","), unit="% kapitału",
                      tip="Ile kapitału jesteś gotów stracić, jeśli zadziała stop."),
            B.control("stop", "number", "Odległość stopu", f"{stop:g}".replace(".", ","), unit="× zmienność dzienna"),
            B.control("days", "number", "Planowany czas trzymania", f"{horizon:g}".replace(".", ","), unit="dni"),
            B.control("hit", "number", "Skuteczność", f"{hit:g}".replace(".", ","), unit="%",
                      tip="Domyślnie Twój hit rate z historii."),
            B.control("payoff", "number", "Zysk / strata", f"{payoff:g}".replace(".", ","),
                      tip="Domyślnie Twój payoff ratio z historii."),
        ], submit="Przelicz", grid=True),
        B.metrics([
            B.metric("Zmienność dzienna instrumentu", vol, "pct", digits=2, tip="Odchylenie standardowe dziennych zmian ceny z ostatnich 120 sesji."),
            B.metric("Odległość stopu", stop_pct, "pct", digits=1,
                     tip="Odległość stopu × zmienność dzienna × pierwiastek z liczby dni trzymania."),
            B.metric("Wielkość pozycji", size, "pln", tip="Kapitał × ryzyko na transakcję ÷ odległość stopu.",
                     note=f"{B.f_pct(size / equity)} kapitału"),
            B.metric("Strata przy stopie", equity * risk_pct / 100.0, "pln", tip="Tyle stracisz, jeśli cena dojdzie do stopu."),
        ], "Wielkość wg zmienności"),
        B.metrics([
            B.metric("Pełny Kelly", full, "pct", digits=1, tip="Kelly"),
            B.metric("Połowa Kelly'ego", half, "pct", digits=1, tip="Kelly",
                     note=(B.f_pln(half * equity) + " ryzykowanego kapitału") if half == half else None),
        ], "Kryterium Kelly'ego"),
    ]
    if full == full and full <= 0:
        blocks.append(B.note("Przy tej skuteczności i tym stosunku zysk/strata przewaga jest ujemna - wzór Kelly'ego mówi, "
                             "żeby nie ryzykować nic.", "warn"))
    blocks.append(B.note("Kelly odnosi się do kapitału ryzykowanego w transakcji (jak pole „Ryzyko na transakcję”), a nie do "
                         "wartości pozycji. To kalkulator do własnych założeń, nie rekomendacja."))
    return {"title": "Wielkość pozycji", "blocks": blocks}


def costs(ctx, p) -> dict:
    pf, exp = ctx.pf, ctx.export
    t = _trades(ctx)
    c = trader.cost_breakdown(t, exp.open, exp.cash, pf.meta, ctx.market.fx, account_currencies=ctx.account_currencies)
    years = max(len(pf.dates) / 252.0, 1e-9)
    ratios = trader.cost_ratios(c["total"], pf.total_pnl(), float(pf.equity.mean()), years)
    items = pd.Series(c["items"]).sort_values(ascending=False)
    items = items[items.abs() > 0.005]
    share = ratios["share_of_gross"]
    lead = (f"Koszty zabrały {B.f_pln(c['total'])}, czyli {B.f_pct(share, 0)} zysku przed kosztami." if share == share
            else f"Koszty wyniosły {B.f_pln(c['total'])} przy stracie na portfelu.")
    cls_names = {"STOCK": "Akcje", "ETF": "ETF-y", "CFD": "CFD"}
    by = pd.DataFrame([{"name": cls_names.get(k, k), "turnover": v["turnover"], "spread": v["spread"], "fx": v["fx"]}
                       for k, v in c["by_class"].items()])
    blocks = [
        B.statement(lead, f"To {B.f_pct(ratios['per_year_of_equity'], 2)} średniego kapitału rocznie przy obrocie {B.f_pln(c['turnover'])}."),
        B.chart(B.hbar(items, "pln", 0), "Koszty wg rodzaju"),
        B.metrics([
            B.metric("Koszty razem", c["total"], "pln", tip="Koszty"),
            B.metric("Udział w zysku przed kosztami", share, "pct", digits=0,
                     tip="Koszty podzielone przez wynik, jaki byłby bez nich (wynik portfela + koszty)."),
            B.metric("Koszt roczny względem kapitału", ratios["per_year_of_equity"], "pct", digits=2,
                     tip="Koszty w przeliczeniu na rok, jako procent średniej wartości portfela."),
            B.metric("Obrót", c["turnover"], "pln", tip="Suma wartości wszystkich kupna i sprzedaży (dla CFD: nominał)."),
            B.metric("Podatki pobrane u źródła", c["taxes_withheld"], "pln",
                     tip="Podatek od dywidend i odsetek pobrany przez brokera. To nie koszt transakcyjny - pokazany osobno."),
        ]),
    ]
    if len(by):
        blocks.append(B.table(by, [("name", "Klasa", "text"), ("turnover", "Obrót", "pln"), ("spread", "Spread", "pln"),
                                   ("fx", "Przewalutowanie", "pln")], title="Szacunki wg klasy aktywów"))
    blocks.append(B.bullets([
        "**Prowizje i swapy** pochodzą wprost z raportu XTB.",
        "**Spread** to szacunek: połowa spreadu z tabeli tickerów (kolumna spread, w punktach bazowych) od wartości każdego "
        "kupna i sprzedaży. Jest już zawarty w cenach transakcji, więc nie odejmuj go drugi raz od wyniku.",
        "**Przewalutowanie** to szacunek 0,5% od wartości kupna i sprzedaży akcji i ETF-ów w walucie, w której nie masz "
        f"rachunku (Twoje rachunki: {', '.join(ctx.account_currencies)}). Też jest już zawarte w wyniku. Wymiana przy przelewie "
        "między własnymi rachunkami trafia do pozycji „gotówka” w atrybucji. Sprawdź aktualną stawkę w tabeli opłat XTB.",
    ], "Skąd te liczby"))
    return {"title": "Koszty", "blocks": blocks}


def regime_screen(ctx, p) -> dict:
    pf = ctx.pf
    method = p.str("method", "hmm", {"hmm", "vol"})
    k = p.int("states", 2, 2, 3)
    ctl = B.controls([
        B.control("method", "segmented", "Metoda", method, [("hmm", "Ukryty model Markowa"), ("vol", "Progi zmienności")],
                  tip="HMM sam znajduje stany rynku różniące się zmiennością. Progi zmienności to prostsza reguła: "
                      "rynek jest nerwowy, gdy jego zmienność z 21 sesji jest wysoka na tle historii."),
        B.control("states", "segmented", "Liczba reżimów", k, [(2, "2"), (3, "3")]),
    ])
    levels = ctx.market.benchmarks_base.get(p.bench)
    if levels is None or levels.dropna().empty:
        return {"title": "Reżimy", "blocks": [ctl, B.note("Brak notowań benchmarku do wyznaczenia reżimów rynku.", "warn")]}
    bench_full = levels.dropna().pct_change().dropna()
    try:
        states_full = ctx.memo(("regimes", method, k, p.bench), lambda: (
            regimes.hmm_regimes(bench_full, k) if method == "hmm" else regimes.volatility_regimes(bench_full, k)))
    except ValueError as exc:
        return {"title": "Reżimy", "blocks": [ctl, B.note(str(exc), "warn")]}
    states = states_full.reindex(pf.dates).ffill().bfill().astype(int)
    port = regimes.regime_stats(pf.returns, states, k)
    bench = regimes.regime_stats(ctx.bench_series(p.bench), states, k)
    labels = regimes.LABELS[k]
    now = labels[int(states.iloc[-1])]
    calm, nervous = port.iloc[0], port.iloc[-1]
    idx = perf.twr_index(ctx.bench_series(p.bench)).to_frame(ctx.bench_label(p.bench))
    spec = B.line(idx, "num", 0, max_points=5000)
    spec["spans"] = [list(s) for s in regimes.spans(states, k - 1)]
    tab = port.reset_index().merge(bench.reset_index()[["regime", "ann_return", "volatility"]], on="regime", suffixes=("", "_b"))

    def describe(row):
        return ("zarabiał " if row["ann_return"] >= 0 else "tracił ") + B.f_pct(abs(row["ann_return"]), 0) + " rocznie"

    blocks = [
        ctl,
        B.statement(f"W nerwowym rynku portfel {describe(nervous)}, w spokojnym {describe(calm)}.",
                    f"Dziś rynek jest w reżimie: {now}. Nerwowy rynek to {B.f_pct(nervous['share'], 0)} dni w Twojej historii."),
        B.chart(spec, f"{ctx.bench_label(p.bench)}: zacieniowane okresy nerwowego rynku",
                "Reżimy wyznaczam na benchmarku wybranym w ustawieniach, na całej dostępnej historii notowań."),
        B.table(tab, [("regime", "Reżim", "text"), ("share", "Udział dni", "pct", 0), ("ann_return", "Portfel rocznie", "pct", 1),
                      ("volatility", "Zmienność portfela", "pct", 1), ("sharpe", "Sharpe", "num", 2),
                      ("worst_day", "Najgorszy dzień", "pct", 1), ("ann_return_b", "Benchmark rocznie", "pct", 1),
                      ("volatility_b", "Zmienność benchmarku", "pct", 1)], title="Portfel w każdym reżimie"),
        B.note("Zwroty roczne w reżimie to średni dzienny zwrot z tych dni pomnożony przez 252 - pokazują tempo, a nie "
               "wynik osiągnięty w kalendarzowym roku. Reżimy są wyznaczone z perspektywy czasu, więc nie nadają się do "
               "prognozowania."),
    ]
    return {"title": "Reżimy", "blocks": blocks}
