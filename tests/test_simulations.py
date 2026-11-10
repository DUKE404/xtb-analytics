import numpy as np
import pandas as pd
import pytest

from core import montecarlo as mc
from core import optimize as opt
from core import risk, stress, whatif

META = pd.DataFrame({
    "asset_class": ["STOCK", "STOCK", "ETF", "CFD", "CFD", "STOCK", "ETF"],
    "currency": ["USD", "PLN", "EUR", "USD", "USD", "USD", "PLN"],
    "sector": ["Technologia", "Energia", "ETF akcji globalnych", "Indeks akcji", "Surowce", "Dobra podstawowe", "ETF akcji polskich"],
    "country": ["USA", "Polska", "Świat", "USA", "Świat", "USA", "Polska"],
}, index=["AAPL.US", "PKN.PL", "EUNL.DE", "US500", "GOLD", "KO.US", "ETFBW20TR.PL"])


# ------------------------------------------------------------------ stress

def test_factor_mapping():
    fm = stress.factor_map(META, META.index)
    assert fm["eq_factor"].to_dict() == {"AAPL.US": "NDX", "PKN.PL": "WIG20", "EUNL.DE": "WORLD", "US500": "SP500",
                                         "GOLD": "GOLD", "KO.US": "SP500", "ETFBW20TR.PL": "WIG20"}
    assert fm.at["AAPL.US", "fx_factor"] == "USDPLN" and fm.at["EUNL.DE", "fx_factor"] == "EURPLN"
    assert fm.at["PKN.PL", "fx_factor"] == "" and fm.at["US500", "fx_factor"] == ""    # CFD: bez ryzyka walutowego nominału


def test_scenario_by_hand():
    """Akcja USA za 10 000 zł: S&P -20%, USD/PLN +10% -> 10 000 x (0,8 x 1,1 - 1) = -1 200 zł."""
    exposure = pd.Series({"KO.US": 10000.0, "PKN.PL": 5000.0, "US500": -50000.0})
    shocks = {"SP500": -0.20, "USDPLN": 0.10, "WIG20": -0.30}
    out = stress.apply_scenario(exposure, META, shocks)
    assert out.at["KO.US", "pnl"] == pytest.approx(-1200.0)
    assert out.at["PKN.PL", "pnl"] == pytest.approx(-1500.0)
    assert out.at["US500", "pnl"] == pytest.approx(10000.0)                      # krótki CFD zarabia na spadku
    betas = pd.Series({"KO.US": 0.5, "PKN.PL": 1.5})
    scaled = stress.apply_scenario(exposure, META, shocks, betas)
    assert scaled.at["KO.US", "pnl"] == pytest.approx(10000 * (0.9 * 1.1 - 1))
    assert scaled.at["PKN.PL", "pnl"] == pytest.approx(-2250.0)
    table = stress.run_scenarios(exposure, META, 100000.0, {"mój": shocks})
    assert table.at["mój", "pnl"] == pytest.approx(7300.0) and table.at["mój", "pct"] == pytest.approx(0.073)


def test_loss_cannot_exceed_position_value():
    out = stress.apply_scenario(pd.Series({"PKN.PL": 1000.0}), META, {"WIG20": -0.66}, pd.Series({"PKN.PL": 2.5}))
    assert out.at["PKN.PL", "pnl"] == pytest.approx(-1000.0)


def test_historical_scenarios_are_complete():
    for name, sc in stress.HISTORICAL.items():
        assert set(sc["shocks"]) == set(stress.FACTORS), name
        assert sc["shocks"]["SP500"] < 0 and sc["shocks"]["USDPLN"] > 0 and sc["period"]


