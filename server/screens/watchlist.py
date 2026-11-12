"""Spółki / Lista: pozycje z portfela z bieżącą ceną i zmianą dzienną, najbliższe raporty, dywidendy, wiadomości.

Notowania, terminy i nagłówki pochodzą z Yahoo Finance (w trybie demo są zmyślone). Gdy bieżącej ceny nie da
się pobrać, lista pokazuje ostatnie zamknięcie z historii notowań - ekran działa także bez tych danych.
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from core import companies as C
from server import blocks as B

CLASS_PL = {"STOCK": "akcja", "ETF": "ETF", "CFD": "CFD"}
SORTS = [("weight", "Waga"), ("change", "Zmiana dzienna"), ("report", "Termin raportu")]
WD = ["pn.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]


def _plural(n: int, one: str, few: str, many: str) -> str:
    return one if n == 1 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _in_days(n: int) -> str:
    return "dziś" if n == 0 else "jutro" if n == 1 else f"za {n} dni"


def _big(x) -> str:
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return "–"
    for lim, unit in ((1e12, "bln"), (1e9, "mld"), (1e6, "mln")):
        if abs(x) >= lim:
            return f"{B.f_num(x / lim, 1)} {unit}"
    return B.f_num(x, 0)


def _today(ctx, pf) -> pd.Timestamp:
    return pf.as_of if ctx.is_demo else max(pd.Timestamp.today().normalize(), pf.as_of)


def _is_company(sym: str, row: dict) -> bool:
    """Spółka (ma raporty i wiadomości): akcja albo CFD na akcję. ETF-y i CFD na indeksy - nie."""
    return row["asset_class"] == "STOCK" or (row["asset_class"] == "CFD" and str(sym).endswith("_4"))


def _briefs(ctx, items, today) -> dict:
    """Karty spółek pobrane równolegle. Błąd jednej nie psuje pozostałych."""
    def one(item):
        sym, row = item
        parts = ("quote", "events", "news") if _is_company(sym, row) else ("quote",)
        try:
            return sym, ctx.provider.brief(row, today, parts), None
        except Exception as exc:
            return sym, None, str(exc)
    workers = int(getattr(ctx.provider, "max_workers", 1) or 1)
    if workers > 1 and len(items) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            res = list(pool.map(one, items))
    else:
        res = [one(i) for i in items]
    return {sym: (brief, err) for sym, brief, err in res}


def companies_list(ctx, p) -> dict:
    pf = ctx.pf_full or ctx.pf                      # lista jest zawsze "na dziś", niezależnie od okresu analizy
    equity = float(pf.equity.iloc[-1])
    qty = pf.qty.iloc[-1]
    held = [s for s in qty.index if abs(qty[s]) > 1e-12]
    if not held or equity <= 0:
        return {"title": "Lista", "blocks": [B.statement("W portfelu nie ma otwartych pozycji.",
                                                         "Lista pokazuje instrumenty, które masz teraz na rachunku.")]}
    m = ctx.mapping.set_index("symbol")
    items = [(s, m.loc[s].to_dict() | {"symbol": s}) for s in held if s in m.index]
    today = _today(ctx, pf)
    # notowania odświeżają się co 10 minut (klucz z numerem dziesięciominutowego okna)
    briefs = ctx.memo(("briefs", str(today.date()), int(time.time() // 600)), lambda: _briefs(ctx, items, today))
    exposure = pf.current_exposure()
    avg_open = C.avg_open_price(ctx.export_raw.open) if ctx.export_raw is not None else pd.Series(dtype=float)

    rows, reports, divs, news, failed, stale = [], [], [], [], [], []
    quote_times = []
    for sym, row in items:
        brief, _err = briefs.get(sym, (None, None))
        name = str(row.get("name") or sym)
        hist = ctx.market.prices[sym] if sym in ctx.market.prices.columns else pd.Series(dtype=float)
        factor = float(row.get("price_factor") or 1.0)
        q = (brief or {}).get("quote")
        if q:
            q = {**q, "price": q["price"] * factor, "prev_close": q["prev_close"] * factor}
        else:
            q = C.quote_from_history(hist)
            if q:
                stale.append(name)
        if not q:
            failed.append(name)
            continue
        if q.get("time"):
            quote_times.append(str(q["time"]))
        chg = C.day_change(q["price"], q["prev_close"])
        e = float(exposure.get(sym, 0.0))
        cur = str(row.get("currency") or "")
        is_cfd = row["asset_class"] == "CFD"
        r = {"sym": sym, "name": name, "cls": CLASS_PL.get(row["asset_class"], row["asset_class"]),
             "price": f"{B.f_num(q['price'], 2)} {cur}".strip() if not (is_cfd and not str(sym).endswith("_4")) else B.f_num(q["price"], 2),
             "chg": chg, "day": e * chg if chg == chg else np.nan, "weight": e / equity,
             "since": C.since_purchase(q["price"], avg_open.get(sym), 1.0 if e >= 0 else -1.0),
             "off_high": C.off_high(hist, q["price"]), "report": "–", "report_days": 10 ** 6}
        ev = (brief or {}).get("events") if _is_company(sym, row) else None
        if ev:
            ecur = ev.get("currency") or cur
            nxt = [pd.Timestamp(x) for x in ev.get("earnings", []) if pd.Timestamp(x) >= today]
            if nxt:
                d = nxt[0]
                days = int((d - today).days)
                fp = C.fiscal_period(d, ev.get("fy_end_month"))
                approx = bool(ev.get("estimated")) or len(ev.get("earnings", [])) > 1
                r["report"], r["report_days"] = f"{d:%d.%m}{'*' if approx else ''} · {_in_days(days)}", days
                reports.append({"date": d, "name": name, "when": f"{WD[d.weekday()]} {d:%d.%m.%Y}", "days": days,
                                "what": fp["label"], "status": "szacunkowa" if approx else "potwierdzona",
                                "weight": abs(e) / equity,
                                "eps": f"{B.f_num(ev['eps_avg'], 2)} {ecur}" if ev.get("eps_avg") is not None else "–",
                                "rev": f"{_big(ev['rev_avg'])} {ecur}" if ev.get("rev_avg") is not None else "–"})
            if ev.get("ex_dividend") and pd.Timestamp(ev["ex_dividend"]) >= today:
                dd = pd.Timestamp(ev["ex_dividend"])
                divs.append({"date": dd, "name": name, "when": f"{dd:%d.%m.%Y}", "days": int((dd - today).days),
                             "rate": f"{B.f_num(ev['dividend_rate'], 2)} {ecur}" if ev.get("dividend_rate") else "–",
                             "pay": f"{pd.Timestamp(ev['dividend_date']):%d.%m.%Y}" if ev.get("dividend_date") else "–"})
        for n in ((brief or {}).get("news") or []) if _is_company(sym, row) else []:
            news.append({**n, "tag": name, "sym": sym})
        rows.append(r)

    if not rows:
        return {"title": "Lista", "blocks": [
            B.statement("Nie udało się pobrać notowań dla pozycji z portfela.", "Spróbuj ponownie za chwilę."),
            B.note("Sprawdź tickery Yahoo w „Dane i ustawienia → Tickery”.", "warn")]}

    t = pd.DataFrame(rows)
    sort = p.str("sort", "weight", [s[0] for s in SORTS])
    t = (t.sort_values("chg", ascending=False) if sort == "change"
         else t.sort_values(["report_days", "name"]) if sort == "report"
         else t.reindex(t["weight"].abs().sort_values(ascending=False).index))
    br = C.breadth(t.set_index("name")["chg"], t.set_index("name")["weight"])
    day_total = float(t["day"].sum(skipna=True))
    reports.sort(key=lambda x: x["date"])
    divs.sort(key=lambda x: x["date"])

    verb = _plural(br["up"], "rośnie", "rosną", "rośnie")
    lead = (f"Na ostatniej sesji {verb} {br['up']} z {br['n']} pozycji; razem zmieniają wartość portfela o "
            f"{B.f_pct(br['contribution'], 2, sign=True)} ({'+' if day_total > 0 else ''}{B.f_pln(day_total)}).")
    ch = t.set_index("name")["chg"].dropna()
    sub = (f"Najmocniej w górę: {ch.idxmax()} {B.f_pct(ch.max(), 1, sign=True)}. "
           f"Najmocniej w dół: {ch.idxmin()} {B.f_pct(ch.min(), 1, sign=True)}.") if len(ch) > 1 else None

    in30 = [x for x in reports if x["days"] <= 30]
    top = [
        B.metric("Pozycje na plusie", f"{br['up']} z {br['n']}", "text",
                 "Ile pozycji z portfela zakończyło ostatnią sesję wyżej niż poprzednią."),
        B.metric("Zmiana wartości pozycji", br["contribution"], "pct", digits=2, sign=True,
                 tip="Suma (waga pozycji × jej zmiana dzienna). Szacunek: liczy od wartości pozycji z daty raportu XTB "
                     "i nie uwzględnia zmiany kursów walut.", note=f"{'+' if day_total > 0 else ''}{B.f_pln(day_total)}"),
    ]
    if reports:
        f = reports[0]
        top.append(B.metric("Najbliższy raport", f["name"], "text",
                            "Spółka z portfela, która jako pierwsza opublikuje wyniki finansowe.",
                            note=f"{f['when']} · {_in_days(f['days'])} · {f['what']}"))
        top.append(B.metric("Raporty w ciągu 30 dni", len(in30), "int",
                            "Ile spółek z portfela publikuje wyniki w najbliższych 30 dniach i jaką część kapitału stanowią. "
                            "Wokół publikacji kursy ruszają się zwykle mocniej.",
                            note=f"{B.f_pct(sum(x['weight'] for x in in30), 0)} kapitału"))

    blocks = [
        B.statement(lead, sub),
        B.controls([B.control("sort", "segmented", "Kolejność", sort, SORTS)]),
        B.table(t, [("name", "Pozycja", "text"), ("price", "Cena", "text"), ("chg", "Dzień", "pct", 2, True),
                    ("day", "Dzień (kwota)", "pln", 0, True), ("weight", "Waga", "pct", 1),
                    ("since", "Od zakupu", "pct", 1, True), ("off_high", "Od szczytu 52 tyg.", "pct", 1),
                    ("report", "Raport", "text"), ("cls", "Rodzaj", "text")],
                "Twoje pozycje",
                "Dzień = zmiana ceny od poprzedniego zamknięcia. Kwota to szacunek w walucie wyświetlania. Od zakupu = cena "
                "wobec średniej ceny otwarcia (bez walut, dywidend i prowizji). Gwiazdka przy dacie raportu = data szacunkowa.",
                limit=25),
        B.chart(B.hbar(t.set_index("name")["chg"].dropna().sort_values(ascending=False), "pct", 2, signed=True),
                "Zmiana dzienna", "Zmiana ceny instrumentu od poprzedniego zamknięcia."),
        B.metrics(top, "W skrócie"),
    ]

    if reports:
        r = pd.DataFrame(reports)
        blocks.append(B.table(r, [("name", "Spółka", "text"), ("when", "Data", "text"), ("days", "Za ile dni", "int"),
                                  ("what", "Raport", "text"), ("status", "Data jest", "text"), ("weight", "Waga", "pct", 1),
                                  ("eps", "Konsensus EPS", "text"), ("rev", "Konsensus przychodów", "text")],
                              "Najbliższe raporty kwartalne i roczne",
                              "Kwartał ustalam z daty publikacji i końca roku obrotowego spółki; Q4 to zarazem wyniki roczne. "
                              "Konsensus to średnia prognoz analityków. Data „szacunkowa” nie została jeszcze ogłoszona przez spółkę.",
                              limit=15))
    else:
        blocks.append(B.note("Nie znalazłem zapowiedzianych terminów raportów dla spółek z portfela."))
    if divs:
        blocks.append(B.table(pd.DataFrame(divs), [("name", "Spółka", "text"), ("when", "Dzień bez prawa do dywidendy", "text"),
                                                   ("days", "Za ile dni", "int"), ("rate", "Dywidenda roczna / akcję", "text"),
                                                   ("pay", "Wypłata", "text")], "Nadchodzące dywidendy",
                              "Żeby dostać dywidendę, akcje trzeba mieć przed tym dniem (ex-dividend). Tego dnia kurs zwykle "
                              "spada o kwotę dywidendy.", limit=10))

    # wiadomości: najnowsze, najwyżej 3 na spółkę, z filtrem
    names = sorted({n["sym"]: n["tag"] for n in news}.items(), key=lambda kv: kv[1])
    pick = p.str("news", "", [""] + [s for s, _ in names])
    news.sort(key=lambda n: n.get("time") or "", reverse=True)
    if pick:
        shown = [n for n in news if n["sym"] == pick][:8]
    else:
        seen, shown = {}, []
        for n in news:
            if seen.get(n["sym"], 0) < 3:
                seen[n["sym"]] = seen.get(n["sym"], 0) + 1
                shown.append(n)
        shown = shown[:15]
    if names:
        blocks.append(B.feed([{k: n.get(k) for k in ("title", "source", "time", "url", "tag")} for n in shown],
                             "Wiadomości o spółkach",
                             "Nagłówki z Yahoo Finance, zwykle po angielsku; odnośnik otwiera artykuł u wydawcy. "
                             "To przegląd prasy, nie rekomendacja."))
        blocks.append(B.controls([B.control("news", "select", "Pokaż wiadomości", pick,
                                            [("", "Wszystkie spółki")] + names)]))

    if stale:
        blocks.append(B.note("Bez bieżącego notowania (pokazuję ostatnie zamknięcie z historii): " + ", ".join(stale[:8])
                             + (" i inne" if len(stale) > 8 else "") + "."))
    if failed:
        blocks.append(B.note("Brak notowań dla: " + ", ".join(failed[:8]) + ". Sprawdź ticker w zakładce Tickery.", "warn"))
    latest = max(quote_times)[:10] if quote_times else None
    src = "dane przykładowe (zmyślone)" if ctx.is_demo else "Yahoo Finance"
    blocks.append(B.note(f"Źródło: {src}" + (f", najnowsze notowanie z {pd.Timestamp(latest):%d.%m.%Y}" if latest else "")
                         + ". Ceny mogą być opóźnione o kilkanaście minut; lista odświeża się co 10 minut. Skład portfela "
                         f"pochodzi z raportu XTB z {(ctx.report_date or pf.as_of):%d.%m.%Y} i nie zależy od wybranego okresu analizy. "
                         "Terminy raportów potrafią się zmieniać."))
    return {"title": "Lista", "blocks": blocks}
