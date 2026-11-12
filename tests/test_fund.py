"""Miary funduszowe: long/short, beta, przechwytywanie rynku, epizody obsunięć, limity."""
import numpy as np
import pandas as pd
import pytest

from core import hedgefund as H
from server.app import create_app
from server.sessions import SessionStore
from tests.test_server import check_blocks, strict_json, synthetic_provider


def _daily(monthly: list[float], start="2025-01-01") -> pd.Series:
    """Szereg dzienny, w którym każdy miesiąc ma zadaną stopę (cały ruch pierwszego dnia miesiąca)."""
    days = pd.bdate_range(start, periods=21 * len(monthly) + 40)
    r = pd.Series(0.0, index=days)
    months = sorted({(d.year, d.month) for d in days})[:len(monthly)]
    for (y, m), v in zip(months, monthly):
        r[days[(days.year == y) & (days.month == m)][0]] = v
    return r[[(d.year, d.month) in months for d in days]]


def test_long_short_exposure_by_hand():
    exp = pd.Series({"A": 600.0, "B": 300.0, "C": -200.0})
    ls = H.long_short(exp, 1000.0, pd.Series({"A": 1.5, "B": 0.5, "C": 1.0}))
    assert (ls["long"], ls["short"], ls["gross"], ls["net"]) == pytest.approx((0.9, 0.2, 1.1, 0.7))
    assert (ls["n_long"], ls["n_short"]) == (2, 1) and ls["cash"] == pytest.approx(0.3)
    assert ls["beta_adjusted"] == pytest.approx((600 * 1.5 + 300 * 0.5 - 200 * 1.0) / 1000)
    assert H.long_short(exp, 1000.0, pd.Series({"A": 1.5}))["beta_adjusted"] == pytest.approx((900 + 300 - 200) / 1000)   # brak bety = 1


def test_hedge_notional():
    assert H.hedge_notional(0.85, 100_000.0, 0.0) == pytest.approx(85_000.0)       # tyle trzeba mieć na krótko, by beta = 0
    assert H.hedge_notional(0.85, 100_000.0, 0.5) == pytest.approx(35_000.0)
    assert H.hedge_notional(0.30, 100_000.0, 0.5) == pytest.approx(-20_000.0)      # ujemny: za mało rynku, nie za dużo


def test_asset_betas():
    rng = np.random.default_rng(1)
    b = pd.Series(rng.normal(0, 0.01, 300), index=pd.bdate_range("2025-01-01", periods=300))
    a = pd.DataFrame({"X": 1.5 * b, "Y": -0.5 * b + 0.001, "Z": b.iloc[:10]})
    out = H.asset_betas(a, b)
    assert out["X"] == pytest.approx(1.5) and out["Y"] == pytest.approx(-0.5) and np.isnan(out["Z"])


def test_capture_ratios_on_months():
    """Benchmark: +10%, -10%, +5%, -5%. Portfel bierze połowę spadków i 120% wzrostów."""
    b = _daily([0.10, -0.10, 0.05, -0.05])
    p = _daily([0.12, -0.05, 0.06, -0.025])
    c = H.capture_ratios(p, b)
    assert c["up"] == pytest.approx(1.2) and c["down"] == pytest.approx(0.5) and (c["up_months"], c["down_months"]) == (2, 2)
    ms = H.month_stats(p)
    assert ms["positive"] == 0.5 and ms["best"] == pytest.approx(0.12) and ms["worst"] == pytest.approx(-0.05)


def test_drawdown_episodes_by_hand():
    days = pd.bdate_range("2025-01-06", periods=8)
    r = pd.Series([0.0, -0.10, -0.10, 0.25, 0.0, -0.05, 0.02, 0.0], index=days)      # 100 -> 90 -> 81 -> 101,25 -> ... -> 96,19
    ep = H.drawdown_episodes(r)
    assert len(ep) == 2
    first = ep.iloc[0]
    assert first["depth"] == pytest.approx(-0.19) and first["peak"] == days[0] and first["trough"] == days[2]
    assert first["recovery"] == days[3] and not first["ongoing"] and first["days_down"] == 2
    second = ep.iloc[1]
    assert second["depth"] == pytest.approx(-0.05) and second["ongoing"] and pd.isna(second["recovery"])


def test_rolling_stats_beta_and_vol():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2025-01-01", periods=200)
    b = pd.Series(rng.normal(0, 0.01, 200), index=idx)
    roll = H.rolling_stats(0.8 * b, b, 63)
    assert roll["beta"].dropna().iloc[-1] == pytest.approx(0.8)
    assert roll["vol"].iloc[-1] == pytest.approx((0.8 * b).iloc[-63:].std(ddof=1) * np.sqrt(252))
    assert len(roll) == 200 - 62