def test_estimate_betas():
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2024-01-01", periods=400)
    proxy = pd.DataFrame({"SP500": rng.normal(0, 0.01, 400), "NDX": rng.normal(0, 0.012, 400)}, index=idx)
    assets = pd.DataFrame({"KO.US": 0.6 * proxy["SP500"] + rng.normal(0, 0.002, 400),
                           "AAPL.US": 5.0 * proxy["NDX"]}, index=idx)
    fm = stress.factor_map(META, ["KO.US", "AAPL.US", "PKN.PL"])
    b = stress.estimate_betas(assets, proxy, fm)
    assert b["KO.US"] == pytest.approx(0.6, abs=0.05)
    assert b["AAPL.US"] == 2.5          # przycięte do górnego limitu
    assert b["PKN.PL"] == 1.0           # brak danych -> 1


def test_stress_on_sample(portfolio):
    exp = portfolio.current_exposure()
    equity = float(portfolio.equity.iloc[-1])
    t = stress.run_scenarios(exp, portfolio.meta, equity, {k: v["shocks"] for k, v in stress.HISTORICAL.items()})
    assert (t["pnl"] < 0).all() and (t["pct"] > -0.9).all()
    assert t.at["Kryzys finansowy 2008", "pnl"] < t.at["COVID, marzec 2020", "pnl"]


# ------------------------------------------------------------------ Monte Carlo

def test_constant_returns_give_exact_compound_growth():
    r = np.full(300, 0.0005)
    sim = mc.simulate(r, 10000.0, years=2, n_paths=50, method="bootstrap")
    days = 24 * mc.DAYS_PER_MONTH
    assert np.allclose(sim["values"][:, -1], 10000 * 1.0005 ** days)
    assert np.allclose(sim["max_drawdown"], 0.0) and mc.prob_drawdown(sim, 0.01) == 0.0
    assert mc.prob_target(sim, 12000, 2) == 1.0 and mc.prob_target(sim, 13000, 2) == 0.0


def test_contributions_by_hand():
    """Zerowe zwroty: wartość = start + dopłaty; pierwsza dopłata na początku 2. miesiąca."""
    sim = mc.simulate(np.zeros(100), 1000.0, years=1, n_paths=5, monthly_contribution=100.0)
    assert sim["values"][0].tolist() == pytest.approx([1000, 1000] + [1000 + 100 * k for k in range(1, 12)])
    assert sim["contributed"].tolist() == pytest.approx(sim["values"][0].tolist())
    assert mc.prob_loss(sim, 1) == 0.0


def test_block_bootstrap_uses_only_history_in_blocks():
    r = np.arange(100, dtype=float)
    paths = mc.block_bootstrap(r, horizon=60, n_paths=20, block=20, seed=3)
    assert paths.shape == (20, 60) and set(np.unique(paths)) <= set(r)
    diffs = np.diff(paths[:, :20], axis=1) % 100            # wewnątrz bloku kolejne dni historii
    assert np.all(diffs == 1)


def test_student_t_paths_moments_and_fat_tails():
    p = mc.student_t_paths(0.0004, 0.01, 5, horizon=2000, n_paths=400, seed=1)
    assert p.mean() == pytest.approx(0.0004, abs=0.0001) and p.std() == pytest.approx(0.01, rel=0.03)
    normal_tail = (np.abs(np.random.default_rng(1).normal(0, 0.01, p.size)) > 0.04).mean()
    assert (np.abs(p - 0.0004) > 0.04).mean() > 5 * normal_tail
    assert 4.5 <= mc.fit_df(np.random.default_rng(2).standard_t(5, 50000)) <= 8


def test_return_override_sets_median_growth():
    rng = np.random.default_rng(5)
    hist = rng.normal(0.002, 0.01, 500)                      # historia z nierealnie wysoką średnią
    sim = mc.simulate(hist, 100000.0, years=10, n_paths=4000, method="t", annual_return=0.06, seed=9)
    median_cagr = (np.median(sim["values"][:, -1]) / 100000) ** (1 / 10) - 1
    assert median_cagr == pytest.approx(0.06, abs=0.012)
    boot = mc.simulate(hist, 100000.0, years=5, n_paths=3000, method="bootstrap", annual_return=0.06, seed=9)
    assert (np.median(boot["values"][:, -1]) / 100000) ** (1 / 5) - 1 == pytest.approx(0.06, abs=0.02)


