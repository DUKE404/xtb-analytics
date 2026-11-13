"""Zakładka „Rynki”: parsery źródeł, obliczenia zmian i krzywej, cache z cofnięciem do starej kopii, ekrany."""
import math

import pandas as pd
import pytest

from core import markets as M
from data import markets as D

TREASURY_CSV = """Date,"1 Mo","1.5 Month","2 Mo","3 Mo","4 Mo","6 Mo","1 Yr","2 Yr","3 Yr","5 Yr","7 Yr","10 Yr","20 Yr","30 Yr"
11/12/2026,4.05,4.04,4.02,3.98,3.94,3.85,3.68,3.57,3.58,3.70,3.90,4.12,4.68,4.71
11/11/2026,4.04,4.03,4.01,3.97,3.93,3.84,3.66,3.55,3.56,3.68,3.88,4.10,4.66,4.70
11/05/2026,4.03,4.02,4.00,3.96,3.92,3.82,3.62,3.50,3.51,3.62,3.82,4.02,4.60,4.64
"""

FRED_CSV = """observation_date,DGS1MO,DGS3MO,DGS2,DGS10,DGS30
2026-11-10,4.03,3.97,3.55,4.10,4.70
2026-11-11,.,.,.,.,.
2026-11-12,4.05,3.98,3.57,4.12,4.71
"""

CALENDAR_JSON = [
    {"title": "CPI y/y", "country": "USD", "date": "2026-11-13T08:30:00-05:00", "impact": "High",
     "forecast": "2.9%", "previous": "3.0%"},
    {"title": "German ZEW Economic Sentiment", "country": "EUR", "date": "2026-11-12T05:00:00-05:00",
     "impact": "Medium", "forecast": "40.2", "previous": "39.3"},
    {"title": "", "country": "USD", "date": "2026-11-13T08:30:00-05:00", "impact": "High"},
    {"title": "Bad date", "country": "USD", "date": "jutro", "impact": "Low"},
]


def test_parse_treasury_csv_maps_tenors_and_sorts():
    df = D.parse_treasury_csv(TREASURY_CSV)
    assert list(df.columns) == D.TENORS                     # "1.5 Month" pominięte, kolejność jak na osi
    assert df.index.is_monotonic_increasing and df.index[-1] == pd.Timestamp("2026-11-12")
    assert df.loc["2026-11-12", "10Y"] == pytest.approx(4.12)


def test_tenor_from_header():
    assert D.tenor_from_header("1 Mo") == "1M" and D.tenor_from_header("30 Yr") == "30Y"
    assert D.tenor_from_header("1.5 Month") is None and D.tenor_from_header("Date") is None


def test_parse_fred_csv_handles_missing_dots():
    df = D.parse_fred_csv(FRED_CSV)
    assert list(df.columns) == ["1M", "3M", "2Y", "10Y", "30Y"]
    assert len(df) == 2 and df["10Y"].iloc[-1] == pytest.approx(4.12)


def test_parse_calendar_json_converts_to_utc_and_skips_bad_rows():
    ev = D.parse_calendar_json(CALENDAR_JSON)
    assert [e["title"] for e in ev] == ["German ZEW Economic Sentiment", "CPI y/y"]
    assert ev[1]["time"] == "2026-11-13T13:30:00Z" and ev[1]["impact"] == "High" and ev[1]["country"] == "USD"


def test_changes_windows():
    idx = pd.bdate_range("2025-12-01", "2026-02-27")
    s = pd.Series(range(len(idx)), index=idx, dtype=float) + 100
    c = M.changes(s)
    assert c["last"] == s.iloc[-1]
    assert c["d1"] == pytest.approx(s.iloc[-1] / s.iloc[-2] - 1)
    assert c["w1"] == pytest.approx(s.iloc[-1] / s.loc["2026-02-20"] - 1)
    assert c["ytd"] == pytest.approx(s.iloc[-1] / s.loc["2025-12-31"] - 1)
    assert math.isnan(c["y1"])                               # za krótka historia - brak, a nie zmyślona wartość
    d = M.changes(s, diff=True)
    assert d["m1"] == pytest.approx(s.iloc[-1] - s.loc["2026-01-27"])


def test_changes_uses_last_session_before_weekend():
    idx = pd.to_datetime(["2026-11-06", "2026-11-09", "2026-11-13"])   # pt., pn., pt.
    s = pd.Series([100.0, 101.0, 110.0], index=idx)
    assert M.changes(s)["w1"] == pytest.approx(0.10)         # 13.11 − 7 dni = 06.11


