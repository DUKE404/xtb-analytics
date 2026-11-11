import numpy as np
import pandas as pd
import pytest

from core import attribution as attr
from core import performance as perf
from core.portfolio import reconstruct
from tests.conftest import make_frames
from tests.test_portfolio import DAYS, META, _fx


def _series(values, start="2025-01-01"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)), dtype=float)


def test_twr_and_cagr_by_hand():
    r = _series([0.10, -0.05, 0.02])
    assert perf.total_return(r) == pytest.approx(1.10 * 0.95 * 1.02 - 1)
    assert perf.twr_index(r).iloc[-1] == pytest.approx(100 * 1.10 * 0.95 * 1.02)
    flat = _series([0.0004] * 504)                                    # dokładnie 2 lata sesji
    assert perf.cagr(flat) == pytest.approx(1.0004 ** 252 - 1)
    assert perf.total_return(flat) == pytest.approx((1 + perf.cagr(flat)) ** 2 - 1)


def test_volatility_sharpe_sortino_known_values():
    r = _series([0.01, -0.01] * 126)
    sd = np.std([0.01, -0.01] * 126, ddof=1)
    assert perf.volatility(r) == pytest.approx(sd * np.sqrt(252))
    r2 = _series([0.02, -0.01] * 126)                                # średnia 0,5% dziennie
    assert perf.sharpe(r2, rf_annual=0.0) == pytest.approx(0.005 / r2.std(ddof=1) * np.sqrt(252))
    assert perf.sharpe(r2, rf_annual=0.0504) == pytest.approx((0.005 - 0.0002) / r2.std(ddof=1) * np.sqrt(252))
    downside = np.sqrt(np.mean(np.minimum(r2.values, 0) ** 2)) * np.sqrt(252)   # = 0,01*sqrt(0,5)*sqrt(252)
    assert downside == pytest.approx(0.01 * np.sqrt(0.5) * np.sqrt(252))
    assert perf.sortino(r2) == pytest.approx(0.005 * 252 / downside)


def test_drawdown_by_hand():
    # indeks: 1.10 -> 0.88 -> 0.792 -> 1.188 -> 1.188
    r = _series([0.10, -0.20, -0.10, 0.50, 0.0])
    st = perf.drawdown_stats(r)
    assert st["max_drawdown"] == pytest.approx(0.792 / 1.10 - 1)
    assert st["peak_date"] == r.index[0] and st["trough_date"] == r.index[2] and st["recovery_date"] == r.index[3]
    assert st["recovery_days"] == (r.index[3] - r.index[2]).days
    assert st["max_duration_days"] == (r.index[3] - r.index[0]).days
    assert st["current_drawdown"] == pytest.approx(0.0)
    assert perf.calmar(r) == pytest.approx(perf.cagr(r) / 0.28)


def test_drawdown_from_start_and_not_recovered():
    r = _series([-0.10, -0.10, 0.05])
    st = perf.drawdown_stats(r)
    assert st["max_drawdown"] == pytest.approx(0.81 - 1)                # liczone od poziomu startowego
    assert st["recovery_date"] is None and np.isnan(st["recovery_days"]) and st["longest_ongoing"]
    assert perf.drawdown_stats(_series([0.01, 0.02]))["max_drawdown"] == 0.0


def test_xirr_matches_excel_documentation_example():
    dates = ["2008-01-01", "2008-03-01", "2008-10-30", "2009-02-15", "2009-04-01"]
    amounts = [-10000, 2750, 4250, 3250, 2750]
    assert perf.xirr(dates, amounts) == pytest.approx(0.373362535, abs=1e-8)


def test_xirr_simple_and_degenerate():
    assert perf.xirr(["2024-01-01", "2024-12-31"], [-1000, 1100]) == pytest.approx(0.10)   # 365 dni
    assert np.isnan(perf.xirr(["2024-01-01", "2024-06-01"], [-1000, -50]))
    flows = pd.Series([1000.0, 0.0, 0.0], index=pd.to_datetime(["2024-01-01", "2024-07-01", "2024-12-31"]))
    assert perf.portfolio_xirr(flows, 1100.0) == pytest.approx(0.10)


def test_mwr_below_twr_when_money_added_before_a_loss():
    """+10% na 1 000 zł, potem dopłata 10 000 zł i -10%: TWR = -1%, ale pieniądze faktycznie straciły więcej."""
    idx = pd.to_datetime(["2024-01-01", "2024-07-01", "2024-12-31"])
    flows = pd.Series([1000.0, 10000.0, 0.0], index=idx)
    final = (1000 * 1.10 + 10000) * 0.90
    assert perf.portfolio_xirr(flows, final) < -0.01 - 0.05


def test_beta_alpha_tracking_error_ir():
    rng = np.random.default_rng(0)
    b = _series(rng.normal(0.0003, 0.01, 500))
    p = 0.0002 + 1.5 * b
    beta, alpha = perf.beta_alpha(p, b, rf_annual=0.0)
    assert beta == pytest.approx(1.5) and alpha == pytest.approx(0.0002 * 252)
    assert perf.tracking_error(p, b) == pytest.approx(0.5 * b.std(ddof=1) * np.sqrt(252))
    active = p - b
    assert perf.information_ratio(p, b) == pytest.approx(active.mean() / active.std(ddof=1) * np.sqrt(252))
    assert perf.correlation(p, b) == pytest.approx(1.0)
    # z rf: alfa = (stała + (beta-1)*rf_dzienne) * 252
    beta2, alpha2 = perf.beta_alpha(p, b, rf_annual=0.0504)
    assert beta2 == pytest.approx(1.5) and alpha2 == pytest.approx((0.0002 + 0.5 * 0.0002) * 252)


