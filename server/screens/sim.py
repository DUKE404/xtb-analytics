"""Ekrany działu Symulacje: stress testy, Monte Carlo, "co jeśli", optymalizacja."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import montecarlo as mc
from core import optimize as opt
from core import risk, stress, whatif
from data.mapping import auto_map
from server import blocks as B

CUSTOM = "Własny scenariusz"
CUSTOM_DEFAULTS = {"USDPLN": 15.0, "NDX": -25.0}     # przykład ze specyfikacji: USD/PLN +15%, Nasdaq -25%


def _names(ctx, index) -> list[str]:
    n = ctx.pf.meta["name"]
    return [str(n.get(i, i)) for i in index]


def _years(n: int) -> str:
    return "1 rok" if n == 1 else f"{n} lata" if n in (2, 3, 4) else f"{n} lat"


# --------------------------------------------------------------------------- stress testy

def _betas(ctx) -> pd.Series:
    def build():
        local = ctx.market.prices.pct_change()
        proxy = ctx.market.benchmarks_local.pct_change()
        return stress.estimate_betas(local, proxy, stress.factor_map(ctx.pf.meta, ctx.pf.current_exposure().index, ctx.base))
    return ctx.memo("stress_betas", build)


def _fx_remark(ctx) -> str:
    if ctx.base == "PLN":
        return "Osłabienie złotego w kryzysach częściowo amortyzuje spadki zagranicznych akcji."
    return (f"Wynik liczony w {ctx.base}: osłabienie złotego w kryzysach obniża wartość polskich akcji i gotówki "
            f"w PLN widzianą w {ctx.base}.")


def stress_screen(ctx, p) -> dict:
    pf = ctx.pf
    exp = pf.current_exposure()
    if not len(exp):
        return {"title": "Stres", "blocks": [B.note("Portfel jest w całości w gotówce - scenariusze rynkowe go nie ruszają.")]}
    equity = float(pf.equity.iloc[-1])
    use_beta = p.bool("beta", True)
    betas = _betas(ctx) if use_beta else None
    custom = {f: p.float(f"s_{f}", CUSTOM_DEFAULTS.get(f, 0.0), -95.0, 300.0) / 100.0 for f in stress.FACTORS}
    scenarios = {k: v["shocks"] for k, v in stress.HISTORICAL.items()} | {CUSTOM: custom}
    cash = ctx.cash_by_currency()
    t = stress.run_scenarios(exp, pf.meta, equity, scenarios, betas, ctx.base, cash)
    hist = t.drop(index=CUSTOM)
    worst = hist["pnl"].idxmin()
    detail = p.str("detail", CUSTOM, set(scenarios))

    shocks_tbl = pd.DataFrame({k: pd.Series(v) for k, v in scenarios.items()}).reindex(list(stress.FACTORS))
    shocks_tbl.insert(0, "factor", [stress.FACTORS[f] for f in shocks_tbl.index])
    d = stress.apply_scenario(exp, pf.meta, scenarios[detail], betas, ctx.base, cash)
    d.insert(0, "name", _names(ctx, d.index))
    d["factor"] = ["kurs waluty" if f == stress.CASH_FACTOR else stress.FACTORS.get(f, "poza modelem") for f in d["eq_factor"]]
    d = d.sort_values("pnl")
    outside = d.index[d["eq_factor"] == ""].tolist()

    ctl = [B.control(f"s_{f}", "number", label, f"{custom[f] * 100:g}".replace(".", ","), unit="%")
           for f, label in stress.FACTORS.items()]
    ctl.append(B.control("beta", "toggle", "Skaluj szok betą każdej spółki", use_beta,
                         tip="Spółka o becie 1,5 względem swojego indeksu dostaje 1,5 raza większy szok niż indeks."))
    blocks = [
        B.statement(f"Powtórka scenariusza „{worst}” zabrałaby dzisiejszemu portfelowi {B.f_pln(abs(hist.at[worst, 'pnl']))}.",
                    f"To {B.f_pct(abs(hist.at[worst, 'pct']))} kapitału. " + _fx_remark(ctx)),
        B.chart(B.hbar(t["pnl"], "pln", 0, signed=True), "Wynik portfela w scenariuszach"),
        B.table(t.reset_index().rename(columns={"index": "name"}),
                [("name", "Scenariusz", "text"), ("pnl", "Wynik", "pln"), ("pct", "% kapitału", "pct", 1)]),
        B.section(CUSTOM, [B.controls(ctl, submit="Przelicz scenariusz", grid=True),
                           B.note("Wpisz zmianę każdego czynnika w procentach, np. USD/PLN 15 i Nasdaq 100 −25. "
                                  "Puste lub 0 oznacza brak ruchu.")], open_=True),
        B.controls([B.control("detail", "select", "Pokaż pozycje w scenariuszu", detail, [(k, k) for k in scenarios])]),
        B.table(d, [("name", "Pozycja", "text"), ("pnl", "Wynik", "pln"), ("change", "Zmiana", "pct", 1),
                    ("exposure", "Ekspozycja", "pln"), ("factor", "Czynnik", "text"), ("beta", "Beta", "num", 2)],
                title=f"Pozycje: {detail}", limit=10),
        B.section("Jakie ruchy rynków przyjęto", [
            B.table(shocks_tbl, [("factor", "Czynnik", "text")] + [(k, k.split(",")[0].replace("Kryzys finansowy ", "").replace("Podwyżki stóp ", "Stopy "), "pct", 0) for k in scenarios]),
            B.note("Scenariusze historyczne to przybliżone ruchy od szczytu do dołka: " +
                   "; ".join(f"{k} ({v['period']})" for k, v in stress.HISTORICAL.items()) +
                   ". Wartości są zaokrąglone i służą do oceny skali ryzyka, nie do odtwarzania historii co do punktu."),
        ], open_=False),
    ]
    if outside:
        blocks.append(B.note("Poza modelem (wynik 0 w każdym scenariuszu): " + ", ".join(_names(ctx, outside)) + ".", "warn"))
    return {"title": "Stres", "blocks": blocks}


# --------------------------------------------------------------------------- Monte Carlo

def _portfolio_returns(ctx) -> pd.Series:
    r, exp, _ = ctx.risk_inputs()
    return risk.pnl_series(r, exp) / float(ctx.pf.equity.iloc[-1])


def montecarlo_screen(ctx, p) -> dict:
    pf = ctx.pf
    equity = float(pf.equity.iloc[-1])
    r, exp, _ = ctx.risk_inputs()
    if not len(exp) or len(r) < 60:
        return {"title": "Monte Carlo", "blocks": [B.note("Za mało historii notowań otwartych pozycji, żeby symulować przyszłość.")]}
    years = p.int("years", 5, 1, 10)
    years = years if years in (1, 5, 10) else 5
    method = p.str("method", "bootstrap", {"bootstrap", "t"})
    use_hist = p.bool("use_hist", False)
    ret = p.float("ret", 6.0, -20.0, 40.0)
    contrib = p.float("contrib", 0.0, 0.0, 1e6)
    target = p.float("target", round(equity * 1.5, -3), 0.0, 1e10)
    dd = p.float("dd", 20.0, 1.0, 95.0)

    port = _portfolio_returns(ctx)
    key = ("mc", method, None if use_hist else round(ret, 4), round(contrib, 2))
    sim = ctx.memo(key, lambda: mc.simulate(port, equity, years=10, n_paths=2000, method=method,
                                            annual_return=None if use_hist else ret / 100.0,
                                            monthly_contribution=contrib))
    m = years * 12
    pct = mc.percentiles(sim, upto_month=m)
    dates = [(pf.as_of + pd.DateOffset(months=i)).strftime("%m.%Y") for i in range(m + 1)]
    v = mc.at_horizon(sim, years)
    p5, p50, p95 = (float(np.percentile(v, q)) for q in (5, 50, 95))
    invested = float(sim["contributed"][m])
    ht = mc.horizon_table(sim)
    ht["label"] = ht["horizon"].map(_years)
    mu_annual = (1 + sim["mu_daily"]) ** 252 - 1

    blocks = [
        B.statement(f"Za {_years(years)} portfel w połowie scenariuszy jest wart ponad {B.f_pln(p50)}.",
                    f"W co dwudziestym scenariuszu mniej niż {B.f_pln(p5)}, w co dwudziestym więcej niż {B.f_pln(p95)}."),
        B.chart(B.fan(dates, [("90% scenariuszy", pct["p5"], pct["p95"]), ("50% scenariuszy", pct["p25"], pct["p75"])],
                      pct["p50"], "pln", 0, lines=[{"name": "Wpłacony kapitał", "values": [float(x) for x in sim["contributed"][:m + 1]],
                                                    "color": "neutral"}]),
                "Rozkład wartości portfela w czasie",
                "Jaśniejsze pasmo obejmuje 90% scenariuszy, ciemniejsze środkowe 50%. Linia to mediana."),
        B.metrics([
            B.metric("Mediana", p50, "pln", tip="Mediana"),
            B.metric("Scenariusz pesymistyczny", p5, "pln", tip="5. percentyl: tylko 1 na 20 symulowanych przyszłości kończy się gorzej."),
            B.metric("Scenariusz optymistyczny", p95, "pln", tip="95. percentyl: tylko 1 na 20 symulowanych przyszłości kończy się lepiej."),
            B.metric(f"Szansa na {B.f_pln(target)}", mc.prob_target(sim, target, years), "pct", digits=0, tip="Prawdopodobieństwo celu"),
            B.metric(f"Ryzyko obsunięcia ponad {dd:g}%".replace(".", ","), mc.prob_drawdown(sim, dd / 100.0, years), "pct", digits=0,
                     tip="Prawdopodobieństwo obsunięcia"),
            B.metric("Ryzyko, że będzie mniej niż wpłacono", mc.prob_loss(sim, years), "pct", digits=0,
                     tip="Odsetek scenariuszy, w których wartość końcowa jest niższa od sumy wpłat.",
                     note=f"wpłacone: {B.f_pln(invested)}"),
        ], "Po roku" if years == 1 else f"Po {years} latach"),
        B.controls([
            B.control("years", "segmented", "Horyzont", years, [(1, "1 rok"), (5, "5 lat"), (10, "10 lat")]),
            B.control("method", "segmented", "Model", method, [("bootstrap", "Bootstrap blokowy"), ("t", "Grube ogony")],
                      tip="Bootstrap losuje całe 20-dniowe fragmenty Twojej historii. Grube ogony losują z rozkładu t-Studenta, "
                          "w którym skrajne dni zdarzają się częściej niż w rozkładzie normalnym."),
            B.control("use_hist", "toggle", "Użyj średniego zwrotu z historii", use_hist,
                      tip=f"Historia tego składu daje {B.f_pct(sim['hist_annual_return'])} rocznie, ale dwa lata to za mało, "
                          "żeby ufać średniej. Domyślnie liczę z założeniem poniżej."),
            B.control("ret", "number", "Zakładany zwrot roczny", f"{ret:g}".replace(".", ","), unit="%"),
            B.control("contrib", "number", "Dopłata co miesiąc", f"{contrib:g}".replace(".", ","), unit="zł"),
            B.control("target", "number", "Cel", f"{target:.0f}", unit="zł"),
            B.control("dd", "number", "Próg obsunięcia", f"{dd:g}".replace(".", ","), unit="%"),
        ], submit="Przelicz symulację", grid=True),
        B.table(ht, [("label", "Horyzont", "text"), ("p5", "Pesymistycznie", "pln"), ("p50", "Mediana", "pln"),
                     ("p95", "Optymistycznie", "pln"), ("contributed", "Wpłacone", "pln"), ("prob_loss", "Ryzyko straty", "pct", 0)],
                title="Horyzonty obok siebie"),
        B.note(f"2 000 ścieżek, {'bootstrap blokowy' if method == 'bootstrap' else 'rozkład t-Studenta'}, zmienność "
               f"{B.f_pct(sim['sigma_daily'] * np.sqrt(252))} rocznie z historii dzisiejszego składu, średni zwrot "
               f"{B.f_pct(mu_annual)} rocznie. Symulacja zakłada niezmieniony skład portfela i nie uwzględnia podatków."),
    ]
    return {"title": "Monte Carlo", "blocks": blocks}


# --------------------------------------------------------------------------- co jeśli

def _extra_returns(ctx, symbol: str) -> tuple[pd.Series, bool]:
    """Historia zwrotów (w walucie bazowej) instrumentu spoza eksportu; pobierana przez dostawcę notowań."""
    def build():
        row = auto_map(symbol)
        start, end = ctx.market.prices.index.min(), ctx.pf.as_of
        px = ctx.provider.price_history(row, start, end)["close"].dropna()
        if len(px) < 60:
            raise ValueError(f"Za mało notowań dla {symbol}.")
        is_cfd = row["asset_class"] == "CFD"
        if not is_cfd and row["currency"] != ctx.base:
            fx = ctx.provider.fx_rates(row["currency"], start, end) if row["currency"] != "PLN" else None
            if ctx.base != "PLN":       # kurs krzyżowy przez złotego
                b = ctx.market.fx_pln[ctx.base]
                fx = (fx.reindex(fx.index.union(b.index)).ffill() / b.reindex(fx.index.union(b.index)).ffill()) \
                    if fx is not None else 1.0 / b
            px = (px * fx.reindex(px.index.union(fx.index)).ffill().reindex(px.index)).dropna()
        return px.pct_change().dropna(), is_cfd
    return ctx.memo(("extra", symbol), build)


def whatif_screen(ctx, p) -> dict:
    pf = ctx.pf
    equity, cash = float(pf.equity.iloc[-1]), float(pf.cash.iloc[-1])
    exp = pf.current_exposure()
    returns = ctx.asset_returns().tail(504)
    known = [s for s in ctx.mapping["symbol"] if s in returns.columns]
    default = exp.abs().idxmax() if len(exp) else (known[0] if known else "")
    symbol = p.str("symbol", default)
    other = p.str("other", "").strip().upper()
    side = p.str("side", "buy", {"buy", "sell"})
    amount = p.float("amount", 5000.0, 0.0, 1e9)
    options = [(s, f"{pf.meta['name'].get(s, s)} ({s})") for s in known] + [("__other__", "Inny instrument…")]
    ctl = B.controls([
        B.control("symbol", "select", "Instrument", symbol if symbol in known or symbol == "__other__" else default, options),
        B.control("other", "text", "Inny symbol XTB (gdy wybrano „Inny instrument”)", other, placeholder="np. TSLA.US", upper=True),
        B.control("side", "segmented", "Kierunek", side, [("buy", "Kupno"), ("sell", "Sprzedaż")]),
        B.control("amount", "number", "Kwota transakcji", f"{amount:g}".replace(".", ","), unit="zł",
                  tip="Dla CFD wpisz wartość nominalną pozycji."),
    ], submit="Sprawdź wpływ transakcji")

    sym = other if symbol == "__other__" else symbol
    if not sym:
        return {"title": "Co jeśli", "blocks": [ctl, B.note("Wybierz instrument albo wpisz symbol XTB.")]}
    try:
        if sym in returns.columns:
            is_cfd = bool(pf.meta["asset_class"].get(sym, "STOCK") == "CFD")
            rets = returns
        else:
            extra, is_cfd = _extra_returns(ctx, sym)
            rets = returns.join(extra.rename(sym), how="left")
        signed = amount if side == "buy" else -amount
        res = whatif.what_if(exp, rets, equity, cash, sym, signed, is_cfd, ctx.bench_series(p.bench).reindex(rets.index))
    except Exception as exc:
        return {"title": "Co jeśli", "blocks": [ctl, B.note(f"Nie udało się policzyć wpływu transakcji: {exc}", "warn")]}

    b, a = res["before"], res["after"]
    name = str(pf.meta["name"].get(sym, auto_map(sym)["name"]))
    verb = "Kupno" if side == "buy" else "Sprzedaż"
    d_var = a["var95"] - b["var95"]
    direction = "podniesie" if d_var > 0 else "obniży"

    def row(label, key, fmt, tip, digits=None):
        def show(x):
            return {"pln": B.f_pln(x), "pct": B.f_pct(x, digits or 1), "x": B.f_num(x) + "×"}.get(fmt, B.f_num(x, digits or 2))
        was, now = show(b[key]), show(a[key])
        # kierunek oceniamy po tym, co widać: różnica poniżej dokładności wyświetlania to "bez zmian"
        arrow = "bez zmian" if was == now else ("wzrost" if a[key] > b[key] else "spadek")
        return B.metric(label, a[key], fmt, tip=tip, digits=digits, note=f"było {was} ({arrow})")

    blocks = [
        ctl,
        B.statement(f"{verb} {name} za {B.f_pln(amount)} {direction} dzienny VaR 95% z {B.f_pln(b['var95'])} do {B.f_pln(a['var95'])}.",
                    f"Pozycja po transakcji: {B.f_pln(res['position_after'])}, czyli {B.f_pct(res['weight_after'])} kapitału."
                    + (f" Korelacja z resztą portfela: {B.f_num(res['correlation_with_rest'])}."
                       if res["correlation_with_rest"] == res["correlation_with_rest"] else "")),
        B.metrics([
            row("VaR 95% na 1 dzień", "var95", "pln", "VaR"),
            row("CVaR 95% na 1 dzień", "cvar95", "pln", "CVaR"),
            row("Zmienność portfela", "volatility", "pct", "Zmienność"),
            row("Beta", "beta", "num", "Beta"),
            row("Top 5 pozycji", "top5", "pct", "Top 5"),
            row("HHI", "hhi", "num", "HHI", 3),
            row("Efektywna liczba zakładów", "enb", "num", "Efektywna liczba zakładów", 1),
            row("Efektywna dźwignia", "gross_leverage", "x", "Efektywna dźwignia"),
            row("Gotówka", "cash", "pln", "Gotówka po transakcji. Dla CFD nie zmienia się (poza zablokowanym depozytem)."),
        ], "Po transakcji"),
    ]
    if a["cash"] < 0:
        blocks.append(B.note("Na tę transakcję nie wystarczy gotówki - saldo po niej byłoby ujemne.", "warn"))
    if side == "sell" and not is_cfd and res["position_after"] < -1e-6:
        blocks.append(B.note("Sprzedajesz więcej, niż masz. Na akcjach rzeczywistych w XTB nie da się zająć pozycji krótkiej.", "warn"))
    blocks.append(B.note("To symulacja skutków dla ryzyka przy dzisiejszych cenach, bez kosztów transakcji. "
                         "Nie jest rekomendacją kupna ani sprzedaży."))
    return {"title": "Co jeśli", "blocks": blocks}


# --------------------------------------------------------------------------- optymalizacja

def optimize_screen(ctx, p) -> dict:
    pf = ctx.pf
    exp = pf.current_exposure()
    held = exp[(~pf.is_cfd.reindex(exp.index).fillna(False)) & (exp > 0)]
    returns = ctx.asset_returns().tail(504)
    held = held[[s for s in held.index if s in returns.columns]]
    if len(held) < 3:
        return {"title": "Optymalizacja", "blocks": [B.note("Do porównania portfeli potrzeba co najmniej trzech pozycji w akcjach lub ETF-ach.")]}
    max_w = p.float("max_w", 35.0, 10.0, 100.0) / 100.0
    method = p.str("method", "min_variance", set(opt.METHODS) - {"current"})
    res = ctx.memo(("opt", round(max_w, 3), round(p.rf, 4)), lambda: opt.compare(returns, held, p.rf, max_w))
    w, st = res["weights"], res["stats"]
    stats = st.copy()
    stats.insert(0, "name", [opt.METHODS[k] for k in stats.index])
    paired = pd.DataFrame({opt.METHODS[method]: w[method], "Obecny portfel": w["current"]})
    paired = paired.sort_values("Obecny portfel", ascending=False)
    paired.index = _names(ctx, paired.index)
    wt = w.copy()
    wt.insert(0, "name", _names(ctx, wt.index))
    wt = wt.sort_values("current", ascending=False)
    cur_vol, lo_vol = st.at["current", "volatility"], st.at["min_variance", "volatility"]

    blocks = [
        B.statement(f"Te same {len(held)} pozycji w innych proporcjach mogłoby wahać się o {B.f_pct(lo_vol)} rocznie zamiast {B.f_pct(cur_vol)}.",
                    "To analiza porównawcza, nie rekomendacja. Pokazuje, ile ryzyka wynika z proporcji, a ile z samego doboru spółek."),
        B.table(stats, [("name", "Portfel", "text"), ("volatility", "Zmienność", "pct", 1), ("return", "Zwrot hist.", "pct", 1),
                        ("sharpe", "Sharpe", "num", 2), ("max_weight", "Najw. waga", "pct", 0),
                        ("max_risk_share", "Najw. udział w ryzyku", "pct", 0), ("turnover", "Obrót", "pct", 0)],
                title="Portfele obok siebie",
                caption="Obrót: jaką część portfela trzeba by przestawić. Zwrot historyczny jest ściągnięty do średniej wszystkich "
                        "pozycji, bo dwa lata historii słabo prognozują zwroty."),
        B.controls([
            B.control("method", "select", "Porównaj obecne wagi z portfelem", method,
                      [(k, v) for k, v in opt.METHODS.items() if k != "current"]),
            B.control("max_w", "slider", "Limit wagi jednej pozycji", round(max_w * 100), min=10, max=100, step=5, fmt="int", unit="%",
                      tip="Bez limitu optymalizator chętnie wkłada większość kapitału w 2-3 pozycje."),
        ]),
        B.chart(B.hbar_grouped(paired, "pct", 0, colors={opt.METHODS[method]: 0, "Obecny portfel": "neutral"}),
                f"Wagi: {opt.METHODS[method]} a obecny portfel"),
        B.table(wt, [("name", "Pozycja", "text"), ("current", "Obecnie", "pct", 1), ("min_variance", "Min. wariancja", "pct", 1),
                     ("max_sharpe", "Max Sharpe", "pct", 1), ("risk_parity", "Parytet ryzyka", "pct", 1), ("hrp", "HRP", "pct", 1)],
                title="Wszystkie wagi", limit=10),
        B.bullets([
            "**Minimalna wariancja** - najniższe możliwe wahania; nie patrzy na zwroty, więc jest najmniej wrażliwa na błędy prognoz.",
            "**Maksymalny Sharpe** - najlepszy stosunek zwrotu do ryzyka według historii. Bardzo wrażliwy na to, które spółki "
            "akurat miały dobre dwa lata; traktuj go najostrożniej.",
            "**Parytet ryzyka** - każda pozycja dokłada tyle samo do wahań portfela.",
            "**HRP** - grupuje podobne spółki i dzieli ryzyko między grupy; odporny na szum w korelacjach.",
            "Uwzględnione są tylko akcje i ETF-y (bez CFD i gotówki), same pozycje długie. Kowariancja z ostatnich 504 sesji, "
            "ściągnięta w 20% do macierzy diagonalnej.",
        ], "Jak czytać te portfele"),
    ]
    return {"title": "Optymalizacja", "blocks": blocks}
