"""Kalendarz: siatka dni, serie, statystyki dni, kwartały obrotowe, parser terminów z yfinance, ekrany."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from core import calendar as K
from data.providers import parse_events
from server.app import create_app
from server.sessions import SessionStore
from tests.test_server import check_blocks, strict_json, synthetic_provider


def test_month_grid_by_hand():
    idx = pd.to_datetime(["2026-10-30", "2026-11-02", "2026-11-03", "2026-11-04"])
    r = pd.Series([0.01, 0.02, -0.01, 0.0], index=idx)
    pnl = pd.Series([100.0, 200.0, -100.0, 0.0], index=idx)
    g = K.month_grid(r, pnl, None, None)
    assert [m["label"] for m in g] == ["listopad 2026", "październik 2026"]         # najnowszy pierwszy
    nov = g[0]
    assert nov["lead"] == 6 and len(nov["days"]) == 30                                # 1.11.2026 to niedziela
    assert nov["days"][0] is None and nov["days"][1] == {"v": 0.02, "pnl": 200.0, "b": None}
    assert nov["total"] == pytest.approx(1.02 * 0.99 - 1) and nov["pnl_total"] == pytest.approx(100.0)
    assert (nov["up"], nov["down"]) == (1, 1)
    assert len(K.month_grid(r, pnl, None, 1)) == 1


def test_streaks_and_day_stats():
    r = pd.Series([0.01, 0.02, 0.0, 0.01, -0.01, -0.02, 0.03, -0.01, -0.01, -0.01])
    s = K.streaks(r)
    assert s == {"current": -3, "longest_up": 3, "longest_down": 3}                   # zero nie przerywa serii
    assert K.streaks(pd.Series([0.01, 0.02]))["current"] == 2
    idx = pd.bdate_range("2026-01-05", periods=4)
    st = K.day_stats(pd.Series([0.02, -0.01, 0.04, 0.0], index=idx))
    assert st["n"] == 3 and st["positive"] == pytest.approx(2 / 3) and st["avg_up"] == pytest.approx(0.03)
    assert st["win_loss"] == pytest.approx(3.0) and st["best_day"] == idx[2] and st["worst_day"] == idx[1]


def test_by_weekday_and_scale():
    idx = pd.bdate_range("2026-01-05", periods=10)                                    # dwa pełne tygodnie
    r = pd.Series([0.01, 0, 0, 0, -0.02, 0.03, 0, 0, 0, -0.04], index=idx)
    wd = K.by_weekday(r)
    assert wd["Pn"] == pytest.approx(0.02) and wd["Pt"] == pytest.approx(-0.03) and np.isnan(wd["Wt"])
    assert K.robust_scale(pd.Series([0.01] * 99 + [0.5])) == pytest.approx(0.01)       # skrajny dzień nie zawyża skali


def test_position_moves():
    mv = K.position_moves(pd.Series({"A": 50.0, "B": -20.0, "C": 5.0}), pd.Series({"A": 1000.0, "B": -400.0, "C": 0.0}))
    assert mv.loc["A", "ret"] == pytest.approx(0.05) and mv.loc["B", "ret"] == pytest.approx(-0.05) and "C" not in mv.index


@pytest.mark.parametrize("date,fy,label", [
    ("2026-10-28", 12, "wyniki Q3 2026"),                       # kalendarzowy rok obrotowy
    ("2027-02-05", 12, "wyniki roczne 2026 (Q4)"),
    ("2026-12-10", 5, "wyniki Q2 2027 r. obr."),                # Oracle: rok obrotowy do maja
    ("2027-01-29", 9, "wyniki Q1 2027 r. obr."),                # Apple: do września
    ("2026-11-20", 1, "wyniki Q3 2027 r. obr."),                # Nvidia: do stycznia
    ("2026-07-25", 6, "wyniki roczne 2026 (Q4) r. obr."),       # Microsoft: do czerwca
    ("2026-01-05", None, "wyniki Q3 2025"),                     # 5 dni po końcu kwartału - to jeszcze poprzedni okres
])
def test_fiscal_period(date, fy, label):
    assert K.fiscal_period(date, fy)["label"] == label


def test_parse_events_from_yfinance_shapes():
    cal = {"Dividend Date": dt.date(2026, 12, 11), "Ex-Dividend Date": dt.date(2026, 11, 20),
           "Earnings Date": [dt.date(2027, 1, 28), dt.date(2027, 2, 2)], "Earnings High": 2.5, "Earnings Low": 2.1,
           "Earnings Average": 2.31, "Revenue High": 1.3e11, "Revenue Low": 1.2e11, "Revenue Average": 1.25e11}
    info = {"currency": "USD", "isEarningsDateEstimate": True, "nextFiscalYearEnd": 1790726400,     # 30.09.2026
            "dividendRate": 1.04}
    hist = pd.DataFrame({"epsActual": [1.6, 1.85], "epsEstimate": [1.55, 1.78], "surprisePercent": [0.032, 0.039]},
                        index=pd.to_datetime(["2026-06-30", "2026-09-30"]))
    ev = parse_events("AAPL", cal, info, hist)
    assert ev["earnings"] == ["2027-01-28", "2027-02-02"] and ev["estimated"] is True
    assert (ev["eps_avg"], ev["rev_avg"], ev["fy_end_month"]) == (2.31, 1.25e11, 9)
    assert ev["ex_dividend"] == "2026-11-20" and ev["dividend_date"] == "2026-12-11" and ev["dividend_rate"] == 1.04
    assert (ev["last_quarter_end"], ev["last_eps_actual"], ev["last_surprise"]) == ("2026-09-30", 1.85, 0.039)
    # tylko info (spółka bez kalendarza): data z sekund epoki
    only = parse_events("X", None, {"earningsTimestampStart": 1795000000, "currency": "EUR"})
    assert only["earnings"] == [pd.Timestamp(1795000000, unit="s").strftime("%Y-%m-%d")] and only["eps_avg"] is None
    assert parse_events("Y", {}, {})["earnings"] == []


# --------------------------------------------------------------------------- ekrany

@pytest.fixture(scope="module")
def api():
    c = create_app(SessionStore(), provider_factory=synthetic_provider).test_client()
    return c, strict_json(c.post("/api/session", data={"demo": "1"}))["sid"]


def test_nav_has_calendar(api):
    c, _ = api
    cal = next(m for m in c.get("/api/health").get_json()["nav"]["more"] if m["id"] == "calendar")
    assert [s["id"] for s in cal["screens"]] == ["calendar-days", "calendar-events"]


def test_days_screen(api):
    c, sid = api
    out = strict_json(c.get(f"/api/session/{sid}/screen/calendar-days?months=3"))
    check_blocks(out["blocks"], {})
    cal = next(b for b in out["blocks"] if b["kind"] == "chart" and b["chart"]["type"] == "calendar")["chart"]
    assert len(cal["months"]) == 3 and cal["scale"] > 0 and cal["bench_name"]
    day = next(d for d in cal["months"][0]["days"] if d)
    assert {"v", "pnl"} <= set(day)
    last = next(b for b in out["blocks"] if b["kind"] == "table" and b["title"] == "Ostatnie sesje")
    assert next(col for col in last["columns"] if col["key"] == "ret")["signed"] is True and len(last["rows"]) == 30
    allm = strict_json(c.get(f"/api/session/{sid}/screen/calendar-days?months=0"))
    n = next(b for b in allm["blocks"] if b["kind"] == "chart" and b["chart"]["type"] == "calendar")["chart"]["months"]
    assert len(n) >= 24                                                               # cała historia demo (2 lata)
    # wybór sesji zmienia tabelę ruchów pozycji
    days = [o["value"] for b in out["blocks"] if b["kind"] == "controls" for it in b["items"] if it["name"] == "day" for o in it["options"]]
    a = strict_json(c.get(f"/api/session/{sid}/screen/calendar-days?day={days[0]}"))
    b = strict_json(c.get(f"/api/session/{sid}/screen/calendar-days?day={days[3]}"))
    title = lambda o: next(x["title"] for x in o["blocks"] if x["kind"] == "chart" and str(x.get("title", "")).startswith("Ruchy"))
    assert title(a) != title(b)


def test_events_screen(api):
    c, sid = api
    out = strict_json(c.get(f"/api/session/{sid}/screen/calendar-events?horizon=180"))
    check_blocks(out["blocks"], {})
    assert out["blocks"][0]["text"].startswith("Najbliżej:")
    up = next(b for b in out["blocks"] if b["kind"] == "table" and b["title"] == "Nadchodzące wyniki")
    dates = [r["when"] for r in up["rows"]]
    assert dates == sorted(dates, key=lambda d: d[6:] + d[3:5] + d[:2]) and all(r["days"] >= 0 for r in up["rows"])
    assert all(r["what"].startswith("wyniki") for r in up["rows"])
    marks = [d for m in next(b for b in out["blocks"] if b["kind"] == "chart" and b["chart"]["type"] == "calendar")["chart"]["months"]
             for d in m["days"] if d]
    assert marks and all("mark" in d for d in marks)
    short = strict_json(c.get(f"/api/session/{sid}/screen/calendar-events?horizon=30"))
    rows = lambda o: next((b["rows"] for b in o["blocks"] if b["kind"] == "table" and b["title"] == "Nadchodzące wyniki"), [])
    assert len(rows(short)) < len(rows(out))


def test_events_screen_with_missing_data(monkeypatch):
    from data.synthetic import SyntheticMarketData
    def boom(self, row, today=None):
        raise RuntimeError("brak sieci")
    monkeypatch.setattr(SyntheticMarketData, "events", boom)
    c = create_app(SessionStore(), provider_factory=synthetic_provider).test_client()
    sid = strict_json(c.post("/api/session", data={"demo": "1"}))["sid"]
    out = strict_json(c.get(f"/api/session/{sid}/screen/calendar-events"))
    assert "Nie znalazłem" in out["blocks"][0]["text"] and any(b["kind"] == "note" and "Brak danych" in b["text"] for b in out["blocks"])