def test_monthly_table():
    idx = pd.bdate_range("2024-12-30", "2025-02-28")
    r = pd.Series(0.0, index=idx)
    r.loc["2025-01-10"] = 0.01
    r.loc["2025-01-20"] = 0.02
    r.loc["2025-02-03"] = -0.03
    tbl = perf.monthly_table(r)
    assert tbl.loc[2025, "sty"] == pytest.approx(1.01 * 1.02 - 1) and tbl.loc[2025, "lut"] == pytest.approx(-0.03)
    assert tbl.loc[2025, "Rok"] == pytest.approx(1.01 * 1.02 * 0.97 - 1) and tbl.loc[2024, "gru"] == 0.0
    assert np.isnan(tbl.loc[2024, "sty"])


def test_levels_to_returns_alignment():
    lv = pd.Series([100, 110, 121.0], index=pd.to_datetime(["2025-01-06", "2025-01-07", "2025-01-09"]))
    r = perf.levels_to_returns(lv, pd.bdate_range("2025-01-06", "2025-01-10"))
    assert r.tolist() == pytest.approx([0, 0.10, 0, 0.10, 0])


def test_summary_on_sample(portfolio, market):
    bench = perf.levels_to_returns(market.benchmarks_base["MSCIW"], portfolio.dates)
    s = perf.summary(portfolio.returns, bench, 0.04, portfolio.flows, portfolio.equity.iloc[-1])
    assert s["total_return"] == pytest.approx((1 + portfolio.returns).prod() - 1)
    assert 0.05 < s["volatility"] < 0.35 and -0.5 < s["max_drawdown"] < 0
    assert 0.2 < s["beta"] < 1.8 and np.isfinite(s["xirr"]) and np.isfinite(s["information_ratio"])
    cmp = perf.comparison_table(portfolio.returns, perf.levels_to_returns(market.benchmarks_base, portfolio.dates))
    assert list(cmp.index)[0] == "Portfel" and {"WIG20", "NDX", "SP500", "MSCIW"} <= set(cmp.index)


# ------------------------------------------------------------------ atrybucja

def test_attribution_price_and_fx_effect_by_hand():
    """10 akcji po 100 USD, kurs 4,00 -> cena 110, kurs 4,10: efekt ceny 400 zł, efekt waluty 110 zł."""
    closed, open_, cash = make_frames(
        positions_open=[{"symbol": "AAPL.US", "type": "BUY", "volume": 10, "open_time": "2025-01-06 16:00", "open_price": 100.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 10000.0},
                  {"type": "Stock purchase", "time": "2025-01-06 16:00", "amount": -4000.0, "symbol": "AAPL.US"},
                  {"type": "DIVIDENT", "time": "2025-01-08 12:00", "amount": 20.0, "symbol": "AAPL.US"},
                  {"type": "Withholding Tax", "time": "2025-01-08 12:00", "amount": -3.0, "symbol": "AAPL.US"}])
    prices = pd.DataFrame({"AAPL.US": [100, 110, 110, 110, 110.0]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, _fx([4.0, 4.0, 4.1, 4.1, 4.1]), as_of="2025-01-10")
    a = attr.decompose(pf, cash)
    row = a.loc["AAPL.US"]
    assert row["price_effect"] == pytest.approx(400) and row["fx_effect"] == pytest.approx(110)
    assert row["income"] == pytest.approx(17) and row["other"] == pytest.approx(0, abs=1e-9)
    assert row["pnl"] == pytest.approx(527)
    assert a["contribution"].sum() == pytest.approx(perf.total_return(pf.returns))
    assert attr.fx_effect_by_currency(a)["USD"] == pytest.approx(110)


def test_attribution_sums_to_twr_and_pnl_on_sample(portfolio, export):
    a = attr.decompose(portfolio, export.cash)
    assert a["contribution"].sum() == pytest.approx(perf.total_return(portfolio.returns), abs=1e-9)
    assert a["pnl"].sum() == pytest.approx(portfolio.total_pnl(), abs=1e-6)
    parts = a[["price_effect", "fx_effect", "income", "other"]].sum(axis=1)
    assert np.allclose(parts, a["pnl"])
    pl_stocks = a[(a["currency"] == "PLN") & (a["asset_class"] != "Gotówka")]
    assert pl_stocks["fx_effect"].abs().max() < 1e-9
    assert attr.by_group(a, "sector")["pnl"].sum() == pytest.approx(a["pnl"].sum())
    assert attr.by_group(a, "country")["contribution"].sum() == pytest.approx(a["contribution"].sum())


def test_attribution_subperiod(portfolio, export):
    a = attr.decompose(portfolio, export.cash, start="2025-06-01", end="2025-12-31")
    sub = portfolio.returns.loc["2025-06-01":"2025-12-31"]
    assert a["contribution"].sum() == pytest.approx(perf.total_return(sub), abs=1e-9)
