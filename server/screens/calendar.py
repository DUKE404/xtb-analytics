"""Kalendarz: dzienne stopy zwrotu w kolorach oraz terminy wyników kwartalnych / rocznych i dywidend spółek z portfela."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from core import calendar as K
from server import blocks as B

MONTH_CHOICES = [(3, "3 mies."), (6, "6 mies."), (12, "12 mies."), (0, "Cały okres")]


def _plural(n: int, one: str, few: str, many: str) -> str:
    return one if n == 1 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _session(d) -> str:
    return f"{K.WEEKDAYS_PL[d.weekday()]} {d:%d.%m.%Y}"


def days(ctx, p) -> dict:
    pf = ctx.pf
    r = pf.returns
    r = r[r.index > pf.dates[0]] if len(r) > 1 else r          # pierwszy dzień okresu nie ma stopy
    if not len(r):
        return {"title": "Stopy dzienne", "blocks": [B.note("Za mało sesji w wybranym okresie.")]}
    pnl = pf.pnl.sum(axis=1)
    bench = ctx.bench_series(p.bench)
    bname = ctx.bench_label(p.bench)
    months = p.int("months", 6, 0, 120)
    grid = K.month_grid(r, pnl, bench, months or None)
    st, sk = K.day_stats(r), K.streaks(r)
    last = r.index[-1]
    lv, lp = float(r.iloc[-1]), float(pnl.get(last, np.nan))
    lead = (f"Ostatnia sesja ({_session(last)}): {B.f_pct(lv, 2, sign=True)}, czyli "
            f"{'+' if lp > 0 else ''}{B.f_pln(lp)}.")
    if sk["current"]:
        n = abs(sk["current"])
        sub = f"To {n}. {'sesja z rzędu na plusie' if sk['current'] > 0 else 'sesja z rzędu na minusie'}." if n > 1 else None
    else:
        sub = None
    if sub is None and st["n"]:
        sub = f"Na plusie kończy się {B.f_pct(st['positive'], 0)} sesji."

    blocks = [
        B.statement(lead, sub),
        B.chart(B.calendar(grid, "pct", 1, K.robust_scale(r), bname), "Stopy dzienne portfela",
                "Zielony = zysk, czerwony = strata; mocniejszy kolor = większy ruch. W nagłówku miesiąca stopa "
                "za cały miesiąc i wynik kwotowy. Puste pola to dni bez sesji."),
        B.controls([B.control("months", "segmented", "Pokaż", months, MONTH_CHOICES)]),
        B.metrics([
            B.metric("Sesje na plusie", st["positive"], "pct", f"Odsetek sesji zakończonych zyskiem ({st['n']} sesji z ruchem).", digits=0),
            B.metric("Najlepszy dzień", st["best"], "pct", "Najwyższa dzienna stopa zwrotu w okresie.", digits=2, sign=True,
                     note=_session(st["best_day"]) if st["best_day"] is not None else None),
            B.metric("Najgorszy dzień", st["worst"], "pct", "Najniższa dzienna stopa zwrotu w okresie.", digits=2, sign=True,
                     note=_session(st["worst_day"]) if st["worst_day"] is not None else None),
            B.metric("Średni dzień wzrostowy", st["avg_up"], "pct", "Średnia stopa w dni zakończone zyskiem.", digits=2, sign=True),
            B.metric("Średni dzień spadkowy", st["avg_down"], "pct", "Średnia stopa w dni zakończone stratą.", digits=2, sign=True),
            B.metric("Zysk / strata w średni dzień", st["win_loss"], "x", "Średni dzień wzrostowy podzielony przez średni spadkowy "
                     "(co do wartości). Powyżej 1: dobre dni są większe niż złe.", digits=2),
            B.metric("Najdłuższa seria wzrostów", sk["longest_up"], "int", "Najwięcej sesji z rzędu zakończonych zyskiem.",
                     note=_plural(sk["longest_up"], "sesja", "sesje", "sesji")),
            B.metric("Najdłuższa seria spadków", sk["longest_down"], "int", "Najwięcej sesji z rzędu zakończonych stratą.",
                     note=_plural(sk["longest_down"], "sesja", "sesje", "sesji")),
        ], "Dni w liczbach"),
    ]

    wd = K.by_weekday(r).to_frame("Średnia stopa")
    blocks.append(B.chart(B.bars(wd, "pct", 2, {"Średnia stopa": 0}), "Średnia stopa według dnia tygodnia",
                          "Przy kilkuset sesjach różnice między dniami są zwykle przypadkowe."))

    # ruchy pozycji w wybranej sesji
    sessions = list(r.index[-20:][::-1])
    opts = [(d.strftime("%Y-%m-%d"), _session(d)) for d in sessions]
    day = p.str("day", opts[0][0], [o[0] for o in opts])
    d = pd.Timestamp(day)
    i = pf.dates.get_loc(d)
    if i > 0:
        mv = K.position_moves(pf.pnl.loc[d, [c for c in pf.qty.columns]], pf.exposure.iloc[i - 1])
        mv = mv[(mv["pnl"].abs() > 1e-9)]
        names = pf.meta["name"].reindex(mv.index).fillna(pd.Series(mv.index, index=mv.index))
        mv = mv.assign(name=names.values).sort_values("ret", ascending=False)
        blocks.append(B.controls([B.control("day", "select", "Sesja", day, opts)]))
        if len(mv):
            blocks.append(B.chart(B.hbar(mv.set_index("name")["ret"], "pct", 2, signed=True),
                                  f"Ruchy pozycji: {_session(d)}",
                                  f"Portfel: {B.f_pct(float(r.loc[d]), 2, sign=True)}. Stopa pozycji = jej wynik tego dnia "
                                  "podzielony przez jej wartość z poprzedniej sesji (dla CFD: nominał)."))
            blocks.append(B.table(mv, [("name", "Pozycja", "text"), ("ret", "Stopa", "pct", 2, True),
                                       ("pnl", "Wynik", "pln", 0, True)], "Wynik pozycji tej sesji", limit=12))

    t = pd.DataFrame({"date": r.index[::-1][:30]})
    t["label"] = t["date"].map(lambda x: f"{x:%d.%m.%Y}")
    t["wd"] = t["date"].map(lambda x: K.WEEKDAYS_SHORT[x.weekday()] if x.weekday() < 5 else "")
    t["ret"] = r.reindex(t["date"]).values
    t["pnl"] = pnl.reindex(t["date"]).values
    t["bench"] = bench.reindex(t["date"]).values if len(bench) else np.nan
    t["diff"] = t["ret"] - t["bench"]
    blocks.append(B.table(t, [("label", "Sesja", "text"), ("wd", "Dzień", "text"), ("ret", "Portfel", "pct", 2, True),
                              ("pnl", "Wynik", "pln", 0, True), ("bench", bname, "pct", 2, True),
                              ("diff", "Różnica", "pct", 2, True)], "Ostatnie sesje", limit=10))
    return {"title": "Stopy dzienne", "blocks": blocks}


# --------------------------------------------------------------------------- terminy wyników

def _today(ctx) -> pd.Timestamp:
    return (ctx.pf_full or ctx.pf).as_of if ctx.is_demo else max(pd.Timestamp.today().normalize(), (ctx.pf_full or ctx.pf).as_of)


def _holdings(ctx) -> list[tuple[str, dict, float]]:
    """Spółki z portfela (akcje i CFD na akcje), jedna na ticker Yahoo, z wagą w kapitale."""
    pf = ctx.pf_full or ctx.pf
    equity = float(pf.equity.iloc[-1])
    exp = pf.current_exposure()
    m = ctx.mapping.set_index("symbol")
    out, seen = [], {}
    for sym, e in exp.items():
        if abs(e) < 1e-9 or sym not in m.index:
            continue
        row = m.loc[sym].to_dict() | {"symbol": sym}
        if not (row["asset_class"] == "STOCK" or (row["asset_class"] == "CFD" and str(sym).endswith("_4"))):
            continue
        key = str(row.get("yf_ticker") or sym)
        w = float(e) / equity if equity > 0 else np.nan
        if key in seen:
            out[seen[key]] = (out[seen[key]][0], out[seen[key]][1], out[seen[key]][2] + w)
            continue
        seen[key] = len(out)
        out.append((sym, row, w))
    return out


def _fetch_all(ctx, holdings, today):
    def one(item):
        sym, row, w = item
        try:
            return sym, row, w, ctx.provider.events(row, today), None
        except Exception as exc:
            return sym, row, w, None, str(exc)
    workers = int(getattr(ctx.provider, "max_workers", 1) or 1)
    if workers > 1 and len(holdings) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(one, holdings))
    return [one(h) for h in holdings]


def _big(x) -> str:
    if x is None or not isinstance(x, (int, float)) or not math.isfinite(x):
        return "–"
    for lim, unit in ((1e12, "bln"), (1e9, "mld"), (1e6, "mln")):
        if abs(x) >= lim:
            return f"{B.f_num(x / lim, 1)} {unit}"
    return B.f_num(x, 0)


def events(ctx, p) -> dict:
    holdings = _holdings(ctx)
    if not holdings:
        return {"title": "Wyniki spółek", "blocks": [B.statement("W portfelu nie ma akcji pojedynczych spółek.",
                                                                 "Terminy wyników dotyczą spółek; ETF-y i CFD na indeksy ich nie mają.")]}
    today = _today(ctx)
    res = ctx.memo(("events", str(today.date())), lambda: _fetch_all(ctx, holdings, today))
    horizon = p.int("horizon", 90, 7, 365)
    rows, divs, past, failed = [], [], [], []
    for sym, row, w, ev, err in res:
        name = str(row.get("name") or sym)
        if ev is None:
            failed.append(name)
            continue
        cur = ev.get("currency") or row.get("currency") or ""
        nxt = [pd.Timestamp(x) for x in ev.get("earnings", []) if pd.Timestamp(x) >= today]
        if nxt:
            d = nxt[0]
            fp = K.fiscal_period(d, ev.get("fy_end_month"))
            approx = ev.get("estimated") or len(ev.get("earnings", [])) > 1
            rows.append({"date": d, "name": name, "what": fp["label"], "annual": fp["annual"], "weight": w,
                         "days": int((d - today).days), "approx": "szacunkowa" if approx else "potwierdzona",
                         "eps": f"{B.f_num(ev['eps_avg'], 2)} {cur}" if ev.get("eps_avg") is not None else "–",
                         "rev": f"{_big(ev.get('rev_avg'))} {cur}" if ev.get("rev_avg") is not None else "–", "sym": sym})
        if ev.get("ex_dividend") and pd.Timestamp(ev["ex_dividend"]) >= today:
            dd = pd.Timestamp(ev["ex_dividend"])
            divs.append({"date": dd, "name": name, "days": int((dd - today).days), "weight": w,
                         "rate": f"{B.f_num(ev['dividend_rate'], 2)} {cur}" if ev.get("dividend_rate") else "–",
                         "pay": f"{pd.Timestamp(ev['dividend_date']):%d.%m.%Y}" if ev.get("dividend_date") else "–"})
        if ev.get("last_quarter_end") and ev.get("last_eps_actual") is not None:
            past.append({"name": name, "q": f"{pd.Timestamp(ev['last_quarter_end']):%m.%Y}",
                         "act": ev["last_eps_actual"], "est": ev.get("last_eps_estimate"), "surprise": ev.get("last_surprise")})
    rows.sort(key=lambda x: x["date"])
    divs.sort(key=lambda x: x["date"])
    soon = [x for x in rows if x["days"] <= horizon]

    if rows:
        f = rows[0]
        when = "dziś" if f["days"] == 0 else "jutro" if f["days"] == 1 else f"za {f['days']} dni"
        lead = f"Najbliżej: {f['name']} - {f['what']}, {_session(f['date'])} ({when})."
        n30 = [x for x in rows if x["days"] <= 30]
        sub = (f"W ciągu 30 dni {len(n30)} {_plural(len(n30), 'spółka publikuje', 'spółki publikują', 'spółek publikuje')} "
               f"wyniki - razem {B.f_pct(sum(abs(x['weight']) for x in n30), 0)} kapitału.") if n30 else \
            "W ciągu najbliższych 30 dni żadna spółka z portfela nie publikuje wyników."
    else:
        lead, sub = "Nie znalazłem zapowiedzianych terminów wyników dla spółek z portfela.", None

    blocks = [B.statement(lead, sub)]
    # kalendarz z zaznaczonymi dniami
    if soon:
        marks: dict[pd.Timestamp, list[str]] = {}
        for x in soon:
            marks.setdefault(x["date"], []).append(f"{x['name']}: {x['what']}")
        for x in divs:
            if x["days"] <= horizon:
                marks.setdefault(x["date"], []).append(f"{x['name']}: prawo do dywidendy")
        last = max(marks)
        months, y, m = [], today.year, today.month
        while (y, m) <= (last.year, last.month) and len(months) < 4:
            n = pd.Period(f"{y}-{m:02d}").days_in_month
            ds = []
            for dday in range(1, n + 1):
                dt = pd.Timestamp(year=y, month=m, day=dday)
                ds.append({"mark": "; ".join(marks[dt])} if dt in marks else None)
            months.append({"label": f"{K.MONTHS_PL[m - 1]} {y}", "year": y, "month": m,
                           "lead": pd.Timestamp(year=y, month=m, day=1).weekday(), "days": ds})
            y, m = (y, m + 1) if m < 12 else (y + 1, 1)
        blocks.append(B.chart(B.calendar(months, hint="Dotknij zaznaczonego dnia, żeby zobaczyć, kto raportuje"),
                              "Kalendarz raportów", "Zaznaczone dni: wyniki spółek i dni ustalenia prawa do dywidendy."))
    blocks.append(B.controls([B.control("horizon", "segmented", "Horyzont", horizon,
                                        [(30, "30 dni"), (90, "90 dni"), (180, "180 dni")])]))
    if soon:
        t = pd.DataFrame(soon)
        t["when"] = t["date"].map(lambda x: f"{x:%d.%m.%Y}")
        t["wd"] = t["date"].map(lambda x: K.WEEKDAYS_SHORT[x.weekday()] if x.weekday() < 5 else "")
        blocks.append(B.table(t, [("name", "Spółka", "text"), ("when", "Data", "text"), ("wd", "Dzień", "text"),
                                  ("days", "Za ile dni", "int"), ("what", "Raport", "text"), ("approx", "Data jest", "text"),
                                  ("weight", "Waga w portfelu", "pct", 1), ("eps", "Konsensus EPS", "text"),
                                  ("rev", "Konsensus przychodów", "text")], "Nadchodzące wyniki",
                              "Kwartał ustalam z daty publikacji i końca roku obrotowego spółki; Q4 to jednocześnie wyniki "
                              "roczne. Data „szacunkowa” to przewidywanie, spółka jej jeszcze nie ogłosiła.", limit=15))
        w30 = pd.Series({x["name"]: x["weight"] for x in soon if x["days"] <= 30})
        if len(w30):
            blocks.append(B.chart(B.hbar(w30.sort_values(ascending=False), "pct", 1), "Część portfela z wynikami w ciągu 30 dni",
                                  "Wokół publikacji wyników kursy ruszają się zwykle mocniej niż w inne dni."))
    if divs:
        t = pd.DataFrame(divs)
        t["when"] = t["date"].map(lambda x: f"{x:%d.%m.%Y}")
        blocks.append(B.table(t, [("name", "Spółka", "text"), ("when", "Prawo do dywidendy", "text"), ("days", "Za ile dni", "int"),
                                  ("rate", "Dywidenda roczna / akcję", "text"), ("pay", "Wypłata", "text")],
                              "Nadchodzące dywidendy", "Żeby dostać dywidendę, akcje trzeba mieć przed dniem ustalenia prawa "
                                                       "(ex-dividend). Tego dnia kurs zwykle spada o kwotę dywidendy.", limit=10))
    if past:
        t = pd.DataFrame(past)
        blocks.append(B.table(t, [("name", "Spółka", "text"), ("q", "Kwartał do", "text"), ("act", "EPS faktyczny", "num", 2),
                                  ("est", "EPS oczekiwany", "num", 2), ("surprise", "Niespodzianka", "pct", 1, True)],
                              "Ostatnie wyniki względem oczekiwań",
                              "Niespodzianka = o ile zysk na akcję różnił się od średniej prognoz analityków.", limit=15))
    if failed:
        blocks.append(B.note("Brak danych dla: " + ", ".join(failed[:8]) + (" i innych" if len(failed) > 8 else "")
                             + ". Sprawdź ticker Yahoo w zakładce Tickery.", "warn"))
    src = next((ev.get("source") for *_, ev, _e in res if ev), "Yahoo Finance")
    blocks.append(B.note(f"Źródło: {src}. Terminy potrafią się zmieniać - przed ważną decyzją sprawdź je w relacjach "
                         "inwestorskich spółki."))
    return {"title": "Wyniki spółek", "blocks": blocks}