def test_tail_ratio_and_omega():
    r = pd.Series([0.02] * 50 + [-0.01] * 50)
    assert H.tail_ratio(r) == pytest.approx(2.0) and H.omega(r) == pytest.approx(2.0)


def test_limits_status():
    t = H.check_limits([
        {"name": "pozycja", "value": 0.20, "limit": 0.15, "kind": "max"},
        {"name": "sektor", "value": 0.30, "limit": 0.35, "kind": "max"},
        {"name": "var", "value": 0.01, "limit": 0.03, "kind": "max"},
        {"name": "gotówka", "value": 0.01, "limit": 0.02, "kind": "min"},
        {"name": "brak", "value": float("nan"), "limit": 0.02, "kind": "max"}]).set_index("name")
    assert t["status"].to_dict() == {"pozycja": "przekroczony", "sektor": "blisko", "var": "w normie",
                                     "gotówka": "przekroczony", "brak": "brak danych"}
    assert t.at["pozycja", "usage"] == pytest.approx(0.20 / 0.15) and t.at["gotówka", "usage"] == pytest.approx(2.0)


# --------------------------------------------------------------------------- ekrany

@pytest.fixture(scope="module")
def api():
    c = create_app(SessionStore(), provider_factory=synthetic_provider).test_client()
    return c, strict_json(c.post("/api/session", data={"demo": "1"}))["sid"]


def test_fund_screens_render(api):
    c, sid = api
    nav = c.get("/api/health").get_json()["nav"]
    where = {scr["id"]: sec["id"] for sec in nav["tabs"] + nav["more"] for scr in sec["screens"]}
    assert (where["perf-summary"], where["fund-longshort"], where["fund-limits"]) == ("perf", "risk", "risk")
    assert "fund-tearsheet" not in where and not any(sec["id"] == "fund" for sec in nav["more"])
    assert {b["value"] for b in c.get("/api/health").get_json()["settings"]["bench_choices"]} == {"SP500", "NDX", "MSCIW"}
    for name, q in (("perf-summary", "bench=NDX&window=126"), ("fund-longshort", "bench=SP500&target=0.5"),
                    ("fund-limits", "pos=5&cash=50")):
        r = c.get(f"/api/session/{sid}/screen/{name}?{q}")
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        check_blocks(strict_json(r)["blocks"], {})
    text = c.get(f"/api/session/{sid}/screen/perf-summary?bench=NDX").get_data(as_text=True)
    assert "MWR / XIRR" in text and "Tracking error" in text and "Czas pod wodą" in text      # to, co miały stare Statystyki
    assert "Nasdaq 100" in text and "Przechwytywanie spadków" in text and "Największe obsunięcia" in text


def test_limits_react_to_thresholds(api):
    c, sid = api
    tight = strict_json(c.get(f"/api/session/{sid}/screen/fund-limits?pos=1&sector=1&top5=1&gross=1&var=0.01&dd=0.01&cash=99"))
    loose = strict_json(c.get(f"/api/session/{sid}/screen/fund-limits?pos=500&sector=500&top5=500&gross=900&var=90&dd=90&cash=0.01"))
    status = lambda out: [r["status"] for b in out["blocks"] if b["kind"] == "table" for r in b["rows"]]
    assert status(tight).count("przekroczony") >= 6 and "przekroczon" in tight["blocks"][0]["text"]
    assert set(status(loose)) == {"w normie"} and "zachowane" in loose["blocks"][0]["text"]


def test_hedge_calculator_scales_with_target_beta(api):
    c, sid = api
    def hedge(target):
        out = strict_json(c.get(f"/api/session/{sid}/screen/fund-longshort?target={target}"))
        return next(m["value"] for b in out["blocks"] if b["kind"] == "metrics" and b.get("title", "") and "zabezpieczenia" in b["title"]
                    for m in b["items"] if m["label"].startswith("Nominał"))
    assert hedge(0.0) > hedge(0.5) > 0


def test_wig20_is_loaded_only_for_polish_holdings():
    from data.synthetic import SyntheticMarketData
    from server.context import build_context
    from tests import real_layout as R
    us = build_context([R.usd_account()], provider=SyntheticMarketData())
    assert "WIG20" not in us.market.benchmarks_base.columns and not any("WIG20" in w for w in us.pf.warnings)
    assert "WIG20" in build_context(None).market.benchmarks_base.columns       # demo ma polskie akcje -> potrzebny do scenariuszy
