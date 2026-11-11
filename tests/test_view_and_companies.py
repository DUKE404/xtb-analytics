"""Widok portfela (bez CFD, okres analizy) i moduł Spółki (odwrócony DCF, dane fundamentalne)."""
import numpy as np
import pandas as pd
import pytest

from core import fundamentals as F
from data.loader import BENCHMARK_KEYS, export_between, resolve_since, resolve_until, without_cfd
from data.providers import _estimate_rows, _stmt_rows
from data.synthetic import SyntheticMarketData
from server.app import create_app
from server.context import build_context
from server.sessions import SessionStore
from tests import real_layout as R
from tests.test_server import check_blocks, strict_json, synthetic_provider


# --------------------------------------------------------------------------- odwrócony DCF

def test_dcf_value_by_hand():
    """FCF 100, wzrost = dyskonto = 10%: każdy z 10 lat wart 100, a wartość rezydualna 100 x 1,025 / 0,075."""
    assert F.dcf_value(100, 0.10, 0.10, 0.025, 10) == pytest.approx(1000 + 100 * 1.025 / 0.075)
    assert np.isnan(F.dcf_value(100, 0.05, 0.02, 0.03, 10))             # dyskonto musi przewyższać wzrost wieczny


def test_reverse_dcf_recovers_the_growth_rate():
    for g in (-0.05, 0.0, 0.08, 0.25):
        value = F.dcf_value(50.0, g, 0.09, 0.02, 10)
        assert F.reverse_dcf(value, 50.0, 0.09, 0.02, 10) == pytest.approx(g, abs=1e-6)
    assert np.isnan(F.reverse_dcf(1000.0, -5.0, 0.09, 0.02))           # ujemne przepływy: pytanie bez odpowiedzi
    assert np.isnan(F.reverse_dcf(1e12, 1.0, 0.09, 0.02))              # wycena nie do uzasadnienia żadnym wzrostem z zakresu


def test_higher_discount_rate_needs_more_growth():
    grid = F.implied_growth_grid(2000.0, 100.0, [0.08, 0.10, 0.12], [0.02, 0.03], 10)
    assert grid[0]["g20"] < grid[1]["g20"] < grid[2]["g20"] and grid[1]["g30"] < grid[1]["g20"]


def test_cagr_and_target_gap():
    assert F.cagr([100, 110, 121]) == pytest.approx(0.10)
    assert np.isnan(F.cagr([-5, 10])) and np.isnan(F.cagr([10]))
    assert F.target_gap(200.0, 250.0) == pytest.approx(0.25) and np.isnan(F.target_gap(0.0, 10.0))


def test_statement_and_estimate_parsers():
    stmt = pd.DataFrame({pd.Timestamp("2025-06-30"): [245e9, 74e9], pd.Timestamp("2024-06-30"): [212e9, np.nan]},
                        index=["Total Revenue", "Free Cash Flow"])
    assert _stmt_rows(stmt, ["Total Revenue"]) == [{"year": 2024, "value": 212e9}, {"year": 2025, "value": 245e9}]
    assert _stmt_rows(stmt, ["Free Cash Flow"]) == [{"year": 2025, "value": 74e9}] and _stmt_rows(None, ["x"]) == []
    est = pd.DataFrame({"avg": [70e9, 300e9], "low": [68e9, 290e9], "high": [72e9, 310e9], "numberOfAnalysts": [30, 40],
                        "yearAgoRevenue": [62e9, 262e9], "growth": [0.13, 0.145]}, index=["0q", "+1y"])
    rows = _estimate_rows(est)
    assert rows[1] == {"period": "+1y", "avg": 300e9, "low": 290e9, "high": 310e9, "growth": 0.145, "analysts": 40.0,
                       "year_ago": 262e9}
    assert _estimate_rows(pd.DataFrame()) == []


# --------------------------------------------------------------------------- portfel bez CFD i okres analizy

def test_benchmarks_have_nasdaq_instead_of_wig():
    assert BENCHMARK_KEYS == ["WIG20", "SP500", "NDX", "MSCIW"]


