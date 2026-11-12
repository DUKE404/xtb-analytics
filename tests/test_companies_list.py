"""Spółki / Lista: notowania, zmiana dzienna, terminy raportów, wiadomości."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from core import companies as C
from data.providers import parse_events, parse_news, parse_quote
from data.synthetic import SyntheticMarketData
from server import blocks as B
from server.app import create_app
from server.sessions import SessionStore
from tests.test_server import check_blocks, strict_json, synthetic_provider


# --------------------------------------------------------------------------- arytmetyka

def test_day_change_and_history_fallback():
    assert C.day_change(103.0, 100.0) == pytest.approx(0.03)
    assert np.isnan(C.day_change(None, 100.0)) and np.isnan(C.day_change(10.0, 0.0))
    s = pd.Series([10.0, np.nan, 11.0, 12.1], index=pd.bdate_range("2026-11-03", periods=4))
    q = C.quote_from_history(s)
    assert (q["price"], q["prev_close"], q["time"]) == (12.1, 11.0, "2026-11-06")
    assert C.quote_from_history(s.iloc[:1]) is None


def test_avg_open_price_is_volume_weighted():
    o = pd.DataFrame({"symbol": ["A", "A", "B"], "type": ["BUY", "BUY", "SELL"], "volume": [25.0, 10.0, 2.0],
                      "open_price": [74.33, 53.75, 100.0]})
    avg = C.avg_open_price(o)
    assert avg["A"] == pytest.approx((25 * 74.33 + 10 * 53.75) / 35) and avg["B"] == pytest.approx(100.0)
    assert C.since_purchase(62.71, avg["A"]) == pytest.approx(62.71 / avg["A"] - 1)
    assert C.since_purchase(90.0, 100.0, side=-1.0) == pytest.approx(0.10)        # krótka pozycja zarabia na spadku
    assert np.isnan(C.since_purchase(90.0, None))


def test_off_high_and_breadth():
    s = pd.Series([100.0, 120.0, 90.0], index=pd.bdate_range("2026-11-03", periods=3))
    assert C.off_high(s) == pytest.approx(-0.25) and C.off_high(s, 130.0) == 0.0 and C.off_high(s, 108.0) == pytest.approx(-0.10)
    b = C.breadth(pd.Series({"A": 0.02, "B": -0.01, "C": np.nan}), pd.Series({"A": 0.5, "B": 0.2}))
    assert (b["up"], b["down"], b["n"]) == (1, 1, 2) and b["contribution"] == pytest.approx(0.008)
    assert (b["best"], b["worst"]) == ("A", "B")


@pytest.mark.parametrize("date,fy,label", [
    ("2026-10-28", 12, "Q3 2026"),
    ("2027-02-05", 12, "roczny 2026 (Q4)"),
    ("2026-12-10", 5, "Q2 2027 r. obr."),                 # Oracle: rok obrotowy do maja
    ("2027-01-29", 9, "Q1 2027 r. obr."),                 # Apple: do września
    ("2026-11-20", 1, "Q3 2027 r. obr."),                 # Nvidia: do stycznia
    ("2026-07-25", 6, "roczny 2026 (Q4) r. obr."),        # Microsoft: do czerwca
    ("2026-01-05", None, "Q3 2025"),                      # 5 dni po końcu kwartału - to jeszcze poprzedni okres
])
def test_fiscal_period(date, fy, label):
    assert C.fiscal_period(date, fy)["label"] == label


# --------------------------------------------------------------------------- kształty danych z yfinance

def test_parse_quote():
    q = parse_quote({"currentPrice": 62.71, "regularMarketPreviousClose": 60.0, "previousClose": 59.0,
                     "regularMarketTime": 1794430800, "currency": "USD", "marketState": "CLOSED"})
    assert (q["price"], q["prev_close"], q["currency"], q["state"]) == (62.71, 60.0, "USD", "CLOSED")
    assert q["time"] == pd.Timestamp(1794430800, unit="s").strftime("%Y-%m-%dT%H:%M:%SZ")
    assert parse_quote({"regularMarketPrice": 10.0, "previousClose": 8.0})["price"] == 10.0
    assert parse_quote({"currentPrice": 10.0}) is None and parse_quote(None) is None


def test_parse_news_new_and_old_shape():
    items = [
        {"id": "1", "content": {"title": "Nowszy", "pubDate": "2026-11-11T21:12:30Z", "contentType": "STORY",
                                "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "https://example.com/a"}}},
        {"title": "Starszy kształt", "link": "https://example.com/b", "publisher": "Barron's", "providerPublishTime": 1794300000},
        {"content": {"title": "Zły odnośnik", "pubDate": "2026-11-10T10:00:00Z", "canonicalUrl": {"url": "javascript:alert(1)"}}},
        {"content": {"title": "", "pubDate": "2026-11-12T10:00:00Z"}}, "śmieć", None,
    ]
    out = parse_news(items)
    assert [n["title"] for n in out] == ["Nowszy", "Zły odnośnik", "Starszy kształt"]          # od najnowszych, bez pustych
    assert out[0] == {"title": "Nowszy", "source": "Reuters", "time": "2026-11-11T21:12:30Z", "url": "https://example.com/a"}
    assert out[1]["url"] == "" and out[2]["source"] == "Barron's"                             # tylko http(s)
    assert len(parse_news(items, limit=1)) == 1 and parse_news(None) == []


def test_parse_events():
    cal = {"Dividend Date": dt.date(2026, 12, 11), "Ex-Dividend Date": dt.date(2026, 11, 20),
           "Earnings Date": [dt.date(2027, 1, 28), dt.date(2027, 2, 2)], "Earnings Average": 2.31, "Revenue Average": 1.25e11}
    ev = parse_events("AAPL", cal, {"currency": "USD", "nextFiscalYearEnd": 1790726400, "dividendRate": 1.04})
    assert ev["earnings"] == ["2027-01-28", "2027-02-02"] and ev["estimated"] is True      # przedział dat = szacunek
    assert (ev["eps_avg"], ev["rev_avg"], ev["fy_end_month"], ev["ex_dividend"]) == (2.31, 1.25e11, 9, "2026-11-20")
    only = parse_events("X", None, {"earningsTimestampStart": 1795000000})
    assert only["earnings"] == [pd.Timestamp(1795000000, unit="s").strftime("%Y-%m-%d")] and only["eps_avg"] is None


def test_feed_block_drops_unsafe_links():
    b = B.feed([{"title": "A", "url": "javascript:alert(1)", "tag": "X"}, {"title": "B", "url": "https://ok.example/x"}])
    assert [i["url"] for i in b["items"]] == [None, "https://ok.example/x"] and b["kind"] == "feed"


# --------------------------------------------------------------------------- ekran

@pytest.fixture()
def api():
    c = create_app(SessionStore(), provider_factory=synthetic_provider).test_client()
    return c, strict_json(c.post("/api/session", data={"demo": "1"}))["sid"]


def _table(out, title):
    return next(b for b in out["blocks"] if b["kind"] == "table" and b["title"] == title)


def test_list_screen_is_first_in_companies_section(api):
    c, sid = api
    comp = c.get("/api/health").get_json()["nav"]["tabs"][1]                 # druga zakładka na dolnym pasku
    assert comp["id"] == "companies" and [s["id"] for s in comp["screens"]] == ["companies-list", "companies"]
    out = strict_json(c.get(f"/api/session/{sid}/screen/companies-list"))
    check_blocks(out["blocks"], {})
    assert out["blocks"][0]["text"].startswith("Na ostatniej sesji")
    rows = _table(out, "Twoje pozycje")["rows"]
    assert len(rows) == 13 and all(r["price"] and r["cls"] in ("akcja", "ETF", "CFD") for r in rows)
    weights = [abs(r["weight"]) for r in rows]
    assert weights == sorted(weights, reverse=True)                                    # domyślnie od największej pozycji
    signed = {col["key"] for col in _table(out, "Twoje pozycje")["columns"] if col["signed"]}
    assert signed == {"chg", "day", "since"}
    # suma kwot dziennych = wkład x kapitał (ta sama liczba w zdaniu i w tabeli)
    contrib = next(m for b in out["blocks"] if b["kind"] == "metrics" for m in b["items"] if m["label"] == "Zmiana wartości pozycji")
    equity = strict_json(c.get(f"/api/session/{sid}"))["equity"]
    assert sum(r["day"] for r in rows) == pytest.approx(contrib["value"] * equity, rel=1e-6)


def test_list_sorting_reports_and_news(api):
    c, sid = api
    by_chg = _table(strict_json(c.get(f"/api/session/{sid}/screen/companies-list?sort=change")), "Twoje pozycje")["rows"]
    ch = [r["chg"] for r in by_chg]
    assert ch == sorted(ch, reverse=True)
    out = strict_json(c.get(f"/api/session/{sid}/screen/companies-list?sort=report"))
    rep = _table(out, "Najbliższe raporty kwartalne i roczne")["rows"]
    assert [r["days"] for r in rep] == sorted(r["days"] for r in rep) and all(r["days"] >= 0 for r in rep)
    assert all(r["what"].startswith(("Q", "roczny")) for r in rep)
    names_with_report = {r["name"] for r in rep}
    listed = {r["name"]: r for r in _table(out, "Twoje pozycje")["rows"]}
    assert all(listed[n]["report"] != "–" for n in names_with_report)
    assert all(r["report"] == "–" for r in listed.values() if r["cls"] == "ETF")       # ETF-y nie mają raportów
    feed = next(b for b in out["blocks"] if b["kind"] == "feed")
    tags = [i["tag"] for i in feed["items"]]
    assert len(feed["items"]) <= 15 and max(tags.count(t) for t in set(tags)) <= 3
    times = [i["time"] for i in feed["items"]]
    assert times == sorted(times, reverse=True)
    pick = next(o["value"] for b in out["blocks"] if b["kind"] == "controls" for it in b["items"] if it["name"] == "news"
                for o in it["options"] if o["value"])
    one = next(b for b in strict_json(c.get(f"/api/session/{sid}/screen/companies-list?news={pick}"))["blocks"] if b["kind"] == "feed")
    assert len({i["tag"] for i in one["items"]}) == 1


def test_list_falls_back_to_last_close_when_quotes_fail(monkeypatch):
    def boom(self, row, today=None, parts=()):
        raise RuntimeError("brak sieci")
    monkeypatch.setattr(SyntheticMarketData, "brief", boom)
    c = create_app(SessionStore(), provider_factory=synthetic_provider).test_client()
    sid = strict_json(c.post("/api/session", data={"demo": "1"}))["sid"]
    out = strict_json(c.get(f"/api/session/{sid}/screen/companies-list"))
    check_blocks(out["blocks"], {})
    assert len(_table(out, "Twoje pozycje")["rows"]) == 13                               # lista działa bez bieżących notowań
    notes = " ".join(b["text"] for b in out["blocks"] if b["kind"] == "note")
    assert "ostatnie zamknięcie" in notes and "Nie znalazłem zapowiedzianych terminów" in notes
    assert not any(b["kind"] == "feed" for b in out["blocks"])


def test_list_ignores_analysis_period_and_respects_cfd_switch(api):
    c, sid = api
    full = _table(strict_json(c.get(f"/api/session/{sid}/screen/companies-list")), "Twoje pozycje")["rows"]
    c.put(f"/api/session/{sid}/view", json={"since": "2025-06-01", "until": "2026-03-31"})
    cut = _table(strict_json(c.get(f"/api/session/{sid}/screen/companies-list")), "Twoje pozycje")["rows"]
    assert {r["name"] for r in cut} == {r["name"] for r in full}                       # lista jest zawsze "na dziś"
    c.put(f"/api/session/{sid}/view", json={"since": "", "until": "", "exclude_cfd": True})
    no_cfd = _table(strict_json(c.get(f"/api/session/{sid}/screen/companies-list")), "Twoje pozycje")["rows"]
    assert all(r["cls"] != "CFD" for r in no_cfd) and len(no_cfd) < len(full)
