"""Testy API: każdy ekran zwraca poprawny, kompletny JSON; sesje, logowanie, wgrywanie plików."""
import io
import json
import math

import pytest

from server.app import create_app
from server.screens import NAV, SCREENS
from server.sessions import SessionStore

CHART_TYPES = {"line", "hbar", "bars", "heatmap", "hist", "fan"}
FMTS = {"pln", "pct", "num", "int", "days", "x", "text"}


def synthetic_provider(is_demo):
    from data.synthetic import SyntheticMarketData
    return SyntheticMarketData()


@pytest.fixture(scope="module")
def client():
    app = create_app(SessionStore(), provider_factory=synthetic_provider)
    return app.test_client()


@pytest.fixture(scope="module")
def sid(client):
    r = client.post("/api/session", data={"demo": "1"})
    assert r.status_code == 201, r.get_data(as_text=True)
    return r.get_json()["sid"]


def strict_json(resp):
    """Odrzuca NaN/Infinity - przeglądarka nie sparsowałaby takiej odpowiedzi."""
    def bad(x):
        raise ValueError(f"niedozwolona stała w JSON: {x}")
    return json.loads(resp.get_data(as_text=True), parse_constant=bad)


def check_chart(c):
    assert c["type"] in CHART_TYPES, c["type"]
    assert c.get("fmt", "num") in FMTS
    if c["type"] == "line":
        assert len(c["x"]) >= 2 and c["series"]
        for s in c["series"]:
            assert len(s["values"]) == len(c["x"]) and s["name"]
    elif c["type"] in ("hbar", "bars"):
        assert c["labels"]
        if "series" in c:
            for s in c["series"]:
                assert len(s["values"]) == len(c["labels"])
        else:
            assert len(c["values"]) == len(c["labels"])
    elif c["type"] == "heatmap":
        assert len(c["z"]) == len(c["rows"]) and all(len(r) == len(c["cols"]) for r in c["z"])
    elif c["type"] == "hist":
        assert len(c["edges"]) == len(c["counts"]) + 1 and sum(c["counts"]) > 0
    elif c["type"] == "fan":
        assert len(c["median"]) == len(c["x"])
        for b in c["bands"]:
            assert len(b["lo"]) == len(c["x"]) == len(b["hi"])


def check_blocks(blocks, counts):
    assert isinstance(blocks, list) and blocks
    for b in blocks:
        kind = b["kind"]
        counts[kind] = counts.get(kind, 0) + 1
        if kind == "metrics":
            assert b["items"]
            for m in b["items"]:
                assert m["label"] and m["fmt"] in FMTS
                assert m["value"] is None or isinstance(m["value"], (int, float, str))
        elif kind == "chart":
            check_chart(b["chart"])
        elif kind == "table":
            keys = [c["key"] for c in b["columns"]]
            assert keys and all(c["fmt"] in FMTS for c in b["columns"])
            for row in b["rows"]:
                assert set(keys) <= set(row)
        elif kind == "section":
            check_blocks(b["blocks"], counts)
        elif kind == "controls":
            for c in b["items"]:
                assert c["name"] and c["type"] in {"segmented", "select", "number", "slider", "toggle", "text"}
                if c["type"] in ("segmented", "select"):
                    assert c["options"] and any(o["value"] == c["value"] for o in c["options"])
        elif kind == "statement":
            assert len(b["text"]) > 20
        elif kind in ("note", "bullets", "download"):
            assert b.get("text") or b.get("items") or b.get("path")
        else:
            raise AssertionError(f"nieznany rodzaj bloku: {kind}")


def test_health_and_nav(client):
    h = client.get("/api/health").get_json()
    assert h["ok"] and h["auth_required"] is False
    listed = [s["id"] for tab in h["nav"]["tabs"] for s in tab["screens"]]
    assert set(listed) <= set(SCREENS), "nawigacja wskazuje na nieistniejący ekran"
    assert len(h["nav"]["tabs"]) <= 4            # + "Więcej" = maks. 5 pozycji na dolnym pasku
    from server.screens import CLIENT_SCREENS
    more = [s["id"] for m in h["nav"]["more"] for s in m["screens"]]
    assert set(more) <= set(SCREENS) | CLIENT_SCREENS
    assert {c["value"] for c in h["settings"]["bench_choices"]} == {"MSCIW", "SP500", "WIG", "WIG20"}


def test_session_meta(client, sid):
    m = strict_json(client.get(f"/api/session/{sid}"))
    assert m["is_demo"] and m["as_of"] == "2026-11-06" and m["summary"]["instrumenty"] >= 15
    assert len(m["mapping"]) == m["summary"]["instrumenty"] and m["unmapped"] == [] and m["equity"] > 100000


@pytest.mark.parametrize("name", sorted(SCREENS))
def test_every_screen_renders(client, sid, name):
    r = client.get(f"/api/session/{sid}/screen/{name}")
    assert r.status_code == 200, r.get_data(as_text=True)[:500]
    out = strict_json(r)
    assert out["title"]
    counts = {}
    check_blocks(out["blocks"], counts)
    assert sum(counts.values()) >= 2, f"ekran {name} jest prawie pusty: {counts}"
    assert out["ms"] < 15000


@pytest.mark.parametrize("name,params", [
    ("perf-summary", {"period": "ytd"}), ("perf-summary", {"period": "12m", "bench": "WIG", "rf": "0.06"}),
    ("perf-benchmarks", {"period": "12m"}), ("perf-attribution", {"period": "ytd"}),
    ("risk-factors", {"freq": "D"}), ("perf-summary", {"period": "bzdura", "rf": "abc", "bench": "XXX"}),
])
def test_screen_params(client, sid, name, params):
    r = client.get(f"/api/session/{sid}/screen/{name}", query_string=params)
    assert r.status_code == 200
    check_blocks(strict_json(r)["blocks"], {})


