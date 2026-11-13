"""Dane do zakładki „Rynki”: rentowności obligacji USA, indeksy, VIX, waluty, surowce i kalendarz makro.

Źródła (bez kluczy API):
- krzywa rentowności: US Treasury (Daily Treasury Par Yield Curve Rates, CSV) → FRED → Yahoo (^IRX, ^FVX, ^TNX, ^TYX),
- notowania: Yahoo Finance (yfinance) → Stooq,
- kalendarz makro: tygodniowy kalendarz Forex Factory (nfs.faireconomy.media, JSON).

Wszystko trafia do wspólnego cache (tabela kv w SQLite) z własnym czasem życia; gdy pobranie się nie uda,
zostaje ostatnia zapisana wersja. Dane nie zależą od portfela użytkownika i nic o portfelu nie wychodzi na zewnątrz.
"""
from __future__ import annotations

import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
                "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={ids}"
CALENDAR_URLS = ("https://nfs.faireconomy.media/ff_calendar_thisweek.json",
                 "https://nfs.faireconomy.media/ff_calendar_nextweek.json")

# zapadalności w kolejności na osi krzywej
TENORS = ["1M", "2M", "3M", "4M", "6M", "1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]
TENOR_LABELS = {"1M": "1 mies.", "2M": "2 mies.", "3M": "3 mies.", "4M": "4 mies.", "6M": "6 mies.", "1Y": "1 rok",
                "2Y": "2 lata", "3Y": "3 lata", "5Y": "5 lat", "7Y": "7 lat", "10Y": "10 lat", "20Y": "20 lat",
                "30Y": "30 lat"}
FRED_IDS = {"1M": "DGS1MO", "3M": "DGS3MO", "6M": "DGS6MO", "1Y": "DGS1", "2Y": "DGS2", "3Y": "DGS3", "5Y": "DGS5",
            "7Y": "DGS7", "10Y": "DGS10", "20Y": "DGS20", "30Y": "DGS30"}
YAHOO_YIELDS = {"3M": "^IRX", "5Y": "^FVX", "10Y": "^TNX", "30Y": "^TYX"}

# klucz -> (nazwa, grupa, kandydaci Yahoo, ticker Stooq, liczba miejsc po przecinku)
INSTRUMENTS = {
    "SP500": ("S&P 500", "Indeksy", "^GSPC", "^spx", 2),
    "NDX": ("Nasdaq 100", "Indeksy", "^NDX", "^ndx", 2),
    "DJI": ("Dow Jones", "Indeksy", "^DJI", "^dji", 2),
    "RUT": ("Russell 2000", "Indeksy", "^RUT", "", 2),
    "STOXX50": ("Euro Stoxx 50", "Indeksy", "^STOXX50E", "", 2),
    "DAX": ("DAX", "Indeksy", "^GDAXI", "^dax", 2),
    "WIG20": ("WIG20", "Indeksy", "WIG20.WA|^WIG20", "wig20", 2),
    "N225": ("Nikkei 225", "Indeksy", "^N225", "^nkx", 2),
    "VIX": ("VIX", "Zmienność", "^VIX", "", 2),
    "VIX3M": ("VIX 3M", "Zmienność", "^VIX3M", "", 2),
    "DXY": ("Indeks dolara (DXY)", "Waluty", "DX-Y.NYB", "", 2),
    "EURUSD": ("EUR/USD", "Waluty", "EURUSD=X", "eurusd", 4),
    "USDPLN": ("USD/PLN", "Waluty", "USDPLN=X|PLN=X", "usdpln", 4),
    "EURPLN": ("EUR/PLN", "Waluty", "EURPLN=X", "eurpln", 4),
    "GBPUSD": ("GBP/USD", "Waluty", "GBPUSD=X", "gbpusd", 4),
    "USDJPY": ("USD/JPY", "Waluty", "USDJPY=X|JPY=X", "usdjpy", 2),
    "CHFPLN": ("CHF/PLN", "Waluty", "CHFPLN=X", "chfpln", 4),
    "GOLD": ("Złoto", "Surowce", "GC=F", "xauusd", 1),
    "BRENT": ("Ropa Brent", "Surowce", "BZ=F", "cb.f", 2),
}
GROUPS = ["Indeksy", "Zmienność", "Waluty", "Surowce"]

TTL_HOURS = {"yields": 3.0, "quotes": 0.25, "calendar": 1.0}
HISTORY_DAYS = 400
log = logging.getLogger("xtb.markets")


# --------------------------------------------------------------------------- #
# Parsery (czyste funkcje - testowane na przykładowych odpowiedziach)
# --------------------------------------------------------------------------- #

_TENOR_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(mo|mos|month|months|yr|yrs|year|years)\s*$", re.I)


def tenor_from_header(h: str) -> str | None:
    """'1 Mo' -> '1M', '10 Yr' -> '10Y'; zapadalności spoza listy (np. '1.5 Month') -> None."""
    m = _TENOR_RE.match(str(h))
    if not m:
        return None
    n, unit = m.group(1), m.group(2).lower()
    if "." in n:
        return None
    t = f"{int(n)}{'M' if unit.startswith('mo') else 'Y'}"
    return t if t in TENORS else None


def parse_treasury_csv(text: str) -> pd.DataFrame:
    """CSV z US Treasury -> DataFrame (data × zapadalność) w procentach, rosnąco po dacie."""
    df = pd.read_csv(io.StringIO(text))
    if df.empty or "Date" not in df.columns:
        raise ValueError("Treasury: nieoczekiwany format pliku")
    cols = {c: tenor_from_header(c) for c in df.columns if c != "Date"}
    keep = {c: t for c, t in cols.items() if t}
    if not keep:
        raise ValueError("Treasury: brak kolumn z zapadalnościami")
    out = df[list(keep)].rename(columns=keep).apply(pd.to_numeric, errors="coerce")
    out.index = pd.to_datetime(df["Date"], format="%m/%d/%Y", errors="coerce")
    out = out[out.index.notna()].sort_index()
    out = out[~out.index.duplicated(keep="last")]
    return out[[t for t in TENORS if t in out.columns]].dropna(how="all")


def parse_fred_csv(text: str) -> pd.DataFrame:
    """CSV z FRED (kolumna daty 'observation_date' albo 'DATE', braki jako '.') -> DataFrame w procentach."""
    df = pd.read_csv(io.StringIO(text), na_values=["."])
    date_col = next((c for c in df.columns if c.lower() in ("observation_date", "date")), None)
    if date_col is None:
        raise ValueError("FRED: brak kolumny z datą")
    back = {v: k for k, v in FRED_IDS.items()}
    keep = {c: back[c] for c in df.columns if c in back}
    out = df[list(keep)].rename(columns=keep).apply(pd.to_numeric, errors="coerce")
    out.index = pd.to_datetime(df[date_col], errors="coerce")
    out = out[out.index.notna()].sort_index().dropna(how="all")
    return out[[t for t in TENORS if t in out.columns]]


def parse_calendar_json(payload) -> list[dict]:
    """Kalendarz Forex Factory -> lista wydarzeń z czasem w UTC (ISO)."""
    out = []
    for it in payload or []:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        try:
            ts = pd.Timestamp(it.get("date"))
        except (TypeError, ValueError):
            continue
        if not title or pd.isna(ts):
            continue
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        out.append({"time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "title": title[:120],
                    "country": str(it.get("country") or "").upper()[:3],
                    "impact": str(it.get("impact") or "").capitalize()[:10],
                    "forecast": str(it.get("forecast") or "").strip()[:20],
                    "previous": str(it.get("previous") or "").strip()[:20],
                    "actual": str(it.get("actual") or "").strip()[:20]})
    out.sort(key=lambda e: (e["time"], e["country"], e["title"]))
    return out


def _df_to_json(df: pd.DataFrame) -> str:
    return json.dumps({"dates": [d.strftime("%Y-%m-%d") for d in df.index], "columns": list(df.columns),
                       "values": [[None if v is None or not np.isfinite(v) else float(v) for v in row]
                                  for row in df.to_numpy(dtype=float)]})


def _df_from_json(raw: str) -> pd.DataFrame:
    d = json.loads(raw)
    return pd.DataFrame(d["values"], index=pd.to_datetime(d["dates"]), columns=d["columns"], dtype=float)


def _series_to_json(s: pd.Series) -> str:
    return _df_to_json(s.to_frame("close"))


def _series_from_json(raw: str) -> pd.Series:
    return _df_from_json(raw)["close"]


# --------------------------------------------------------------------------- #
# Pobieranie na żywo
# --------------------------------------------------------------------------- #

def _get(url: str, timeout: float = 20.0):
    from data.providers import DataUnavailable, _http_get
    host = url.split("/")[2]
    try:
        r = _http_get(url, timeout=timeout)
    except Exception as exc:
        raise DataUnavailable(f"{host}: {exc}") from exc
    if r.status_code != 200:
        raise DataUnavailable(f"{host}: HTTP {r.status_code}")
    return r


def fetch_treasury(today: pd.Timestamp) -> pd.DataFrame:
    """Bieżący rok i (dla historii z ostatnich 12 miesięcy) poprzedni."""
    parts = []
    for year in (today.year - 1, today.year):
        try:
            parts.append(parse_treasury_csv(_get(TREASURY_URL.format(year=year), timeout=30).text))
        except Exception:
            if year == today.year:
                raise
    out = pd.concat(parts).sort_index()
    return out[~out.index.duplicated(keep="last")]


def fetch_fred(today: pd.Timestamp) -> pd.DataFrame:
    df = parse_fred_csv(_get(FRED_URL.format(ids=",".join(FRED_IDS.values())), timeout=30).text)
    return df.loc[today - pd.Timedelta(days=HISTORY_DAYS):]


def fetch_yahoo_yields(today: pd.Timestamp) -> pd.DataFrame:
    from data.providers import fetch_yfinance
    cols = {}
    for tenor, ticker in YAHOO_YIELDS.items():
        try:
            cols[tenor] = fetch_yfinance(ticker, today - pd.Timedelta(days=HISTORY_DAYS), today)["close"]
        except Exception:
            continue
    if not cols:
        from data.providers import DataUnavailable
        raise DataUnavailable("Yahoo: brak rentowności")
    return pd.DataFrame(cols)[[t for t in TENORS if t in cols]].dropna(how="all")


def fetch_instrument(key: str, today: pd.Timestamp) -> tuple[pd.Series, str]:
    from data.providers import DataUnavailable, fetch_stooq, fetch_yfinance
    _, _, yf_c, stq, _ = INSTRUMENTS[key]
    start, errors = today - pd.Timedelta(days=HISTORY_DAYS), []
    for t in [x for x in yf_c.split("|") if x]:
        try:
            return fetch_yfinance(t, start, today)["close"], "Yahoo Finance"
        except DataUnavailable as exc:
            errors.append(str(exc))
    if stq:
        try:
            return fetch_stooq(stq, start, today)["close"], "Stooq"
        except DataUnavailable as exc:
            errors.append(str(exc))
    raise DataUnavailable("; ".join(errors))


def fetch_calendar() -> list[dict]:
    events, ok, errors = [], False, []
    for url in CALENDAR_URLS:
        try:
            r = _get(url, timeout=20)
            try:
                payload = r.json()
            except ValueError as exc:
                raise ValueError(f"odpowiedź nie jest JSON ({r.headers.get('content-type', '?')}): "
                                 f"{r.text[:80]!r}") from exc
            events += parse_calendar_json(payload)
            ok = True
        except Exception as exc:
            errors.append(f"{url.rsplit('/', 1)[-1]}: {exc}")
    for e in errors:
        log.warning("kalendarz makro: %s", e)
    if not ok:
        from data.providers import DataUnavailable
        raise DataUnavailable("; ".join(errors) or "brak odpowiedzi")
    seen, out = set(), []
    for e in sorted(events, key=lambda e: (e["time"], e["country"], e["title"])):
        k = (e["time"], e["country"], e["title"])
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def live_markets(cache, today=None, workers: int = 4) -> dict:
    """Zbiera wszystko dla zakładki „Rynki”, każdą część z cache albo z sieci (z cofnięciem do starej kopii)."""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    warnings: list[str] = []

    def cached(key, ttl, fetch, dump, load, label):
        hit = cache.get_kv(key, max_age_hours=ttl)
        if hit:
            return load(hit)
        try:
            val = fetch()
            cache.set_kv(key, dump(val))
            return val
        except Exception as exc:
            old = cache.get_kv(key)
            if old:
                warnings.append(f"{label}: nie udało się odświeżyć ({exc}); pokazuję ostatnią zapisaną wersję.")
                return load(old)
            warnings.append(f"{label}: brak danych ({exc}).")
            return None

    # krzywa: Treasury -> FRED -> Yahoo
    def yields_fetch():
        errors = []
        for name, fn in (("US Treasury", fetch_treasury), ("FRED", fetch_fred), ("Yahoo Finance", fetch_yahoo_yields)):
            try:
                df = fn(today)
                if len(df):
                    return {"df": df, "source": name}
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        raise RuntimeError("; ".join(errors))

    y = cached("mk:yields", TTL_HOURS["yields"], yields_fetch,
               lambda v: json.dumps({"df": _df_to_json(v["df"]), "source": v["source"]}),
               lambda raw: (lambda d: {"df": _df_from_json(d["df"]), "source": d["source"]})(json.loads(raw)),
               "Rentowności")

    def one(key):
        name = INSTRUMENTS[key][0]
        v = cached(f"mk:px:{key}", TTL_HOURS["quotes"], lambda: fetch_instrument(key, today),
                   lambda v: json.dumps({"s": _series_to_json(v[0]), "src": v[1]}),
                   lambda raw: (lambda d: (_series_from_json(d["s"]), d["src"]))(json.loads(raw)), name)
        return key, v

    keys = list(INSTRUMENTS)
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            res = list(pool.map(one, keys))
    else:
        res = [one(k) for k in keys]
    prices = {k: v[0] for k, v in res if v is not None and len(v[0].dropna())}
    sources = sorted({v[1] for _, v in res if v is not None})

    cal = cached("mk:calendar", TTL_HOURS["calendar"], fetch_calendar, json.dumps, json.loads, "Kalendarz makro")
    for w in warnings:
        log.warning(w)
    return {"yields": y["df"] if y else None, "yields_source": y["source"] if y else None,
            "prices": prices, "prices_source": ", ".join(sources) or None,
            "calendar": cal, "calendar_source": "Forex Factory" if cal is not None else None,
            "now": pd.Timestamp.now(tz="UTC"), "warnings": warnings, "is_synthetic": False}


# --------------------------------------------------------------------------- #
# Tryb demo
# --------------------------------------------------------------------------- #

DEMO_LEVELS = {"SP500": 6650.0, "NDX": 24400.0, "DJI": 46900.0, "RUT": 2450.0, "STOXX50": 5600.0, "DAX": 24100.0,
               "WIG20": 2900.0, "N225": 49000.0, "VIX": 17.5, "VIX3M": 19.6, "DXY": 99.4, "EURUSD": 1.158,
               "USDPLN": 3.67, "EURPLN": 4.25, "GBPUSD": 1.31, "USDJPY": 152.0, "CHFPLN": 4.58, "GOLD": 4000.0,
               "BRENT": 64.0}
DEMO_CURVE = {"1M": 4.02, "2M": 3.99, "3M": 3.95, "4M": 3.91, "6M": 3.83, "1Y": 3.66, "2Y": 3.55, "3Y": 3.56,
              "5Y": 3.68, "7Y": 3.88, "10Y": 4.10, "20Y": 4.66, "30Y": 4.70}
DEMO_EVENTS = [  # (dni robocze od dziś, godzina UTC, kraj, tytuł, ważność, prognoza, poprzednio)
    (0, "13:30", "USD", "Unemployment Claims", "Medium", "225K", "219K"),
    (1, "13:30", "USD", "Core CPI m/m", "High", "0.3%", "0.3%"),
    (1, "13:30", "USD", "CPI y/y", "High", "2.9%", "3.0%"),
    (2, "13:30", "USD", "PPI m/m", "Medium", "0.2%", "0.1%"),
    (3, "13:30", "USD", "Retail Sales m/m", "High", "0.4%", "0.6%"),
    (3, "10:00", "EUR", "German ZEW Economic Sentiment", "Medium", "40.2", "39.3"),
    (4, "15:00", "USD", "Prelim UoM Consumer Sentiment", "Medium", "54.0", "53.6"),
    (6, "19:00", "USD", "FOMC Meeting Minutes", "High", "", ""),
    (7, "13:30", "USD", "Building Permits", "Low", "1.33M", "1.31M"),
    (8, "13:30", "USD", "Core PCE Price Index m/m", "High", "0.2%", "0.2%"),
    (8, "14:45", "USD", "Flash Manufacturing PMI", "High", "52.1", "52.5"),
    (9, "10:00", "EUR", "CPI Flash Estimate y/y", "High", "2.1%", "2.1%"),
]


def demo_markets(end) -> dict:
    """Zmyślone, ale spójne dane rynkowe (deterministyczne) - żeby zakładka działała bez sieci."""
    end = pd.Timestamp(end).normalize()
    dates = pd.bdate_range(end - pd.Timedelta(days=HISTORY_DAYS), end)
    rng = np.random.default_rng(2026)
    n = len(dates)
    level = rng.normal(0, 0.035, n).cumsum()            # wspólny poziom stóp
    slope = rng.normal(0, 0.02, n).cumsum()
    level -= level[-1]
    slope -= slope[-1]
    curve = pd.DataFrame({t: DEMO_CURVE[t] + level + slope * (i - 6) / 6 for i, t in enumerate(TENORS)}, index=dates)
    prices = {}
    for i, (key, base) in enumerate(DEMO_LEVELS.items()):
        vol = {"VIX": 0.06, "VIX3M": 0.04}.get(key, 0.004 if INSTRUMENTS[key][1] == "Waluty" else 0.011)
        r = np.random.default_rng(100 + i).normal(0.0002 if key not in ("VIX", "VIX3M") else 0.0, vol, n)
        path = np.exp(r.cumsum())
        if key in ("VIX", "VIX3M"):                       # zmienność wraca do średniej (proces AR(1) na logarytmie)
            shocks = np.random.default_rng(7).normal(0, 0.07 if key == "VIX" else 0.045, n)
            lv = np.zeros(n)
            for j in range(1, n):
                lv[j] = 0.94 * lv[j - 1] + shocks[j]
            path = np.exp(lv)
        prices[key] = pd.Series(base * path / path[-1], index=dates)
    events = []
    for days, hm, cty, title, imp, fc, prev in DEMO_EVENTS:
        d = end + pd.offsets.BDay(days) if days else end
        events.append({"time": f"{d:%Y-%m-%d}T{hm}:00Z", "title": title, "country": cty, "impact": imp,
                       "forecast": fc, "previous": prev, "actual": ""})
    events.sort(key=lambda e: e["time"])
    return {"yields": curve, "yields_source": "dane przykładowe (zmyślone)", "prices": prices,
            "prices_source": "dane przykładowe (zmyślone)", "calendar": events,
            "calendar_source": "dane przykładowe (zmyślone)", "now": end.tz_localize("UTC") + pd.Timedelta(hours=8),
            "warnings": [], "is_synthetic": True}
