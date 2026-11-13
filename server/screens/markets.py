"""Rynki: rentowności obligacji USA i krzywa, indeksy, VIX, waluty, surowce oraz kalendarz danych makro.

Ekrany nie zależą od portfela ani od okresu analizy - pokazują stan rynku „na dziś”. Dane pobiera dostawca
(`ctx.provider.markets()`, patrz data/markets.py); w trybie demo są zmyślone. Tylko opis rynku, bez rekomendacji.
"""
from __future__ import annotations

import math
import re
import time

import pandas as pd

from core import markets as M
from data.markets import GROUPS, INSTRUMENTS, TENOR_LABELS, TENORS
from server import blocks as B

WD = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
WD_SHORT = ["pn.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz."]
COUNTRIES = {"USD": "USA", "EUR": "Strefa euro", "GBP": "Wlk. Brytania", "JPY": "Japonia", "CNY": "Chiny",
             "CHF": "Szwajcaria", "CAD": "Kanada", "AUD": "Australia", "NZD": "Nowa Zelandia", "ALL": "Świat"}
IMPACT = {"High": "●●●", "Medium": "●●○", "Low": "●○○", "Holiday": "święto"}
REGIONS = [("us", "USA"), ("major", "USA i Europa"), ("all", "Wszystkie")]
REGION_SETS = {"us": {"USD"}, "major": {"USD", "EUR", "GBP", "CHF"}, "all": None}
IMPACTS = [("high", "Najważniejsze"), ("medium", "Ważne"), ("all", "Wszystkie")]
IMPACT_SETS = {"high": {"High"}, "medium": {"High", "Medium"}, "all": {"High", "Medium", "Low", "Holiday"}}

# najczęstsze publikacje z kalendarza (po angielsku w źródle)
TITLES_PL = {
    "CPI m/m": "Inflacja CPI m/m", "CPI y/y": "Inflacja CPI r/r", "Core CPI m/m": "Inflacja bazowa CPI m/m",
    "Core CPI y/y": "Inflacja bazowa CPI r/r", "PPI m/m": "Ceny producentów PPI m/m", "Core PPI m/m": "Bazowe PPI m/m",
    "PPI y/y": "Ceny producentów PPI r/r", "Core PCE Price Index m/m": "Bazowy deflator PCE m/m",
    "Core PCE Price Index y/y": "Bazowy deflator PCE r/r", "PCE Price Index m/m": "Deflator PCE m/m",
    "PCE Price Index y/y": "Deflator PCE r/r", "Non-Farm Employment Change": "Zatrudnienie poza rolnictwem (NFP)",
    "Unemployment Rate": "Stopa bezrobocia", "Average Hourly Earnings m/m": "Średnie płace godzinowe m/m",
    "Unemployment Claims": "Nowe wnioski o zasiłek", "ADP Non-Farm Employment Change": "Zatrudnienie wg ADP",
    "JOLTS Job Openings": "Wolne miejsca pracy (JOLTS)", "Federal Funds Rate": "Decyzja Fed w sprawie stóp",
    "FOMC Statement": "Komunikat FOMC", "FOMC Press Conference": "Konferencja prezesa Fed",
    "FOMC Meeting Minutes": "Protokół z posiedzenia FOMC", "FOMC Economic Projections": "Projekcje gospodarcze FOMC",
    "Advance GDP q/q": "PKB q/q (wstępny)", "Prelim GDP q/q": "PKB q/q (drugi szacunek)",
    "Final GDP q/q": "PKB q/q (ostateczny)", "Retail Sales m/m": "Sprzedaż detaliczna m/m",
    "Core Retail Sales m/m": "Bazowa sprzedaż detaliczna m/m", "ISM Manufacturing PMI": "ISM przemysł",
    "ISM Services PMI": "ISM usługi", "Flash Manufacturing PMI": "PMI przemysł (wstępny)",
    "Flash Services PMI": "PMI usługi (wstępny)", "Prelim UoM Consumer Sentiment": "Nastroje konsumentów UMich (wstępne)",
    "Revised UoM Consumer Sentiment": "Nastroje konsumentów UMich (finalne)", "CB Consumer Confidence": "Zaufanie konsumentów (Conference Board)",
    "Prelim UoM Inflation Expectations": "Oczekiwania inflacyjne UMich (wstępne)", "Durable Goods Orders m/m": "Zamówienia dóbr trwałych m/m",
    "Core Durable Goods Orders m/m": "Bazowe zamówienia dóbr trwałych m/m", "Building Permits": "Pozwolenia na budowę",
    "Housing Starts": "Rozpoczęte budowy domów", "Existing Home Sales": "Sprzedaż domów na rynku wtórnym",
    "New Home Sales": "Sprzedaż nowych domów", "Pending Home Sales m/m": "Umowy kupna domów m/m",
    "Industrial Production m/m": "Produkcja przemysłowa m/m", "Empire State Manufacturing Index": "Indeks Empire State",
    "Philly Fed Manufacturing Index": "Indeks Fed z Filadelfii", "Trade Balance": "Bilans handlowy",
    "Crude Oil Inventories": "Zapasy ropy (EIA)", "Natural Gas Storage": "Zapasy gazu",
    "Employment Cost Index q/q": "Koszty zatrudnienia q/q", "Prelim Nonfarm Productivity q/q": "Wydajność pracy q/q",
    "Treasury Currency Report": "Raport walutowy Departamentu Skarbu", "Bank Holiday": "Święto (rynki zamknięte)",
    "CPI Flash Estimate y/y": "Inflacja HICP r/r (szybki szacunek)", "Core CPI Flash Estimate y/y": "Bazowa HICP r/r (szybki szacunek)",
    "Main Refinancing Rate": "Decyzja EBC w sprawie stóp", "Monetary Policy Statement": "Komunikat o polityce pieniężnej",
    "ECB Press Conference": "Konferencja prezes EBC", "German ZEW Economic Sentiment": "Niemcy: nastroje ZEW",
    "German Ifo Business Climate": "Niemcy: klimat biznesu Ifo", "German Prelim CPI m/m": "Niemcy: inflacja CPI m/m (wstępna)",
    "Official Bank Rate": "Decyzja Banku Anglii w sprawie stóp", "Fed Chair Powell Speaks": "Wystąpienie prezesa Fed",
}