def test_percentiles_ordered_and_probabilities_monotone(portfolio, market):
    r, exp, _ = risk.risk_inputs(portfolio.current_exposure(), market.asset_returns_pln(portfolio.meta))
    port = risk.pnl_series(r, exp) / float(portfolio.equity.iloc[-1])
    sim = mc.simulate(port, 150000.0, years=10, n_paths=1500, annual_return=0.06)
    pct = mc.percentiles(sim)
    assert (pct["p5"] <= pct["p25"]).all() and (pct["p25"] <= pct["p50"]).all() and (pct["p75"] <= pct["p95"]).all()
    assert pct.shape == (121, 5) and pct["p50"].iloc[0] == 150000
    assert mc.prob_target(sim, 200000, 10) >= mc.prob_target(sim, 300000, 10)
    assert mc.prob_drawdown(sim, 0.10) >= mc.prob_drawdown(sim, 0.30)
    assert mc.prob_drawdown(sim, 0.20, years=1) <= mc.prob_drawdown(sim, 0.20, years=10)
    t = mc.horizon_table(sim)
    assert t["horizon"].tolist() == [1, 5, 10] and (t["p95"].diff().dropna() > 0).all()


# ------------------------------------------------------------------ co jeśli

def _whatif_setup():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2024-01-01", periods=500)
    mkt = rng.normal(0.0003, 0.01, 500)
    r = pd.DataFrame({"A": mkt + rng.normal(0, 0.004, 500), "B": mkt + rng.normal(0, 0.004, 500),
                      "C": rng.normal(0.0002, 0.012, 500)}, index=idx)
    return r, pd.Series(mkt, index=idx), pd.Series({"A": 60000.0, "B": 30000.0})


def test_whatif_matches_direct_recomputation():
    r, bench, exp = _whatif_setup()
    out = whatif.what_if(exp, r, equity=100000.0, cash=10000.0, symbol="C", amount=8000.0, bench=bench)
    new = pd.Series({"A": 60000.0, "B": 30000.0, "C": 8000.0})
    assert out["after"]["var95"] == pytest.approx(risk.var_historical(risk.pnl_series(r, new), 0.95))
    assert out["before"]["var95"] == pytest.approx(risk.var_historical(risk.pnl_series(r, exp), 0.95))
    assert out["after"]["cash"] == 2000.0 and out["after"]["positions"] == 3
    assert out["after"]["hhi"] < out["before"]["hhi"]                 # trzecia pozycja zmniejsza koncentrację
    assert abs(out["correlation_with_rest"]) < 0.15                    # C jest niezależne od reszty
    assert out["before"]["beta"] == pytest.approx(0.9, abs=0.05)       # 90% kapitału z betą ~1


def test_whatif_sell_and_cfd():
    r, bench, exp = _whatif_setup()
    sell = whatif.what_if(exp, r, 100000.0, 10000.0, "A", -60000.0, bench=bench)
    assert sell["position_after"] == 0.0 and sell["after"]["positions"] == 1 and sell["after"]["cash"] == 70000.0
    assert sell["after"]["var95"] < sell["before"]["var95"] and sell["after"]["beta"] == pytest.approx(0.3, abs=0.05)
    hedge = whatif.what_if(exp, r, 100000.0, 10000.0, "A", -30000.0, is_cfd=True, bench=bench)
    assert hedge["after"]["cash"] == 10000.0                           # CFD nie zużywa gotówki (poza depozytem)
    with pytest.raises(ValueError):
        whatif.what_if(exp, r, 100000.0, 10000.0, "NIE_MA", 1000.0)


# ------------------------------------------------------------------ optymalizacja