def test_curve_snapshots_and_shape():
    y = D.parse_treasury_csv(TREASURY_CSV)
    snaps = M.curve_snapshots(y)
    assert snaps["Dziś"][0] == pd.Timestamp("2026-11-12")
    assert snaps["Tydzień temu"][0] == pd.Timestamp("2026-11-05")
    assert "Miesiąc temu" not in snaps                       # brak danych sprzed miesiąca
    assert M.curve_shape(pd.Series({"3M": 5.0, "2Y": 4.8, "10Y": 4.2})) == "odwrócona"
    assert M.curve_shape(pd.Series({"3M": 3.0, "2Y": 3.2, "10Y": 4.2})) == "rosnąca (normalna)"
    assert M.curve_shape(pd.Series({"3M": 4.1, "2Y": 3.6, "10Y": 4.2})) == "lekko rosnąca"


def test_upcoming_filters_country_impact_and_time():
    ev = D.parse_calendar_json(CALENDAR_JSON)
    now = pd.Timestamp("2026-11-12T12:30:00Z")
    assert [e["title"] for e in M.upcoming(ev, now, {"USD"}, {"High"})] == ["CPI y/y"]
    assert len(M.upcoming(ev, now, None, {"High", "Medium"})) == 1      # ZEW było ponad 2 godziny temu
    assert len(M.upcoming(ev, pd.Timestamp("2026-11-12T10:30:00Z"), None, {"High", "Medium"})) == 2


class FakeCache:
    def __init__(self):
        self.kv = {}

    def get_kv(self, key, max_age_hours=None):
        v = self.kv.get(key)
        if v is None:
            return None
        value, fresh = v
        return value if (fresh or max_age_hours is None) else None

    def set_kv(self, key, value):
        self.kv[key] = (value, True)

    def expire(self):
        self.kv = {k: (v, False) for k, (v, _) in self.kv.items()}


def _fake_series(today):
    idx = pd.bdate_range(today - pd.Timedelta(days=60), today)
    return pd.Series(range(len(idx)), index=idx, dtype=float) + 50


def test_live_markets_fallbacks_and_stale_cache(monkeypatch):
    today = pd.Timestamp("2026-11-12")
    calls = {"treasury": 0}

    def treasury(t):
        calls["treasury"] += 1
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(D, "fetch_treasury", treasury)
    monkeypatch.setattr(D, "fetch_fred", lambda t: D.parse_fred_csv(FRED_CSV))
    monkeypatch.setattr(D, "fetch_instrument", lambda k, t: (_fake_series(t), "Yahoo Finance"))
    monkeypatch.setattr(D, "fetch_calendar_ff", lambda: D.parse_calendar_json(CALENDAR_JSON))
    monkeypatch.setattr(D, "_shared", None)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    cache = FakeCache()
    m = D.live_markets(cache, today, workers=1)
    assert m["yields_source"] == "FRED" and m["yields"]["10Y"].iloc[-1] == pytest.approx(4.12)
    assert set(m["prices"]) == set(D.INSTRUMENTS) and len(m["calendar"]) == 2 and not m["warnings"]

    # świeży cache: bez sieci
    monkeypatch.setattr(D, "fetch_fred", lambda t: (_ for _ in ()).throw(RuntimeError("nie powinno być wołane")))
    D.live_markets(cache, today, workers=1)
    assert calls["treasury"] == 1

    # cache przeterminowany, sieć padła: zostaje stara kopia + ostrzeżenie
    cache.expire()
    boom = lambda *a: (_ for _ in ()).throw(RuntimeError("brak sieci"))   # noqa: E731
    for name in ("fetch_treasury", "fetch_fred", "fetch_yahoo_yields", "fetch_instrument", "fetch_calendar_ff"):
        monkeypatch.setattr(D, name, boom)
    m2 = D.live_markets(cache, today, workers=1)
    assert m2["yields"]["10Y"].iloc[-1] == pytest.approx(4.12) and len(m2["prices"]) == len(D.INSTRUMENTS)
    assert any("ostatnią zapisaną wersję" in w for w in m2["warnings"])

    # pusty cache i brak sieci: ekran dostaje None zamiast wyjątku
    m3 = D.live_markets(FakeCache(), today, workers=1)
    assert m3["yields"] is None and m3["prices"] == {} and m3["calendar"] is None


class _Provider:
    def __init__(self, data):
        self.data = data

    def markets(self, today=None):
        return self.data


class _Ctx:
    def __init__(self, data):
        self.provider = _Provider(data)
        self._c = {}

    def memo(self, key, fn):
        if key not in self._c:
            self._c[key] = fn()
        return self._c[key]


