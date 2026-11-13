"""Miary funduszowe: statystyki (tearsheet) w dziale Wyniki; long/short z betą i limity ryzyka w dziale Ryzyko.

To te same dane co na pozostałych ekranach, ułożone tak, jak raportuje je fundusz: stopy i ryzyko względem
benchmarku, obsunięcia jako epizody, ekspozycje jako część NAV, limity ze statusem.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from core import hedgefund as H
from core import performance as perf
from core import risk
from server import blocks as B

NOT_ADVICE = "To kalkulator do własnych analiz, nie rekomendacja zawarcia transakcji."


def _bench(ctx, p):
    b = ctx.bench_series(p.bench)
    return (b.reindex(ctx.pf.dates) if b is not None else None), ctx.bench_label(p.bench)


def tearsheet(ctx, p) -> dict:
    pf = ctx.pf
    r = pf.returns
    if len(r) < 5:
        return {"title": "Statystyki", "blocks": [B.note("Za mało danych w wybranym okresie.")]}
    b, bname = _bench(ctx, p)
    s = perf.summary(r, b, p.rf, pf.flows, float(pf.equity.iloc[-1]))
    cap = H.capture_ratios(r, b) if b is not None else {"up": np.nan, "down": np.nan, "up_months": 0, "down_months": 0}
    ms = H.month_stats(r)
    window = p.int("window", 63, 21, 252)
    roll = H.rolling_stats(r, b, window, p.rf)
    dd = H.drawdown_episodes(r, 5)
    bench_cagr = perf.cagr(b) if b is not None else np.nan
    lead = (f"Od {pf.dates[0]:%d.%m.%Y} portfel zarabia {B.f_pct(s['cagr'])} rocznie przy zmienności {B.f_pct(s['volatility'])} "
            f"- Sharpe {B.f_num(s['sharpe'], 2)}.")
    sub = (f"{bname}: {B.f_pct(bench_cagr)} rocznie. Beta {B.f_num(s.get('beta'), 2)}, alfa {B.f_pct(s.get('alpha'), 1, sign=True)} rocznie."
           if b is not None and np.isfinite(bench_cagr) else None)
    recovered = s.get("dd_recovery") is not None or s["max_drawdown"] == 0
    blocks = [
        B.statement(lead, sub),
        B.metrics([
            B.metric("Stopa zwrotu (TWR)", s["total_return"], "pct", sign=True),
            B.metric("CAGR", s["cagr"], "pct", sign=True),
            B.metric("MWR / XIRR", s["xirr"], "pct", sign=True),
            B.metric("Zmienność", s["volatility"], "pct"),
            B.metric("Sharpe", s["sharpe"], "num", digits=2),
            B.metric("Sortino", s["sortino"], "num", digits=2),
            B.metric("Calmar", s["calmar"], "num", "Stopa roczna podzielona przez największe obsunięcie. Mówi, ile zwrotu "
                                                   "przypada na jednostkę najgorszej straty.", digits=2),
            B.metric("Max drawdown", s["max_drawdown"], "pct"),
        ], "Zwrot i ryzyko"),
        B.metrics([
            B.metric("Beta", s.get("beta"), "num", "Beta", digits=2),
            B.metric("Alfa (rocznie)", s.get("alpha"), "pct", "Alfa", sign=True),
            B.metric("Korelacja", s.get("correlation"), "num", "Jak blisko portfel chodzi za benchmarkiem: 1 = identycznie, 0 = niezależnie.", digits=2),
            B.metric("Tracking error", s.get("tracking_error"), "pct"),
            B.metric("Information ratio", s.get("information_ratio"), "num", digits=2),
            B.metric("Przechwytywanie wzrostów", cap["up"], "x", f"W miesiącach, gdy {bname} rósł, portfel zarabiał średnio tyle razy "
                     f"więcej (lub mniej) niż on. Liczone na {cap['up_months']} miesiącach.", digits=2),
            B.metric("Przechwytywanie spadków", cap["down"], "x", f"W miesiącach spadku benchmarku portfel tracił średnio tyle razy "
                     f"tyle co on. Poniżej 1 = traci mniej niż rynek. Liczone na {cap['down_months']} miesiącach.", digits=2),
        ], f"Względem benchmarku: {bname}"),
        B.metrics([
            B.metric("Dodatnie miesiące", ms["positive"], "pct", f"Udział miesięcy z zyskiem ({ms['n']} miesięcy).", digits=0),
            B.metric("Najlepszy miesiąc", ms["best"], "pct", "Najwyższa miesięczna stopa zwrotu w okresie.", sign=True),
            B.metric("Najgorszy miesiąc", ms["worst"], "pct", "Najniższa miesięczna stopa zwrotu w okresie.", sign=True),
            B.metric("Dni na plusie", s["positive_days"], "pct", digits=0),
            B.metric("Najlepszy dzień", s["best_day"], "pct", digits=2, sign=True),
            B.metric("Najgorszy dzień", s["worst_day"], "pct", digits=2, sign=True),
        ], "Miesiące i dni"),
        B.metrics([
            B.metric("Skośność", s["skew"], "num", digits=2),
            B.metric("Kurtoza", s["kurtosis"], "num", digits=2),
            B.metric("Tail ratio", H.tail_ratio(r), "num", "95. percentyl dziennych zwrotów do 5. percentyla (co do wartości). "
                                                         "Powyżej 1: dobre dni są większe niż złe.", digits=2),
            B.metric("Omega", H.omega(r), "num", "Suma dziennych zysków do sumy dziennych strat. Powyżej 1 = zyski przeważają.", digits=2),
        ], "Kształt rozkładu"),
    ]
    if len(roll):
        w = f"{window} sesji"
        blocks.append(B.controls([B.control("window", "segmented", "Okno kroczące", window,
                                            [(21, "1 mies."), (63, "3 mies."), (126, "6 mies."), (252, "12 mies.")])]))
        blocks.append(B.chart(B.line(roll[["sharpe"]].rename(columns={"sharpe": "Sharpe"}).dropna(), "num", 2, zero=True, group="rolling"),
                              "Sharpe kroczący", f"Okno {w}. Pokazuje, czy jakość wyniku jest stała, czy pochodzi z jednego okresu."))
        blocks.append(B.chart(B.line(roll[["vol"]].rename(columns={"vol": "Zmienność"}).dropna(), "pct", 1, group="rolling"),
                              "Zmienność krocząca", f"Roczna, okno {w}."))
        if "beta" in roll.columns and roll["beta"].notna().any():
            blocks.append(B.chart(B.line(roll[["beta"]].rename(columns={"beta": "Beta"}).dropna(), "num", 2, zero=True, group="rolling"),
                                  f"Beta krocząca do {bname}", f"Okno {w}. Rosnąca beta = portfel coraz mocniej chodzi za rynkiem."))
    if len(dd):
        t = dd.assign(peak=dd["peak"].map(lambda d: f"{d:%d.%m.%Y}"), trough=dd["trough"].map(lambda d: f"{d:%d.%m.%Y}"),
                      recovery=[("trwa" if o else f"{x:%d.%m.%Y}") for x, o in zip(dd["recovery"], dd["ongoing"])])
        blocks.append(B.table(t, [("peak", "Szczyt", "text"), ("trough", "Dołek", "text"), ("recovery", "Odrobione", "text"),
                                  ("depth", "Głębokość", "pct", 1), ("days_down", "Dni do dołka", "int"),
                                  ("days_total", "Dni łącznie", "int")], "Największe obsunięcia",
                              "Każdy epizod od szczytu do odrobienia straty. Dni kalendarzowe.", limit=5))
    blocks.append(B.metrics([
        B.metric("Najdłuższe obsunięcie", s["max_dd_duration_days"], "days", tip="Czas obsunięcia",
                 note="nadal trwa" if s["dd_longest_ongoing"] else None),
        B.metric("Odrabianie największego spadku", s["max_dd_recovery_days"] if recovered else None, "days",
                 tip="Czas odrabiania", note=None if recovered else "jeszcze nie odrobione"),
    ], "Czas pod wodą"))
    if s["n_days"] < 500:
        blocks.append(B.note("Przy historii krótszej niż ok. 2 lata Sharpe, alfa i information ratio mają dużą "
                             "niepewność. Traktuj je jako orientacyjne."))
    return {"title": "Statystyki", "blocks": blocks}


def _betas(ctx, p) -> pd.Series:
    b = ctx.bench_series(p.bench)
    if b is None:
        return pd.Series(dtype=float)
    lvl = ctx.market.benchmarks_base[p.bench].dropna()
    br = lvl.pct_change().dropna().loc[:ctx.pf.as_of].iloc[-504:]
    return ctx.memo(("asset_betas", p.bench), lambda: H.asset_betas(ctx.asset_returns().iloc[-504:], br))


def longshort(ctx, p) -> dict:
    pf = ctx.pf
    equity = float(pf.equity.iloc[-1])
    exp = pf.current_exposure()
    exp = exp[exp.abs() > 1e-9]
    if not len(exp) or equity <= 0:
        return {"title": "Long / short", "blocks": [B.note("Portfel jest w całości w gotówce.")]}
    bname = ctx.bench_label(p.bench)
    betas = _betas(ctx, p)
    ls = H.long_short(exp, equity, betas)
    beta_adj = ls.get("beta_adjusted", ls["net"])
    target = p.float("target", 0.0, -0.5, 1.5)
    hedge = H.hedge_notional(beta_adj, equity, target)
    lead = (f"Portfel jest zaangażowany na {B.f_pct(ls['net'], 0)} netto i {B.f_pct(ls['gross'], 0)} brutto kapitału; "
            f"jego beta do {bname} to {B.f_num(beta_adj, 2)}.")
    sub = (f"{ls['n_long']} pozycji długich ({B.f_pct(ls['long'], 0)} NAV)"
           + (f", {ls['n_short']} {'krótka' if ls['n_short'] == 1 else 'krótkie' if ls['n_short'] < 5 else 'krótkich'} "
              f"({B.f_pct(ls['short'], 0)} NAV)." if ls["n_short"] else ", brak pozycji krótkich."))
    meta = pf.meta
    sect = pd.DataFrame({"e": exp / equity, "s": meta["sector"].reindex(exp.index).fillna("Nieznany")})
    by = pd.DataFrame({"Długie": sect[sect["e"] > 0].groupby("s")["e"].sum(),
                       "Krótkie": sect[sect["e"] < 0].groupby("s")["e"].sum()}).fillna(0.0)
    by["net"] = by["Długie"] + by["Krótkie"]
    by = by.sort_values("net", ascending=False)
    pos = pd.DataFrame({"name": meta["name"].reindex(exp.index).fillna(pd.Series(exp.index, index=exp.index)),
                        "side": np.where(exp > 0, "długa", "krótka"), "weight": exp / equity,
                        "beta": betas.reindex(exp.index), "beta_w": exp / equity * betas.reindex(exp.index).fillna(1.0)})
    pos = pos.reindex(pos["weight"].abs().sort_values(ascending=False).index)
    blocks = [
        B.statement(lead, sub),
        B.metrics([
            B.metric("Ekspozycja długa", ls["long"], "pct", "Suma pozycji długich (akcje, ETF-y, nominał długich CFD) jako część kapitału.", digits=0),
            B.metric("Ekspozycja krótka", ls["short"], "pct", "Suma nominałów pozycji krótkich jako część kapitału.", digits=0),
            B.metric("Brutto", ls["gross"], "pct", "Długie plus krótkie. Powyżej 100% oznacza dźwignię.", digits=0),
            B.metric("Netto", ls["net"], "pct", "Długie minus krótkie - kierunkowe zaangażowanie w rynek.", digits=0),
            B.metric("Beta portfela", beta_adj, "num", f"Suma (waga × beta instrumentu do {bname}) z ostatnich dwóch lat. "
                     "1,0 = portfel rusza się jak benchmark; 0 = neutralny rynkowo.", digits=2),
            B.metric("Gotówka i niezaangażowane", ls["cash"], "pct", "Kapitał minus ekspozycja netto.", digits=0),
        ]),
        B.chart(B.hbar_grouped(by[["Długie", "Krótkie"]], "pct", 0, {"Długie": "pos", "Krótkie": "neg"}), "Ekspozycja według sektorów",
                "Jako część kapitału (NAV). Długie na plus, krótkie na minus."),
        B.table(pos, [("name", "Pozycja", "text"), ("side", "Strona", "text"), ("weight", "Waga w NAV", "pct", 1),
                      ("beta", "Beta", "num", 2), ("beta_w", "Wkład w betę", "num", 2)], "Pozycje i ich beta",
                f"Beta do {bname}. Wkład w betę = waga × beta; suma kolumny to beta portfela.", limit=12),
        B.metrics([
            B.metric("Nominał zabezpieczenia", abs(hedge), "pln",
                     f"Wartość {'krótkiej' if hedge > 0 else 'długiej'} pozycji na {bname}, przy której beta portfela spada do "
                     f"docelowej. Liczone jako (beta portfela − beta docelowa) × kapitał.",
                     note=f"{'krótka' if hedge > 0 else 'długa'} pozycja na indeksie, {B.f_pct(abs(hedge) / equity, 0)} NAV"),
            B.metric("Beta po zabezpieczeniu", target, "num", "Beta docelowa wybrana suwakiem poniżej.", digits=2),
        ], "Kalkulator zabezpieczenia bety"),
        B.controls([B.control("target", "slider", "Beta docelowa", round(target, 2), min=0, max=1, step=0.05, fmt="num",
                              tip="0 = portfel neutralny wobec rynku; 0,5 = połowa wrażliwości rynku.")]),
        B.note(NOT_ADVICE + " Beta z przeszłości nie musi się utrzymać, a zabezpieczenie indeksem nie usuwa ryzyka "
               "pojedynczych spółek.", "warn"),
    ]
    return {"title": "Long / short", "blocks": blocks}


LIMIT_DEFAULTS = {"pos": 15.0, "sector": 35.0, "top5": 60.0, "gross": 150.0, "var": 3.0, "dd": 20.0, "cash": 2.0}


def limits(ctx, p) -> dict:
    pf = ctx.pf
    equity = float(pf.equity.iloc[-1])
    exp = pf.current_exposure()
    exp = exp[exp.abs() > 1e-9]
    lim = {k: p.float(k, v, 0.0, 1000.0) for k, v in LIMIT_DEFAULTS.items()}
    w = (exp / equity) if equity > 0 else exp * np.nan
    sect = w.groupby(pf.meta["sector"].reindex(exp.index).fillna("Nieznany")).sum() if len(w) else pd.Series(dtype=float)
    var95 = np.nan
    try:
        r, e, _ = ctx.risk_inputs()
        if len(e) and len(r) > 30:
            var95 = risk.var_historical(risk.pnl_series(r, e), 0.95, 1) / equity
    except Exception:
        pass
    dd_now = float(perf.drawdown_series(pf.returns).iloc[-1]) if len(pf.returns) else np.nan
    names = pf.meta["name"].reindex(exp.index)
    big = w.abs().idxmax() if len(w) else None
    rows = [
        {"name": "Największa pozycja", "detail": str(names.get(big, big)) if big else "–", "value": float(w.abs().max()) if len(w) else 0.0,
         "limit": lim["pos"] / 100, "kind": "max"},
        {"name": "Największy sektor", "detail": str(sect.abs().idxmax()) if len(sect) else "–",
         "value": float(sect.abs().max()) if len(sect) else 0.0, "limit": lim["sector"] / 100, "kind": "max"},
        {"name": "Pięć największych pozycji", "detail": "koncentracja", "value": float(w.abs().nlargest(5).sum()) if len(w) else 0.0,
         "limit": lim["top5"] / 100, "kind": "max"},
        {"name": "Ekspozycja brutto", "detail": "długie + krótkie", "value": float(w.abs().sum()) if len(w) else 0.0,
         "limit": lim["gross"] / 100, "kind": "max"},
        {"name": "VaR 95% na 1 dzień", "detail": "część kapitału", "value": var95, "limit": lim["var"] / 100, "kind": "max"},
        {"name": "Bieżące obsunięcie", "detail": "od szczytu", "value": abs(dd_now), "limit": lim["dd"] / 100, "kind": "max"},
        {"name": "Gotówka", "detail": "minimum", "value": float(pf.cash.iloc[-1]) / equity if equity > 0 else np.nan,
         "limit": lim["cash"] / 100, "kind": "min"},
    ]
    t = H.check_limits(rows)
    breached, near = int((t["status"] == "przekroczony").sum()), int((t["status"] == "blisko").sum())
    if breached:
        lead = f"{breached} z {len(t)} limitów jest przekroczonych: " + ", ".join(t.loc[t["status"] == "przekroczony", "name"].str.lower()) + "."
    elif near:
        lead = f"Wszystkie limity są zachowane, ale {near} jest wykorzystanych w ponad 80%."
    else:
        lead = f"Wszystkie {len(t)} limitów ryzyka są zachowane."
    t["limit_txt"] = [("min " if k == "min" else "max ") + B.f_pct(v, 0 if v >= 0.1 else 1) for v, k in zip(t["limit"], t["kind"])]
    ctl = [B.control(k, "number", label, f"{lim[k]:g}".replace(".", ","), unit="%") for k, label in (
        ("pos", "Maks. jedna pozycja"), ("sector", "Maks. jeden sektor"), ("top5", "Maks. top 5 pozycji"),
        ("gross", "Maks. ekspozycja brutto"), ("var", "Maks. VaR 95% 1 dzień"), ("dd", "Maks. obsunięcie"),
        ("cash", "Min. gotówka"))]
    return {"title": "Limity ryzyka", "blocks": [
        B.statement(lead, f"Stan na {pf.as_of:%d.%m.%Y}, progi jako część kapitału (NAV)."),
        B.table(t, [("name", "Limit", "text"), ("detail", "Czego dotyczy", "text"), ("value", "Teraz", "pct", 1),
                    ("limit_txt", "Próg", "text"), ("usage", "Wykorzystanie", "pct", 0), ("status", "Status", "text")],
                "Monitor limitów", "Wykorzystanie powyżej 100% = limit przekroczony; od 80% = blisko progu.", limit=10),
        B.chart(B.hbar(t.set_index("name")["usage"].clip(upper=2.0).dropna(), "pct", 0), "Wykorzystanie limitów",
                "100% to próg. Słupki ucięte na 200%."),
        B.section("Ustaw własne progi", [B.controls(ctl, submit="Przelicz", grid=True)]),
        B.note("Progi domyślne to typowe ograniczenia portfela akcyjnego, nie zalecenie. Ustaw własne i wracaj tu po każdej "
               "większej zmianie w portfelu."),
    ]}