def _title(t: str) -> str:
    if t in TITLES_PL:
        return TITLES_PL[t]
    if t.startswith("FOMC Member ") and t.endswith(" Speaks"):
        return f"Wystąpienie członka FOMC ({t[12:-7]})"
    if t.startswith("Fed Chair ") and t.endswith(" Speaks"):
        return f"Wystąpienie prezesa Fed ({t[10:-7]})"
    return t


_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")


def _pl_number(v: str | None) -> str:
    """'0.3%' -> '0,3%' (polski przecinek dziesiętny w prognozach ze źródła)."""
    return _DECIMAL.sub(",", v) if v else "–"


def _local(ts: pd.Timestamp) -> pd.Timestamp:
    try:
        return ts.tz_convert("Europe/Warsaw")
    except Exception:  # pragma: no cover - brak bazy stref czasowych
        return ts


def _plural(n: int, one: str, few: str, many: str) -> str:
    return one if n == 1 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _in_days(n: int) -> str:
    return "dziś" if n == 0 else "jutro" if n == 1 else f"za {n} {_plural(n, 'dzień', 'dni', 'dni')}"


def _data(ctx) -> dict | None:
    """Dane rynkowe raz na 10 minut na sesję (dostawca ma własny, wspólny cache)."""
    fn = getattr(ctx.provider, "markets", None)
    if fn is None:
        return None

    def load():
        try:
            return fn()
        except Exception as exc:
            return {"error": str(exc)}
    return ctx.memo(("markets", int(time.time() // 600)), load)


def _unavailable(title: str, what: str, d: dict | None) -> dict:
    blocks = [B.statement(f"Nie udało się pobrać danych: {what}.", "Spróbuj ponownie za kilka minut.")]
    for w in (d or {}).get("warnings", [])[:4]:
        blocks.append(B.note(w, "warn"))
    if d and d.get("error"):
        blocks.append(B.note(f"Szczegóły: {d['error'][:200]}", "warn"))
    return {"title": title, "blocks": blocks}


def _src_note(d: dict, key: str, extra: str = "") -> dict:
    src = d.get(key) or "–"
    return B.note(f"Źródło: {src}.{(' ' + extra) if extra else ''} To opis sytuacji rynkowej, nie rekomendacja.")


def _bp(x) -> float:
    return x * 100 if isinstance(x, float) and math.isfinite(x) else math.nan


def _sgn_bp(x) -> str:
    v = _bp(x)
    if not math.isfinite(v):
        return "–"
    r = int(round(v))
    return f"{'+' if r > 0 else ''}{B.f_num(r, 0)} pb"


def _sgn_pp(x) -> str:
    if not (isinstance(x, float) and math.isfinite(x)):
        return "–"
    return f"{'+' if x > 0 else ''}{B.f_num(x, 2)} pp"


# --------------------------------------------------------------------------- #
# Stopy USA
# --------------------------------------------------------------------------- #

def rates(ctx, p) -> dict:
    title = "Rentowności USA"
    d = _data(ctx)
    y = (d or {}).get("yields")
    if y is None or not len(y):
        return _unavailable(title, "rentowności obligacji USA", d)
    y = y.dropna(how="all").sort_index()
    snaps = M.curve_snapshots(y)
    today_date, today = snaps["Dziś"]
    tenors = [t for t in TENORS if t in y.columns and math.isfinite(float(today.get(t, math.nan)))]
    ch = {t: M.changes(y[t], diff=True) for t in tenors}
    shape = M.curve_shape(today)
    s210, s310 = M.spread(y, "10Y", "2Y"), M.spread(y, "10Y", "3M")

    key = "10Y" if "10Y" in ch else tenors[-1]
    k = ch[key]
    lead = (f"Rentowność 10-letnich obligacji USA wynosi {B.f_num(k['last'], 2)}%, {_sgn_bp(k['w1'])} w tydzień "
            f"i {_sgn_bp(k['m1'])} w miesiąc." if key == "10Y"
            else f"Rentowność obligacji USA ({TENOR_LABELS[key]}) wynosi {B.f_num(k['last'], 2)}%.")
    sub = None
    if len(s210):
        sub = (f"Krzywa jest {shape}: różnica 10 lat − 2 lata to {_sgn_pp(float(s210.iloc[-1]))}"
               + (f", 10 lat − 3 mies. {_sgn_pp(float(s310.iloc[-1]))}." if len(s310) else "."))

    curve_series = []
    colors = {"Dziś": 0, "Tydzień temu": 2, "Miesiąc temu": "neutral"}
    for label, (dt, row) in snaps.items():
        curve_series.append({"name": f"{label} ({dt:%d.%m})", "values": [row.get(t) / 100 if math.isfinite(row.get(t, math.nan)) else None for t in tenors],
                             "color": colors[label]})
    blocks = [
        B.statement(lead, sub),
        B.chart(B.curve([t for t in tenors], curve_series, "pct", 2), "Krzywa rentowności: dziś, tydzień i miesiąc temu",
                "Rentowność obligacji skarbowych USA według zapadalności (od 1 miesiąca do 30 lat). Krzywa rosnąca to "
                "stan typowy; odwrócona (krótkie wyżej niż długie) historycznie często wyprzedzała spowolnienie gospodarki."),
    ]

    rows = [{"tenor": TENOR_LABELS[t], "last": ch[t]["last"] / 100, "d1": _bp(ch[t]["d1"]), "w1": _bp(ch[t]["w1"]),
             "m1": _bp(ch[t]["m1"]), "ytd": _bp(ch[t]["ytd"]), "y1": _bp(ch[t]["y1"])} for t in tenors]
    blocks.append(B.table(pd.DataFrame(rows), [("tenor", "Zapadalność", "text"), ("last", "Dziś", "pct", 2),
                                               ("d1", "Dzień (pb)", "num", 0, True), ("w1", "Tydzień (pb)", "num", 0, True),
                                               ("m1", "Miesiąc (pb)", "num", 0, True), ("ytd", "Od pocz. roku (pb)", "num", 0, True),
                                               ("y1", "12 mies. (pb)", "num", 0, True)],
                          "Rentowności i zmiany",
                          "pb = punkt bazowy, czyli 0,01 punktu procentowego. Wzrost rentowności oznacza spadek cen obligacji.",
                          limit=15))

    spreads = []
    for long_, short_, label, tip in (
            ("10Y", "2Y", "10 lat − 2 lata", "Najczęściej obserwowany spread krzywej. Poniżej zera = krzywa odwrócona."),
            ("10Y", "3M", "10 lat − 3 mies.", "Spread, który Fed z Nowego Jorku wykorzystuje w modelu ryzyka recesji."),
            ("30Y", "5Y", "30 lat − 5 lat", "Nachylenie długiego końca krzywej - premia za bardzo długie pożyczanie.")):
        s = M.spread(y, long_, short_)
        if len(s):
            c = M.changes(s, diff=True)
            spreads.append(B.metric(f"Spread {label}", f"{_sgn_pp(c['last'])}", "text", tip,
                                    note=f"tydzień {_sgn_bp(c['w1'])} · miesiąc {_sgn_bp(c['m1'])}"))
    if spreads:
        blocks.append(B.metrics(spreads, "Nachylenie krzywej"))

    hist = y.loc[today_date - pd.DateOffset(years=1):]
    cols = [t for t in ("3M", "2Y", "10Y", "30Y") if t in hist.columns]
    if len(hist) >= 2 and cols:
        lines = (hist[cols] / 100).rename(columns=TENOR_LABELS)
        blocks.append(B.chart(B.line(lines, "pct", 2, colors={TENOR_LABELS[t]: i for i, t in enumerate(cols)}, group="rates"),
                              "Rentowności w ostatnich 12 miesiącach", "Przesuń palcem po wykresie, żeby odczytać wartości z danego dnia."))
    sp = pd.DataFrame({"10 lat − 2 lata": s210, "10 lat − 3 mies.": s310}).loc[today_date - pd.DateOffset(years=1):].dropna(how="all")
    if len(sp) >= 2:
        blocks.append(B.chart(B.line(sp / 100, "pct", 2, zero=True, group="rates"), "Spready krzywej (w punktach procentowych)",
                              "Wartości poniżej zera oznaczają odwróconą krzywą w danym odcinku."))
    for w in d.get("warnings", []):
        if w.startswith("Rentowności"):
            blocks.append(B.note(w, "warn"))
    blocks.append(_src_note(d, "yields_source", f"Ostatnie notowanie z {today_date:%d.%m.%Y}; Departament Skarbu USA "
                                                "publikuje krzywą raz dziennie po zamknięciu rynku w Nowym Jorku."))
    return {"title": title, "blocks": blocks}


# --------------------------------------------------------------------------- #
# Notowania: indeksy, VIX, waluty, surowce
# --------------------------------------------------------------------------- #

def _quote_rows(prices: dict, group: str) -> list[dict]:
    rows = []
    for key, (name, grp, _, _, digits) in INSTRUMENTS.items():
        if grp != group or key not in prices:
            continue
        c = M.changes(prices[key])
        if not math.isfinite(c["last"]):
            continue
        rows.append({"key": key, "name": name, "last": B.f_num(c["last"], digits), "date": f"{c['date']:%d.%m}",
                     **{k: c[k] for k in M.WINDOWS}})
    return rows


def quotes(ctx, p) -> dict:
    title = "Notowania"
    d = _data(ctx)
    prices = (d or {}).get("prices") or {}
    if not prices:
        return _unavailable(title, "notowania indeksów i walut", d)
    ch = {k: M.changes(s) for k, s in prices.items()}

    def pct(k, digits=1):
        return B.f_pct(ch[k]["d1"], digits, sign=True) if k in ch else "–"
    idx_part = [f"{INSTRUMENTS[k][0]} {pct(k)}" for k in ("SP500", "NDX", "DAX", "WIG20") if k in ch]
    lead = ("Na ostatniej sesji: " + ", ".join(idx_part) + ".") if idx_part else "Notowania głównych rynków."
    sub_parts = []
    if "VIX" in ch:
        v = ch["VIX"]["last"]
        sub_parts.append(f"VIX {B.f_num(v, 1)} – {M.vix_regime(v)}")
    for k in ("EURUSD", "USDPLN"):
        if k in ch:
            sub_parts.append(f"{INSTRUMENTS[k][0]} {B.f_num(ch[k]['last'], INSTRUMENTS[k][4])} ({pct(k, 2)})")
    blocks = [B.statement(lead, "; ".join(sub_parts) + "." if sub_parts else None)]

    cols = [("name", "Instrument", "text"), ("last", "Ostatnio", "text"), ("d1", "Dzień", "pct", 2, True),
            ("w1", "Tydzień", "pct", 1, True), ("m1", "Miesiąc", "pct", 1, True), ("ytd", "Od pocz. roku", "pct", 1, True),
            ("y1", "12 mies.", "pct", 1, True), ("date", "Z dnia", "text")]
    captions = {
        "Indeksy": "Poziomy indeksów w walutach notowania (bez przeliczenia na złote).",
        "Zmienność": "VIX to oczekiwana przez rynek zmienność S&P 500 na najbliższe 30 dni (w % rocznie); VIX 3M - na 3 miesiące. "
                     "Poniżej 15 rynek jest spokojny, powyżej 30 - w silnym stresie.",
        "Waluty": "Kursy z rynku międzybankowego (nie kursy NBP ani kursy wymiany w XTB). DXY mierzy dolara wobec koszyka "
                  "sześciu walut.",
        "Surowce": "Kontrakty terminowe z najbliższą datą wygaśnięcia, w dolarach (złoto za uncję, ropa za baryłkę).",
    }
    for group in GROUPS:
        rows = _quote_rows(prices, group)
        if not rows:
            continue
        blocks.append(B.table(pd.DataFrame(rows), cols, group, captions[group], limit=12))
        if group == "Zmienność" and "VIX" in ch:
            items = [B.metric("VIX", ch["VIX"]["last"], "num", digits=1,
                              tip="Indeks strachu: oczekiwana zmienność S&P 500 wyceniona w opcjach na 30 dni.",
                              note=M.vix_regime(ch["VIX"]["last"]))]
            if "VIX3M" in ch and ch["VIX3M"]["last"]:
                ratio = ch["VIX"]["last"] / ch["VIX3M"]["last"]
                items.append(B.metric("VIX / VIX 3M", ratio, "num", digits=2,
                                      tip="Struktura terminowa zmienności. Poniżej 1 - stan typowy (dalsza przyszłość "
                                          "niepewniejsza). Powyżej 1 - rynek boi się najbliższych tygodni bardziej niż "
                                          "kolejnych miesięcy, co zdarza się w okresach paniki.",
                                      note="krótki termin spokojniejszy" if ratio < 1 else "nerwowość tu i teraz"))
            blocks.append(B.metrics(items, "Zmienność w skrócie"))
            last = prices["VIX"].index[-1]
            vix = pd.DataFrame({INSTRUMENTS[k][0]: prices[k] for k in ("VIX", "VIX3M") if k in prices}).loc[last - pd.DateOffset(years=1):]
            if len(vix) >= 2:
                blocks.append(B.chart(B.line(vix.ffill(), "num", 1), "VIX w ostatnich 12 miesiącach"))

    idx = [k for k in ("SP500", "NDX", "STOXX50", "DAX", "WIG20", "N225") if k in prices]
    if idx:
        last = max(prices[k].index[-1] for k in idx)
        df = pd.DataFrame({INSTRUMENTS[k][0]: prices[k] for k in idx}).loc[last - pd.DateOffset(years=1):].ffill().dropna(how="all")
        if len(df) >= 2:
            blocks.insert(2, B.chart(B.line(df, "num", 1, rebase=True, delta="rel", colors={"S&P 500": 3, "Nasdaq 100": 2, "WIG20": 1}),
                                     "Indeksy w ostatnich 12 miesiącach",
                                     "Wszystkie linie startują od 100 na początku widocznego okna - widać, który rynek rósł szybciej. "
                                     "W walutach notowania."))
        day = pd.Series({INSTRUMENTS[k][0]: ch[k]["d1"] for k in idx}).dropna().sort_values(ascending=False)
        if len(day):
            blocks.insert(2, B.chart(B.hbar(day, "pct", 2, signed=True), "Indeksy: zmiana na ostatniej sesji"))
    fx = [k for k in ("USDPLN", "EURPLN") if k in prices]
    if fx:
        last = max(prices[k].index[-1] for k in fx)
        df = pd.DataFrame({INSTRUMENTS[k][0]: prices[k] for k in fx}).loc[last - pd.DateOffset(years=1):].ffill().dropna(how="all")
        if len(df) >= 2:
            blocks.append(B.chart(B.line(df, "num", 4), "Złoty w ostatnich 12 miesiącach",
                                  "Wyższy kurs = słabszy złoty. Od tego zależy wartość zagranicznych aktywów liczona w złotych."))
    missing = [INSTRUMENTS[k][0] for k in INSTRUMENTS if k not in prices]
    if missing:
        blocks.append(B.note("Brak notowań: " + ", ".join(missing) + ".", "warn"))
    blocks.append(_src_note(d, "prices_source", "Notowania mogą być opóźnione o kilkanaście minut; ekran odświeża się co 10 minut. "
                                                "W trakcie sesji „Dzień” liczy się od poprzedniego zamknięcia."))
    return {"title": title, "blocks": blocks}


# --------------------------------------------------------------------------- #
# Kalendarz makro
# --------------------------------------------------------------------------- #

def calendar(ctx, p) -> dict:
    title = "Kalendarz makro"
    d = _data(ctx)
    events = (d or {}).get("calendar")
    if events is None:
        return _unavailable(title, "kalendarz publikacji makroekonomicznych", d)
    region = p.str("region", "us", [r[0] for r in REGIONS])
    imp = p.str("impact", "medium", [i[0] for i in IMPACTS])
    now = pd.Timestamp(d.get("now") or pd.Timestamp.now(tz="UTC"))
    now = now.tz_localize("UTC") if now.tzinfo is None else now
    ev = M.upcoming(events, now, REGION_SETS[region], IMPACT_SETS[imp])
    today_local = _local(now).normalize()

    def when(e):
        t = _local(e["ts"])
        days = int((t.normalize() - today_local).days)
        return t, days

    high_us = M.upcoming(events, now, {"USD"}, {"High"}, lookback_hours=0)
    if high_us:
        t, days = when(high_us[0])
        same = [e for e in high_us if e["ts"] == high_us[0]["ts"]]
        names = ", ".join(_title(e["title"]) for e in same[:3])
        lead = (f"Najbliższa ważna publikacja z USA: {names} – {WD[t.weekday()]} {t:%d.%m}, godz. {t:%H:%M} "
                f"({_in_days(days)}).")
    else:
        lead = "W kalendarzu na ten i przyszły tydzień nie ma już ważnych publikacji z USA."
    n_high = sum(1 for e in ev if e.get("impact") == "High")
    sub = (f"W wybranym zakresie: {len(ev)} {_plural(len(ev), 'publikacja', 'publikacje', 'publikacji')}, w tym {n_high} "
           f"{_plural(n_high, 'najważniejsza', 'najważniejsze', 'najważniejszych')}. Godziny czasu polskiego.") if ev else None

    blocks = [B.statement(lead, sub),
              B.controls([B.control("region", "segmented", "Kraje", region, REGIONS),
                          B.control("impact", "segmented", "Waga danych", imp, IMPACTS)])]
    if not ev:
        blocks.append(B.note("Brak publikacji dla wybranych filtrów. Kalendarz obejmuje bieżący i (jeśli dostępny) kolejny tydzień."))
    by_day: dict = {}
    for e in ev:
        t, days = when(e)
        by_day.setdefault((t.normalize(), days), []).append({
            "time": f"{t:%H:%M}" if (t.hour or t.minute) else "cały dzień", "title": _title(e["title"]),
            "country": COUNTRIES.get(e.get("country"), e.get("country") or "–"), "impact": IMPACT.get(e.get("impact"), "–"),
            "forecast": _pl_number(e.get("forecast")), "previous": _pl_number(e.get("previous")),
            "actual": _pl_number(e.get("actual"))})
    cols = [("time", "Godzina", "text"), ("title", "Dane", "text"), ("impact", "Waga", "text"),
            ("forecast", "Prognoza", "text"), ("previous", "Poprzednio", "text")]
    if any(r["actual"] != "–" for rows in by_day.values() for r in rows):
        cols.insert(3, ("actual", "Odczyt", "text"))
    if region != "us":
        cols.insert(2, ("country", "Kraj", "text"))
    for i, ((day, days), rows) in enumerate(sorted(by_day.items())):
        head = f"{WD[day.weekday()].capitalize()} {day:%d.%m} · {_in_days(days)}" if days >= 0 else f"{WD[day.weekday()].capitalize()} {day:%d.%m}"
        blocks.append(B.section(head, [B.table(pd.DataFrame(rows), cols, limit=40)], open_=days <= 7))
    blocks.append(B.bullets([
        "CPI i PCE (inflacja) oraz raport z rynku pracy (NFP, stopa bezrobocia) najmocniej wpływają na oczekiwania co do stóp Fed, "
        "a przez to na rentowności, dolara i wyceny akcji.",
        "Decyzja Fed (Federal Funds Rate) i konferencja prezesa to zwykle najbardziej zmienne momenty miesiąca.",
        "Rynek reaguje na różnicę między odczytem a prognozą (konsensusem), a nie na sam odczyt.",
    ], "Na co patrzeć"))
    for w in d.get("warnings", []):
        if w.startswith("Kalendarz"):
            blocks.append(B.note(w, "warn"))
    blocks.append(_src_note(d, "calendar_source", "Waga publikacji (●●● najważniejsze) według źródła. Prognozy to mediana oczekiwań "
                                                  "rynku; terminy potrafią się przesuwać."))
    return {"title": title, "blocks": blocks}