def _cov(vols, corr=0.0):
    n = len(vols)
    c = np.full((n, n), corr) + np.eye(n) * (1 - corr)
    names = [f"A{i}" for i in range(n)]
    return pd.DataFrame(np.outer(vols, vols) * c, index=names, columns=names)


def test_min_variance_closed_form():
    cov = _cov([0.10, 0.20])
    assert opt.min_variance(cov).values == pytest.approx([0.8, 0.2], abs=1e-5)     # w1 = s2^2 / (s1^2 + s2^2)
    rho, s1, s2 = 0.5, 0.10, 0.20
    w1 = (s2 ** 2 - rho * s1 * s2) / (s1 ** 2 + s2 ** 2 - 2 * rho * s1 * s2)
    assert opt.min_variance(_cov([s1, s2], rho))["A0"] == pytest.approx(min(w1, 1.0), abs=1e-5)
    capped = opt.min_variance(cov, max_weight=0.6)
    assert capped.values == pytest.approx([0.6, 0.4], abs=1e-6)


def test_max_sharpe_uncorrelated_equal_vol_is_proportional_to_excess_return():
    cov = _cov([0.2, 0.2, 0.2])
    mu = pd.Series([0.08, 0.06, 0.04], index=cov.index)
    w = opt.max_sharpe(mu, cov, rf=0.02)
    assert w.values == pytest.approx(np.array([6, 4, 2]) / 12, abs=1e-4)
    none_good = opt.max_sharpe(pd.Series([0.01, 0.0, -0.01], index=cov.index), cov, rf=0.02)
    assert none_good.values == pytest.approx(opt.min_variance(cov).values, abs=1e-6)


def test_risk_parity_equalises_risk_contributions():
    cov = _cov([0.10, 0.20, 0.40])
    w = opt.risk_parity(cov)
    assert w.values == pytest.approx(np.array([4, 2, 1]) / 7, abs=1e-4)          # bez korelacji: w ~ 1/sigma
    rng = np.random.default_rng(0)
    a = rng.normal(size=(6, 6))
    general = pd.DataFrame(a @ a.T / 6 + np.eye(6) * 0.05, index=list("ABCDEF"), columns=list("ABCDEF"))
    w = opt.risk_parity(general)
    rc = w.values * (general.values @ w.values)
    assert rc / rc.sum() == pytest.approx(np.full(6, 1 / 6), abs=2e-4)


def test_hrp_properties():
    cov = _cov([0.10, 0.20, 0.40, 0.25])
    w = opt.hrp(cov)
    iv = 1 / np.diag(cov.values)
    assert w.sum() == pytest.approx(1.0) and (w > 0).all()
    assert w.sort_values(ascending=False).index.tolist() == pd.Series(iv, index=cov.index).sort_values(ascending=False).index.tolist()
    two = opt.hrp(_cov([0.10, 0.20]))
    assert two.values == pytest.approx([0.8, 0.2])                               # dwa aktywa: odwrotność wariancji
    assert opt.hrp(cov, max_weight=0.4).max() <= 0.4 + 1e-9


def test_compare_on_sample(portfolio, market):
    exp = portfolio.current_exposure()
    held = exp[~portfolio.is_cfd.reindex(exp.index)]
    res = opt.compare(market.asset_returns_pln(portfolio.meta).tail(504), held, rf=0.04, max_weight=0.35)
    w, st = res["weights"], res["stats"]
    assert list(w.columns) == list(opt.METHODS)
    assert np.allclose(w.sum(), 1.0) and (w.values >= -1e-9).all() and (w.drop(columns="current").max() <= 0.35 + 1e-6).all()
    assert st.at["min_variance", "volatility"] <= st["volatility"].min() + 1e-9
    assert st.at["max_sharpe", "sharpe"] >= st.drop(index="max_sharpe")["sharpe"].max() - 1e-6
    assert st.at["current", "turnover"] == 0.0 and (st["turnover"].drop("current") > 0).all()
    assert st.at["risk_parity", "max_risk_share"] <= st.at["current", "max_risk_share"]