def test_screens_without_data_show_message():
    from server.screens import Params
    from server.screens import markets as S
    empty = {"yields": None, "prices": {}, "calendar": None, "warnings": ["Rentowności: brak danych (HTTP 503)."]}
    for fn in (S.rates, S.quotes, S.calendar):
        out = fn(_Ctx(empty), Params())
        assert out["blocks"][0]["kind"] == "statement" and "Nie udało się" in out["blocks"][0]["text"]


def test_rates_screen_with_partial_curve_and_calendar_filters():
    from server.screens import Params
    from server.screens import markets as S
    data = {"yields": D.parse_fred_csv(FRED_CSV), "yields_source": "FRED", "prices": {}, "warnings": [],
            "calendar": D.parse_calendar_json(CALENDAR_JSON), "calendar_source": "Forex Factory",
            "now": pd.Timestamp("2026-11-12T08:00:00Z")}
    out = S.rates(_Ctx(data), Params())
    curve = next(b["chart"] for b in out["blocks"] if b["kind"] == "chart" and b["chart"]["type"] == "curve")
    assert curve["labels"] == ["1M", "3M", "2Y", "10Y", "30Y"] and curve["series"][0]["values"][3] == pytest.approx(0.0412)
    assert "4,12%" in out["blocks"][0]["text"]

    cal = S.calendar(_Ctx(data), Params({"region": "us", "impact": "high"}))
    assert "Inflacja CPI r/r" in cal["blocks"][0]["text"] and "14:30" in cal["blocks"][0]["text"]   # 13:30 UTC = 14:30 w Polsce
    rows = [r for b in cal["blocks"] if b["kind"] == "section" for r in b["blocks"][0]["rows"]]
    assert [r["title"] for r in rows] == ["Inflacja CPI r/r"] and rows[0]["forecast"] == "2,9%"
    allr = S.calendar(_Ctx(data), Params({"region": "all", "impact": "all"}))
    rows = [r for b in allr["blocks"] if b["kind"] == "section" for r in b["blocks"][0]["rows"]]
    assert {r["country"] for r in rows} == {"USA", "Strefa euro"}