def test_cfd_carve_out_turns_cfd_results_into_flows():
    full = build_context([R.cfd_account()], provider=SyntheticMarketData())
    eq = build_context([R.cfd_account()], provider=SyntheticMarketData(), exclude_cfd=True)
    assert full.has_cfd and eq.exclude_cfd and eq.pf.symbols == ["NVDA.US"]
    assert eq.pf.equity.iloc[-1] == pytest.approx(full.pf.equity.iloc[-1])            # gotówka dalej zgadza się z rachunkiem
    assert eq.pf.flows.sum() == pytest.approx(5000 - 2000 + 1600 + 1200)              # wynik CFD = wpłaty / wypłaty
    assert eq.pf.total_pnl() == pytest.approx(400.0)                                   # zostaje sam wynik na akcji
    assert (1 + eq.pf.returns).prod() - 1 == pytest.approx(400 / 5800, rel=0.02)
    assert len(eq.export.closed) == 1 and len(eq.export_pln.closed) == 4               # podatki widzą wszystko
    assert set(eq.export.cash["kind"]) == {"DEPOSIT", "TRANSFER", "STOCK_PURCHASE", "STOCK_SALE"}


def test_carve_out_without_cfd_is_a_no_op():
    ctx = build_context([R.usd_account()], provider=SyntheticMarketData(), exclude_cfd=True)
    assert not ctx.has_cfd and not ctx.exclude_cfd


def test_period_codes():
    as_of, first = pd.Timestamp("2026-11-06"), pd.Timestamp("2024-11-04")
    assert resolve_since("", as_of, first) is None and resolve_since("bzdura", as_of, first) is None
    assert resolve_since("ytd", as_of, first) == pd.Timestamp("2026-01-01")
    assert resolve_since("12m", as_of, first) == pd.Timestamp("2025-11-06")
    assert resolve_since("36m", as_of, first) is None                                  # dłużej niż historia = od początku
    assert resolve_since("2026-11-05", as_of, first) == pd.Timestamp("2026-10-23")     # zostają co najmniej 2 tygodnie
    assert resolve_until("2026-03-31", as_of, first) == pd.Timestamp("2026-03-31")
    assert resolve_until("2027-01-01", as_of, first) is None and resolve_until("", as_of, first) is None


def test_window_keeps_daily_returns_and_pnl_identity():
    full = build_context(None).pf
    ctx = build_context(None, since="2025-06-01", until="2026-03-31")
    pf = ctx.pf
    assert pf.dates[0] == pd.Timestamp("2025-06-02") and pf.dates[-1] == pd.Timestamp("2026-03-31")
    pd.testing.assert_series_equal(pf.returns, full.returns.loc[pf.dates])
    assert pf.equity.iloc[-1] == full.equity.loc["2026-03-31"]
    assert pf.flows.iloc[0] == pytest.approx(full.equity.loc[:"2025-05-31"].iloc[-1] + full.flows.loc["2025-06-02"])
    assert pf.equity.iloc[-1] - pf.flows.sum() == pytest.approx(pf.pnl.values.sum())
    # skład i otwarte pozycje są na koniec okna, nie na dzień raportu
    held = set(pf.qty.columns[pf.qty.iloc[-1] != 0])
    assert held == set(ctx.export.open["symbol"]) and ctx.export.as_of == pd.Timestamp("2026-03-31")
    assert ctx.export.closed["close_time"].between("2025-06-01", "2026-04-01").all()
    assert ctx.asset_returns().index.max() <= pd.Timestamp("2026-03-31")
    assert ctx.pf_full.dates[0] == full.dates[0]                                        # podatki mają pełną historię


def test_window_hides_instruments_not_held_in_period():
    full, cut = build_context(None).pf, build_context(None, since="12m").pf
    assert set(cut.symbols) < set(full.symbols) and list(cut.meta.index) == cut.symbols


def test_export_between_moves_later_closed_positions_to_open():
    exp = build_context(None).export
    cut = export_between(exp, None, "2025-12-31")
    later = exp.closed[(exp.closed["open_time"] < "2026-01-01") & (exp.closed["close_time"] >= "2026-01-01")]
    assert len(later) and set(later["position"]) <= set(cut.open["position"])
    assert (cut.cash["time"] < "2026-01-01").all() and (cut.closed["close_time"] < "2026-01-01").all()
    assert without_cfd(exp, build_context(None).mapping).closed["symbol"].isin(["US500", "DE40", "GOLD"]).sum() == 0


