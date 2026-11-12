"""Spółki: co o firmie mówią liczby - odwrócony DCF, sprawozdania, prognozy i ceny docelowe analityków.

Dane pochodzą z Yahoo Finance (w trybie demo są zmyślone). Ekran niczego nie ocenia i nie rekomenduje:
pokazuje, jakie założenia są wpisane w dzisiejszą cenę, i zestawia je z historią oraz konsensusem.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from core import fundamentals as F
from server import blocks as B

DISCLAIMER = ("To dane i arytmetyka, nie rekomendacja. Ceny docelowe i prognozy to opinie analityków zebrane przez "
              "Yahoo Finance - bywają nieaktualne i często się mylą. Odwrócony DCF jest bardzo czuły na założenia.")
REC = {"strong_buy": "zdecydowana większość pozytywna", "buy": "przewaga pozytywnych", "hold": "neutralne",
       "underperform": "przewaga negatywnych", "sell": "negatywne"}
PERIODS = {"0q": "bieżący kwartał", "+1q": "następny kwartał", "0y": "bieżący rok", "+1y": "następny rok"}


def _ok(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and math.isfinite(x)


def _big(x, cur: str = "") -> str:
    """Duże kwoty w walucie spółki: 12,3 mld USD."""
    if not _ok(x):
        return "–"
    for limit, unit in ((1e12, "bln"), (1e9, "mld"), (1e6, "mln")):
        if abs(x) >= limit:
            return f"{B.f_num(x / limit, 2 if abs(x) < 10 * limit else 1)} {unit} {cur}".strip()
    return f"{B.f_num(x, 0)} {cur}".strip()


def _candidates(ctx) -> list[tuple[str, str]]:
    """Akcje z portfela (największe pozycje najpierw), potem pozostałe akcje z historii."""
    m = ctx.mapping.set_index("symbol")
    stocks = [s for s in m.index if m.at[s, "asset_class"] == "STOCK"]
    val = ctx.pf.current_value().reindex(stocks).fillna(0.0)
    order = sorted(stocks, key=lambda s: (-float(val.get(s, 0.0)), s))
    return [(s, f"{m.at[s, 'name']} ({s})" + ("" if val.get(s, 0.0) > 0 else " - poza portfelem")) for s in order]


def _fx_cross(ctx, src: str, dst: str) -> float:
    """Ile `dst` za jednostkę `src` (ostatni kurs NBP). NaN, gdy którejś waluty nie ma."""
    if src == dst:
        return 1.0

    def to_pln(c):
        if c == "PLN":
            return 1.0
        fx = ctx.market.fx_pln
        if fx is not None and c in fx.columns and fx[c].notna().any():
            return float(fx[c].dropna().iloc[-1])
        try:
            end = ctx.pf.as_of
            return float(ctx.provider.fx_rates(c, end - pd.Timedelta(days=20), end).dropna().iloc[-1])
        except Exception:
            return float("nan")

    return to_pln(src) / to_pln(dst)


def _load(ctx, symbol: str) -> dict:
    row = ctx.mapping.set_index("symbol").loc[symbol].to_dict() | {"symbol": symbol}
    return ctx.memo(("fund", symbol), lambda: ctx.provider.fundamentals(row))


def companies(ctx, p) -> dict:
    options = _candidates(ctx)
    if not options:
        return {"title": "Spółki", "blocks": [B.statement("W tym portfelu nie ma akcji pojedynczych spółek.",
                                                          "Moduł działa dla akcji; ETF-y i CFD nie mają sprawozdań.")]}
    symbol = p.str("symbol", options[0][0], [o[0] for o in options])
    r = p.float("r", 10.0, 4.0, 20.0) / 100.0
    tg = p.float("tg", 2.5, 0.0, 4.0) / 100.0
    years = p.int("years", 10, 5, 15)
    picker = B.controls([B.control("symbol", "select", "Spółka", symbol, options)])
    try:
        f = _load(ctx, symbol)
    except Exception as exc:
        return {"title": "Spółki", "blocks": [
            B.statement("Nie udało się pobrać danych o tej spółce.", str(exc)), picker,
            B.note("Sprawdź ticker Yahoo w „Więcej → Dane i ustawienia → Tickery”. Dane fundamentalne są dostępne głównie "
                   "dla spółek z USA i dużych spółek europejskich.", "warn")]}

    cur, fin = f.get("currency") or "", f.get("financial_currency") or f.get("currency") or ""
    k = _fx_cross(ctx, fin, cur) if fin and cur else 1.0          # sprawozdania -> waluta notowań (np. NVO: DKK -> USD)
    conv = lambda x: x * k if _ok(x) and math.isfinite(k) else None
    price, cap = f.get("price"), f.get("market_cap")
    ev = f.get("enterprise_value") if _ok(f.get("enterprise_value")) else cap
    fcf_hist = [(d["year"], conv(d["value"])) for d in f.get("fcf", [])]
    rev_hist = [(d["year"], conv(d["value"])) for d in f.get("revenue", [])]
    fcf = conv(f.get("fcf_ttm")) if _ok(f.get("fcf_ttm")) else (fcf_hist[-1][1] if fcf_hist else None)
    implied = F.reverse_dcf(ev, fcf, r, tg, years) if _ok(ev) and _ok(fcf) else float("nan")
    fcf_cagr, rev_cagr = F.cagr([v for _, v in fcf_hist]), F.cagr([v for _, v in rev_hist])
    est = {e["period"]: e for e in f.get("revenue_estimates", [])}
    next_growth = next((est[k_]["growth"] for k_ in ("+1y", "0y") if k_ in est and _ok(est[k_].get("growth"))), None)
    name = f.get("name") or symbol

    # ---- zdanie otwierające
    if math.isfinite(implied):
        lead = (f"Dzisiejsza wycena {name} zakłada, że wolne przepływy pieniężne będą rosły o {B.f_pct(implied)} "
                f"rocznie przez {years} lat.")
        bits = []
        if math.isfinite(fcf_cagr):
            bits.append(f"przez ostatnie lata rosły o {B.f_pct(fcf_cagr)} rocznie")
        if _ok(next_growth):
            bits.append(f"analitycy prognozują wzrost przychodów o {B.f_pct(next_growth)}")
        sub = ("Dla porównania: " + ", a ".join(bits) + ".") if bits else \
            f"Przy stopie dyskonta {B.f_pct(r)} i wzroście wiecznym {B.f_pct(tg)}."
    elif _ok(fcf) and fcf <= 0:
        lead = f"{name} ma ujemne wolne przepływy pieniężne, więc odwrócony DCF nie daje odpowiedzi."
        sub = "Wycena opiera się na oczekiwaniu przyszłych zysków, których dziś jeszcze nie ma."
    else:
        lead = f"Dla {name} brakuje danych do odwróconego DCF."
        sub = "Poniżej to, co udało się pobrać."

    blocks = [B.statement(lead, sub), picker]

    # ---- wycena
    yield_ = fcf / cap if _ok(fcf) and _ok(cap) and cap > 0 else None
    blocks.append(B.metrics([
        B.metric("Cena", f"{B.f_num(price, 2)} {cur}" if _ok(price) else "–", "text",
                 "Ostatnia cena akcji w walucie notowań, według Yahoo Finance."),
        B.metric("Kapitalizacja", _big(cap, cur), "text", "Cena akcji razy liczba akcji - ile kosztuje cała spółka."),
        B.metric("Wartość przedsiębiorstwa (EV)", _big(ev, cur), "text",
                 "Kapitalizacja plus dług minus gotówka. Tyle trzeba by zapłacić za firmę razem z jej długiem."),
        B.metric("Wolne przepływy (FCF, 12 mies.)", _big(fcf, cur), "text",
                 "Gotówka, która zostaje firmie po wydatkach na działalność i inwestycje. To z niej biorą się dywidendy, "
                 "skup akcji i spłata długu."),
        B.metric("Stopa FCF", yield_, "pct", "FCF podzielone przez kapitalizację. 4% znaczy: firma zarabia dla "
                                            "akcjonariuszy 4 zł gotówki rocznie na każde 100 zł wyceny.", digits=1),
        B.metric("C/Z", f.get("pe"), "num", "Cena do zysku za ostatnie 12 miesięcy.", digits=1),
        B.metric("C/Z prognozowane", f.get("forward_pe"), "num", "Cena do zysku prognozowanego na kolejne 12 miesięcy.", digits=1),
        B.metric("EV/EBITDA", f.get("ev_ebitda"), "num", "Wartość przedsiębiorstwa do zysku operacyjnego przed amortyzacją.", digits=1),
    ], "Wycena"))

    # ---- odwrócony DCF
    if math.isfinite(implied):
        grid = pd.DataFrame(F.implied_growth_grid(ev, fcf, [r - 0.02, r - 0.01, r, r + 0.01, r + 0.02],
                                                  [max(tg - 0.01, 0.0), tg, tg + 0.01], years))
        grid = grid[grid["discount"] > 0.03]
        grid["label"] = [B.f_pct(d, 0) for d in grid["discount"]]
        gcols = [c for c in grid.columns if c.startswith("g")]
        blocks += [
            B.metrics([
                B.metric("Wzrost FCF wpisany w cenę", implied, "pct", digits=1,
                         tip="Takie roczne tempo wzrostu wolnych przepływów sprawia, że suma zdyskontowanych przepływów "
                             "równa się dzisiejszej wartości przedsiębiorstwa. Im wyższe, tym więcej firma musi dowieźć."),
                B.metric("Historyczny wzrost FCF", fcf_cagr, "pct", digits=1,
                         tip="Średnioroczne tempo między pierwszym a ostatnim rokiem ze sprawozdań (zwykle 4 lata)."),
                B.metric("Historyczny wzrost przychodów", rev_cagr, "pct", digits=1,
                         tip="Średnioroczne tempo wzrostu sprzedaży między pierwszym a ostatnim rokiem ze sprawozdań."),
                B.metric("Prognoza wzrostu przychodów", next_growth, "pct", digits=1,
                         tip="Konsensus analityków na najbliższy pełny rok obrotowy."),
            ], "Odwrócony DCF"),
            B.controls([
                B.control("r", "slider", "Stopa dyskonta", round(r * 100, 1), min=5, max=15, step=0.5, unit="%",
                          tip="Zwrot, jakiego wymagasz za ryzyko tej spółki. Typowo 8-10% dla dużych, stabilnych firm, "
                              "więcej dla małych i zadłużonych."),
                B.control("tg", "slider", "Wzrost po okresie prognozy", round(tg * 100, 1), min=0, max=4, step=0.5, unit="%",
                          tip="Tempo wzrostu „na zawsze” po ostatnim roku. Nie powinno przekraczać długoterminowego "
                              "wzrostu gospodarki (2-3%)."),
                B.control("years", "segmented", "Okres wzrostu", years, [(5, "5 lat"), (10, "10 lat"), (15, "15 lat")]),
            ]),
            B.table(grid, [("label", "Dyskonto", "text")] + [
                (c, f"wieczny {B.f_num(int(c[1:]) / 10, 1)}%", "pct", 1) for c in gcols],
                    "Wrażliwość: wzrost wpisany w cenę",
                    "Wiersze: stopa dyskonta. Kolumny: wzrost po okresie prognozy. Puste pole = wynik poza zakresem.", limit=8),
        ]

    # ---- przychody: historia i prognoza
    rev = pd.DataFrame(index=pd.Index([], name="rok"))
    if rev_hist:
        hist = pd.Series({str(y): v / 1e9 for y, v in rev_hist if _ok(v)})
        fut = {}
        last_year = rev_hist[-1][0]
        for i, key in enumerate(("0y", "+1y"), start=1):
            if key in est and _ok(est[key].get("avg")):
                fut[f"{last_year + i}P"] = conv(est[key]["avg"]) / 1e9
        rev = pd.DataFrame({"Przychody": hist}).reindex(list(hist.index) + list(fut))
        rev["Prognoza analityków"] = pd.Series(fut)
        blocks.append(B.chart(B.bars(rev, "num", 1, {"Przychody": 0, "Prognoza analityków": 3}), "Przychody: historia i prognoza",
                              f"W mld {cur}. Lata z literą P to konsensus analityków (rok obrotowy spółki)."
                              + (f" Sprawozdania w {fin} przeliczone po bieżącym kursie." if fin != cur else "")))
    rows = [{"okres": PERIODS.get(e["period"], e["period"]), "avg": _big(conv(e["avg"]), cur),
             "range": f"{_big(conv(e['low']), '')} – {_big(conv(e['high']), cur)}" if _ok(e.get("low")) and _ok(e.get("high")) else "–",
             "growth": e.get("growth"), "n": e.get("analysts")} for e in f.get("revenue_estimates", []) if _ok(e.get("avg"))]
    if rows:
        blocks.append(B.table(pd.DataFrame(rows), [("okres", "Okres", "text"), ("avg", "Średnia prognoza", "text"),
                                                   ("range", "Rozrzut", "text"), ("growth", "Wzrost r/r", "pct", 1),
                                                   ("n", "Analityków", "int")], "Prognozy przychodów", limit=6))

    # ---- przepływy pieniężne
    cf = pd.DataFrame({
        "Przepływy operacyjne": pd.Series({str(d["year"]): conv(d["value"]) for d in f.get("ocf", [])}, dtype=float),
        "Wolne przepływy (FCF)": pd.Series({str(y): v for y, v in fcf_hist}, dtype=float),
        "Zysk netto": pd.Series({str(d["year"]): conv(d["value"]) for d in f.get("net_income", [])}, dtype=float),
    }).dropna(how="all").sort_index() / 1e9
    if len(cf):
        blocks.append(B.chart(B.bars(cf, "num", 1, {"Przepływy operacyjne": 0, "Wolne przepływy (FCF)": 2, "Zysk netto": 6}),
                              "Gotówka i zysk", f"W mld {cur}. Zysk netto jest księgowy; przepływy pokazują, ile gotówki "
                                               "faktycznie wpłynęło. Duża, trwała różnica wymaga wyjaśnienia."))

    # ---- ceny docelowe
    t = f.get("targets") or {}
    if _ok(t.get("mean")) and _ok(price):
        tg_s = pd.Series({"Najniższa": t.get("low"), "Mediana": t.get("median"), "Średnia": t.get("mean"),
                          "Najwyższa": t.get("high")}, dtype=float).dropna()
        gaps = tg_s.map(lambda x: F.target_gap(price, x))
        rec = REC.get(str(f.get("recommendation") or ""), "")
        blocks += [
            B.metrics([
                B.metric("Średnia cena docelowa", f"{B.f_num(t['mean'], 2)} {cur}", "text",
                         note=f"{B.f_pct(F.target_gap(price, t['mean']), 1, sign=True)} wobec ceny"),
                B.metric("Rozrzut", f"{B.f_num(t.get('low'), 0)} – {B.f_num(t.get('high'), 0)} {cur}"
                         if _ok(t.get("low")) and _ok(t.get("high")) else "–", "text",
                         "Najniższa i najwyższa cena docelowa. Szeroki rozrzut = analitycy mocno się różnią."),
                B.metric("Liczba analityków", t.get("analysts"), "int",
                         "Ile biur maklerskich podało cenę docelową. Przy kilku opiniach średnia niewiele znaczy."),
                *([B.metric("Nastawienie analityków", rec, "text",
                            "Uśrednione oceny biur maklerskich według Yahoo Finance. To opis ich opinii, nie zalecenie.")] if rec else []),
            ], "Ceny docelowe analityków (12 miesięcy)"),
            B.chart(B.hbar(gaps, "pct", 1, signed=True), None, "Odległość ceny docelowej od dzisiejszej ceny."),
        ]

    blocks += [B.note(DISCLAIMER, "warn"),
               B.note(f"Źródło: {f.get('source', 'Yahoo Finance')}, pobrano {f.get('fetched', '')}. "
                      "Model: przepływy rosną w stałym tempie przez wybrany okres, potem w tempie wiecznym; "
                      "wartość porównuję z wartością przedsiębiorstwa (EV).")]
    return {"title": "Spółki", "blocks": blocks}