FRED_RELEASES_JSON = {"release_dates": [
    {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-11-18"},
    {"release_id": 50, "release_name": "Employment Situation", "date": "2026-12-04"},
    {"release_id": 101, "release_name": "FOMC Press Release", "date": "2026-12-09"},
    {"release_id": 21, "release_name": "H.6 Money Stock Measures", "date": "2026-11-24"},
    {"release_id": 10, "release_name": "Consumer Price Index", "date": "2026-11-18"},
    {"release_id": 1, "release_name": "Bad", "date": "jutro"},
]}


def test_parse_fred_release_dates_keeps_known_releases_in_utc():
    ev = D.parse_fred_release_dates(FRED_RELEASES_JSON)
    assert [e["title"] for e in ev] == ["Inflacja CPI", "Raport z rynku pracy (NFP, stopa bezrobocia)", "Decyzja Fed w sprawie stóp"]
    assert ev[0]["time"] == "2026-11-18T13:30:00Z"          # 8:30 w Nowym Jorku (czas zimowy) = 13:30 UTC
    assert ev[2]["time"] == "2026-12-09T19:00:00Z" and all(e["country"] == "USD" for e in ev)
    assert ev[0]["impact"] == "High" and ev[0]["forecast"] == ""


def test_merge_calendars_adds_fred_after_forex_factory_week():
    ff = D.parse_calendar_json(CALENDAR_JSON)                 # tydzień 08-14.11.2026
    fred = D.parse_fred_release_dates(FRED_RELEASES_JSON) + D.parse_fred_release_dates(
        {"release_dates": [{"release_name": "Consumer Price Index", "date": "2026-11-13"}]})
    merged = D.merge_calendars(ff, fred)
    titles = [e["title"] for e in merged]
    assert titles.count("Inflacja CPI") == 1                 # 13.11 pokrywa Forex Factory; zostaje 18.11 z FRED
    assert "CPI y/y" in titles and "Decyzja Fed w sprawie stóp" in titles
    assert D.merge_calendars(None, fred) == fred


class _Shared:
    def __init__(self):
        self.kv = {}

    def get(self, key):
        return self.kv.get(key)

    def set(self, key, value):
        import time as _t
        self.kv[key] = (value, _t.time())


def test_calendar_rate_limit_pauses_and_shared_copy_survives_restart(monkeypatch):
    today = pd.Timestamp("2026-11-12")
    calls = {"ff": 0}

    def limited():
        calls["ff"] += 1
        raise D.RateLimited("nfs.faireconomy.media: HTTP 429 (limit zapytań)", 120)

    shared = _Shared()
    monkeypatch.setattr(D, "_shared", shared)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(D, "fetch_calendar_ff", limited)
    warnings = []
    out = D.live_calendar(FakeCache(), today, warnings)
    assert out["events"] is None and out["pause_until"] and out["fred_enabled"] is False
    assert out["pause_until"] - __import__("time").time() >= D.FF_BACKOFF_MIN - 5   # co najmniej 30 minut przerwy
    # druga próba (nawet z nowym, pustym cache lokalnym - restart serwera) nie pyta źródła w trakcie przerwy
    D.live_calendar(FakeCache(), today, [])
    assert calls["ff"] == 1

    # udany odczyt trafia do wspólnego magazynu i jest widoczny po restarcie (pusty cache lokalny)
    shared.kv.pop("mk:ff:pause")
    monkeypatch.setattr(D, "fetch_calendar_ff", lambda: D.parse_calendar_json(CALENDAR_JSON))
    ok = D.live_calendar(FakeCache(), today, [])
    assert ok["source"] == "Forex Factory" and len(ok["events"]) == 2
    monkeypatch.setattr(D, "fetch_calendar_ff", limited)
    again = D.live_calendar(FakeCache(), today, [])
    assert again["events"] == ok["events"] and calls["ff"] == 1


def test_calendar_uses_fred_when_forex_factory_refuses_and_hides_key(monkeypatch):
    monkeypatch.setattr(D, "_shared", None)
    monkeypatch.setenv("FRED_API_KEY", "tajny-klucz-123")
    monkeypatch.setattr(D, "fetch_calendar_ff", lambda: (_ for _ in ()).throw(D.RateLimited("HTTP 429", 0)))
    monkeypatch.setattr(D, "fetch_calendar_fred", lambda t, k: D.parse_fred_release_dates(FRED_RELEASES_JSON))
    out = D.live_calendar(FakeCache(), pd.Timestamp("2026-11-12"), [])
    assert out["source"] == "FRED" and out["forecasts"] is False and len(out["events"]) == 3

    def boom(t, k):
        raise RuntimeError(f"connection to https://api.stlouisfed.org/...api_key={k} failed")
    monkeypatch.setattr(D, "fetch_calendar_fred", boom)
    warnings = []
    D.live_calendar(FakeCache(), pd.Timestamp("2026-11-12"), warnings)
    assert warnings and "tajny-klucz-123" not in " ".join(warnings) and "***" in " ".join(warnings)


def test_get_reports_rate_limit_and_strips_secret(monkeypatch):
    import data.providers as P

    class R:
        status_code, headers = 429, {"Retry-After": "600"}
    monkeypatch.setattr(P, "_http_get", lambda url, timeout=20: R())
    with pytest.raises(D.RateLimited) as e:
        D._get("https://nfs.faireconomy.media/x.json")
    assert e.value.retry_after == 600

    def fail(url, timeout=20):
        raise ConnectionError(f"Max retries exceeded with url: {url}")
    monkeypatch.setattr(P, "_http_get", fail)
    with pytest.raises(P.DataUnavailable) as e:
        D._get("https://api.stlouisfed.org/fred/x?api_key=SEKRET", secret="SEKRET")
    assert "SEKRET" not in str(e.value)


def test_calendar_screen_explains_pause_and_fred_only_hides_forecasts():
    from server.screens import Params
    from server.screens import markets as S
    pause = __import__("time").time() + 1800
    out = S.calendar(_Ctx({"calendar": None, "warnings": [], "calendar_info": {"pause_until": pause, "fred_enabled": False}}),
                     Params())
    text = " ".join(b.get("text", "") + " " + (b.get("sub") or "") + " ".join(b.get("items", [])) for b in out["blocks"])
    assert "nie przyjmuje zapytań" in text and "FRED_API_KEY" in text
    data = {"calendar": D.parse_fred_release_dates(FRED_RELEASES_JSON), "calendar_source": "FRED", "warnings": [],
            "calendar_info": {"forecasts": False, "fetched": pause}, "now": pd.Timestamp("2026-11-12T08:00:00Z")}
    cal = S.calendar(_Ctx(data), Params({"impact": "all"}))
    table = next(b["blocks"][0] for b in cal["blocks"] if b["kind"] == "section")
    assert [c["key"] for c in table["columns"]] == ["time", "title", "impact"]     # bez pustych kolumn prognoz
    assert "Inflacja CPI" in cal["blocks"][0]["text"]