# --------------------------------------------------------------------------- API

@pytest.fixture(scope="module")
def client():
    return create_app(SessionStore(), provider_factory=synthetic_provider).test_client()


def test_api_view_options_roundtrip(client):
    meta = strict_json(client.post("/api/session", data={"demo": "1", "exclude_cfd": "1", "since": "12m"}))
    assert meta["has_cfd"] and meta["exclude_cfd"] and meta["since"] == "12m" and meta["until"] == ""
    assert [c["value"] for c in meta["since_choices"]] == ["", "ytd", "12m", "24m", "36m"]
    sid = meta["sid"]
    out = strict_json(client.put(f"/api/session/{sid}/view", json={"since": "2025-06-01", "until": "2026-03-31", "exclude_cfd": False}))
    assert (out["since"], out["until"], out["exclude_cfd"]) == ("2025-06-01", "2026-03-31", False)
    assert str(out["as_of"]).startswith("2026-03-31") and str(out["start"]).startswith("2025-06-02")
    kept = strict_json(client.put(f"/api/session/{sid}/view", json={"base": "USD"}))            # pozostałe ustawienia zostają
    assert (kept["since"], kept["until"], kept["base"]) == ("2025-06-01", "2026-03-31", "USD")
    back = strict_json(client.put(f"/api/session/{sid}/view", json={"since": "", "until": "", "base": "PLN"}))
    assert back["since"] == "" and back["until"] == "" and str(back["start"]).startswith("2024-11-04")


def test_every_screen_renders_in_a_custom_period_without_cfd(client):
    from server.screens import SCREENS
    sid = strict_json(client.post("/api/session", data={"demo": "1", "exclude_cfd": "1", "since": "2025-06-01",
                                                        "until": "2026-03-31"}))["sid"]
    for name in SCREENS:
        r = client.get(f"/api/session/{sid}/screen/{name}")
        assert r.status_code == 200, f"{name}: {r.get_data(as_text=True)[:300]}"
        check_blocks(strict_json(r)["blocks"], {})
    assert "Podatki liczę zawsze z całej historii" in client.get(f"/api/session/{sid}/screen/tax").get_data(as_text=True)


def test_companies_screen(client):
    sid = strict_json(client.post("/api/session", data={"demo": "1"}))["sid"]
    out = strict_json(client.get(f"/api/session/{sid}/screen/companies"))
    kinds = [b["kind"] for b in out["blocks"]]
    assert kinds[0] == "statement" and kinds.count("chart") >= 3 and "table" in kinds
    assert "rocznie przez 10 lat" in out["blocks"][0]["text"]
    text = client.get(f"/api/session/{sid}/screen/companies").get_data(as_text=True)
    assert "nie rekomendacja" in text and "Ceny docelowe analityków" in text and "kup" not in text.lower().replace("skup", "")
    symbols = [o["value"] for o in out["blocks"][1]["items"][0]["options"]]
    assert symbols and all(not s.endswith("_4") for s in symbols)
    # wyższa stopa dyskonta -> rynek "wymaga" większego wzrostu
    grow = lambda q: next(m["value"] for b in strict_json(client.get(f"/api/session/{sid}/screen/companies?{q}"))["blocks"]
                          if b["kind"] == "metrics" and b["title"] == "Odwrócony DCF" for m in b["items"]
                          if m["label"].startswith("Wzrost FCF"))
    assert grow(f"symbol={symbols[1]}&r=12") > grow(f"symbol={symbols[1]}&r=8")


def test_companies_screen_survives_missing_data(client, monkeypatch):
    from data.providers import DataUnavailable
    sid = strict_json(client.post("/api/session", data={"demo": "1"}))["sid"]

    def boom(self, row):
        raise DataUnavailable("yfinance: brak danych o spółce")
    monkeypatch.setattr(SyntheticMarketData, "fundamentals", boom)
    out = strict_json(client.get(f"/api/session/{sid}/screen/companies?symbol=PKN.PL"))
    assert "Nie udało się pobrać" in out["blocks"][0]["text"] and out["blocks"][1]["kind"] == "controls"