def test_overview_numbers_match_engine(client, sid, portfolio):
    out = strict_json(client.get(f"/api/session/{sid}/screen/overview"))
    items = {m["label"]: m["value"] for b in out["blocks"] if b["kind"] == "metrics" for m in b["items"]}
    assert items["Wartość portfela"] == pytest.approx(float(portfolio.equity.iloc[-1]))
    assert items["Wynik całkowity"] == pytest.approx(portfolio.total_pnl())
    assert len(items) == 6
    assert out["blocks"][0]["kind"] == "statement" and "19 dni na 20" in out["blocks"][0]["text"]
    tips = [m["tip"] for b in out["blocks"] if b["kind"] == "metrics" for m in b["items"]]
    assert all(tips), "każda liczba na Przeglądzie musi mieć objaśnienie"


def test_every_metric_has_explanation(client, sid):
    missing = []
    for name in SCREENS:
        out = client.get(f"/api/session/{sid}/screen/{name}").get_json()

        def walk(blocks):
            for b in blocks:
                if b["kind"] == "metrics":
                    missing.extend(f"{name}: {m['label']}" for m in b["items"] if not m.get("tip") and not m.get("note"))
                elif b["kind"] == "section":
                    walk(b["blocks"])
        walk(out["blocks"])
    assert not missing, missing


def test_unknown_screen_and_expired_session(client, sid):
    r = client.get(f"/api/session/{sid}/screen/nie-ma")
    assert r.status_code == 404 and r.get_json()["code"] == "no_screen"
    r = client.get("/api/session/nieistniejaca/screen/overview")
    assert r.status_code == 404 and r.get_json()["code"] == "session_expired"
    assert client.get("/api/cokolwiek").status_code == 404


def test_upload_real_file_and_mapping_update(client, sample_path):
    with open(sample_path, "rb") as fh:
        data = fh.read()
    r = client.post("/api/session", data={"files": (io.BytesIO(data), "eksport.xlsx")}, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_data(as_text=True)
    m = r.get_json()
    assert m["is_demo"] is False and m["summary"]["otwarte pozycje"] > 0
    sid2 = m["sid"]
    before = client.get(f"/api/session/{sid2}/screen/overview").get_json()
    r = client.put(f"/api/session/{sid2}/mapping", json={"mapping": [{"symbol": "PKN.PL", "sector": "Paliwa", "name": "Orlen SA"}]})
    assert r.status_code == 200
    row = next(x for x in r.get_json()["mapping"] if x["symbol"] == "PKN.PL")
    assert row["sector"] == "Paliwa" and row["yf_ticker"] == "PKN.WA"
    after = client.get(f"/api/session/{sid2}/screen/overview").get_json()
    assert before["blocks"][2]["items"][0]["value"] == pytest.approx(after["blocks"][2]["items"][0]["value"])
    assert client.delete(f"/api/session/{sid2}").status_code == 200
    assert client.get(f"/api/session/{sid2}").status_code == 404


def test_upload_errors_are_readable(client):
    r = client.post("/api/session", data={"files": (io.BytesIO(b"a;b;c\n1;2;3\n"), "cos.csv")}, content_type="multipart/form-data")
    assert r.status_code == 422 and "Nie rozpoznano" in r.get_json()["error"]
    r = client.post("/api/session", data={})
    assert r.status_code == 400 and r.get_json()["code"] == "no_file"


def test_password_protection(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "tajne-haslo")
    c = create_app(SessionStore(), provider_factory=synthetic_provider).test_client()
    assert c.get("/api/health").get_json()["auth_required"] is True
    assert c.post("/api/session", data={"demo": "1"}).status_code == 401
    assert c.post("/api/login", json={"password": "zle"}).status_code == 401
    token = c.post("/api/login", json={"password": "tajne-haslo"}).get_json()["token"]
    assert len(token) == 64
    r = c.post("/api/session", data={"demo": "1"}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    assert c.get(f"/api/session/{r.get_json()['sid']}/screen/overview").status_code == 401
    assert c.get("/").status_code == 200          # powłoka aplikacji jest publiczna, dane nie
    for _ in range(8):
        c.post("/api/login", json={"password": "zle"})
    assert c.post("/api/login", json={"password": "zle"}).status_code == 429


def test_session_store_evicts_oldest_and_expires():
    s = SessionStore(max_sessions=2, ttl=1000)
    a, b = s.put("A"), s.put("B")
    assert s.get(a) == "A"                        # odświeża czas użycia A
    c = s.put("C")
    assert s.get(b) is None and s.get(a) == "A" and s.get(c) == "C"
    short = SessionStore(ttl=-1)
    assert short.get(short.put("X")) is None


def test_jsonable_handles_numpy_and_nan():
    import numpy as np
    import pandas as pd
    from server.blocks import jsonable
    out = jsonable({"a": np.float64("nan"), "b": np.int64(3), "c": pd.Timestamp("2025-01-02"), "d": [np.inf, 1.5],
                    "e": pd.Series([1.0, float("nan")]), "f": pd.NaT, "g": np.bool_(True)})
    assert out == {"a": None, "b": 3, "c": "2025-01-02", "d": [None, 1.5], "e": [1.0, None], "f": None, "g": True}
    assert math.isfinite(json.loads(json.dumps(out))["d"][1])
