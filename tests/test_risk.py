import numpy as np
import pandas as pd
import pytest
from scipy import stats

from core import cfd, factors, risk
from core.portfolio import reconstruct
from tests.conftest import make_frames
from tests.test_portfolio import DAYS, META, _fx


def _normal_returns(n=4000, k=3, seed=1, vols=(0.01, 0.02, 0.015), corr=0.3):
    rng = np.random.default_rng(seed)
    c = np.full((k, k), corr) + np.eye(k) * (1 - corr)
    cov = np.outer(vols, vols) * c
    x = rng.multivariate_normal(np.zeros(k), cov, n)
    return pd.DataFrame(x, index=pd.bdate_range("2010-01-01", periods=n), columns=list("ABC")[:k]), cov


def test_historical_var_is_empirical_quantile():
    rng = np.random.default_rng(0)
    pnl = pd.Series(rng.normal(0, 1000, 5000))
    assert risk.var_historical(pnl, 0.95) == pytest.approx(-np.quantile(pnl, 0.05))
    assert risk.var_historical(pnl, 0.99) == pytest.approx(-np.quantile(pnl, 0.01))
    q = np.quantile(pnl, 0.05)
    assert risk.cvar_historical(pnl, 0.95) == pytest.approx(-pnl[pnl <= q].mean())
    assert risk.cvar_historical(pnl, 0.95) > risk.var_historical(pnl, 0.95)
    # mały przykład policzony ręcznie: 5% najgorszych z 20 obserwacji
    x = pd.Series(np.arange(-10, 10, dtype=float))
    assert risk.var_historical(x, 0.95) == pytest.approx(9.05)


def test_parametric_var_known_values():
    assert risk.var_parametric(1000.0, 0.95) == pytest.approx(1644.854, abs=1e-3)
    assert risk.var_parametric(1000.0, 0.99) == pytest.approx(2326.348, abs=1e-3)
    assert risk.var_parametric(1000.0, 0.95, horizon=10) == pytest.approx(1644.854 * np.sqrt(10), abs=1e-2)
    assert risk.cvar_parametric(1000.0, 0.95) == pytest.approx(2062.713, abs=1e-3)
    assert risk.cvar_parametric(1000.0, 0.99) == pytest.approx(2665.214, abs=1e-3)
    assert risk.var_parametric(1000.0, 0.95, mu_pln=100.0) == pytest.approx(1544.854, abs=1e-3)


def test_three_methods_agree_on_normal_data():
    """Na danych z rozkładu normalnego VaR historyczny, parametryczny i Monte Carlo muszą być zbliżone."""
    r, cov = _normal_returns()
    exposure = pd.Series({"A": 50000.0, "B": 30000.0, "C": -10000.0})
    true_sigma = np.sqrt(exposure.values @ cov @ exposure.values)
    pnl = risk.pnl_series(r, exposure)
    hist = risk.var_historical(pnl, 0.95)
    par = risk.var_parametric(pnl.std(ddof=1), 0.95)
    mc, mc_cvar = risk.var_monte_carlo(r, exposure, 0.95, n_sims=100000, seed=3)
    theory = stats.norm.ppf(0.95) * true_sigma
    assert hist == pytest.approx(theory, rel=0.06)
    assert par == pytest.approx(theory, rel=0.04)
    assert mc == pytest.approx(par, rel=0.03)
    assert mc_cvar == pytest.approx(risk.cvar_parametric(pnl.std(ddof=1), 0.95), rel=0.04)
    # horyzont 10 dni ~ sqrt(10)
    mc10, _ = risk.var_monte_carlo(r, exposure, 0.95, horizon=10, n_sims=100000, seed=3)
    assert mc10 == pytest.approx(par * np.sqrt(10), rel=0.05)
    assert risk.var_historical(pnl, 0.95, horizon=10) == pytest.approx(theory * np.sqrt(10), rel=0.15)


def test_fat_tails_raise_monte_carlo_var_at_99():
    r, _ = _normal_returns(n=2000)
    exposure = pd.Series({"A": 50000.0, "B": 30000.0, "C": 20000.0})
    normal, _ = risk.var_monte_carlo(r, exposure, 0.99, n_sims=200000, seed=5)
    fat, _ = risk.var_monte_carlo(r, exposure, 0.99, n_sims=200000, seed=5, df_t=5)
    assert fat > normal * 1.05
    sims = risk.simulate_pnl(r, exposure, n_sims=200000, seed=5, df_t=5)
    assert sims.std() == pytest.approx(risk.pnl_series(r, exposure).std(), rel=0.03)   # skalowanie zachowuje wariancję
    heavy = pd.Series(np.random.default_rng(2).standard_t(5, 20000))
    assert 4.5 <= risk.fit_t_df(heavy) <= 9
    assert risk.fit_t_df(pd.Series(np.random.default_rng(2).normal(size=20000))) in (None, 30.0)


def test_var_table_shape(portfolio, market):
    r, exp, notes = risk.risk_inputs(portfolio.current_exposure(), market.asset_returns_pln(portfolio.meta))
    tbl = risk.var_table(r, exp, float(portfolio.equity.iloc[-1]), n_sims=5000)
    assert len(tbl) == 12 and set(tbl["Metoda"]) == {"Historyczny", "Parametryczny", "Monte Carlo"}
    assert (tbl["CVaR"] >= tbl["VaR"] - 1e-9).all() and (tbl["VaR"] > 0).all()
    one = tbl[(tbl["Horyzont (dni)"] == 1) & (tbl["Poziom ufności"] == 0.95)].set_index("Metoda")["VaR % kapitału"]
    assert 0.003 < one["Historyczny"] < 0.05
    ten = tbl[(tbl["Horyzont (dni)"] == 10) & (tbl["Poziom ufności"] == 0.95)].set_index("Metoda")["VaR"]
    assert (ten.values > tbl[(tbl["Horyzont (dni)"] == 1) & (tbl["Poziom ufności"] == 0.95)]["VaR"].values).all()
    assert not notes


def test_risk_contributions_two_assets_closed_form():
    cov = pd.DataFrame([[0.04, 0.006], [0.006, 0.09]], index=["A", "B"], columns=["A", "B"]) / 252
    w = pd.Series({"A": 60.0, "B": 40.0})
    rc = risk.risk_contributions(w, cov)
    var = 60 ** 2 * 0.04 + 40 ** 2 * 0.09 + 2 * 60 * 40 * 0.006
    assert rc.loc["A", "risk_share"] == pytest.approx((60 ** 2 * 0.04 + 60 * 40 * 0.006) / var)
    assert rc["risk_share"].sum() == pytest.approx(1.0)
    assert rc["risk_contribution"].sum() == pytest.approx(np.sqrt(var))
    assert risk.portfolio_volatility(w, cov) == pytest.approx(np.sqrt(var))
    assert rc.loc["A", "standalone_vol"] == pytest.approx(0.2)
    assert risk.diversification_ratio(w, cov) == pytest.approx((60 * 0.2 + 40 * 0.3) / np.sqrt(var))


def test_effective_number_of_bets():
    n = 5
    eye = pd.DataFrame(np.eye(n) * 0.0004, index=list("ABCDE"), columns=list("ABCDE"))
    w = pd.Series(1.0, index=list("ABCDE"))
    assert risk.effective_number_of_bets(w, eye) == pytest.approx(5.0)
    ones = pd.DataFrame(np.full((n, n), 0.0004), index=list("ABCDE"), columns=list("ABCDE"))
    assert risk.effective_number_of_bets(w, ones) == pytest.approx(1.0)
    conc = pd.Series([100.0, 1, 1, 1, 1], index=list("ABCDE"))
    assert 1.0 < risk.effective_number_of_bets(conc, eye) < 1.1


def test_correlation_helpers():
    r, _ = _normal_returns(n=600, corr=0.5)
    c = risk.correlation_matrix(r, 90)
    assert c.shape == (3, 3) and np.allclose(np.diag(c), 1.0)
    assert risk.average_correlation(risk.correlation_matrix(r, 600)) == pytest.approx(0.5, abs=0.07)
    roll = risk.rolling_average_correlation(r, 90, step=10)
    assert len(roll) == len(range(90, 601, 10)) and roll.between(-1, 1).all()
    shr = risk.covariance(r, shrink=1.0)
    assert np.allclose(shr.values, np.diag(np.diag(shr.values)))


def test_concentration_and_exposures():
    e = pd.Series({"A": 40.0, "B": 30.0, "C": 20.0, "D": 10.0})
    c = risk.concentration(e)
    assert c["top1"] == pytest.approx(0.4) and c["top5"] == pytest.approx(1.0)
    assert c["hhi"] == pytest.approx(0.16 + 0.09 + 0.04 + 0.01) and c["effective_n"] == pytest.approx(1 / 0.30)
    eq = pd.Series(25.0, index=list("ABCD"))
    assert risk.concentration(eq)["hhi"] == pytest.approx(0.25)
    meta = pd.DataFrame({"sector": ["Tech", "Tech", "Energia", "Indeks"], "currency": ["USD", "USD", "PLN", "USD"]},
                        index=list("ABCD"))
    ex = risk.exposure_by(pd.Series({"A": 40.0, "B": 30.0, "C": 20.0, "D": -10.0}), meta, "sector", equity=100.0,
                          cash=20.0, cash_label="Gotówka")
    assert ex.loc["Tech", "net_pct"] == pytest.approx(0.70) and ex.loc["Indeks", "short_pct"] == pytest.approx(-0.10)
    assert ex.loc["Gotówka", "long"] == 20.0
    cur = risk.currency_exposure(pd.Series({"A": 40.0, "B": 30.0, "C": 20.0}), meta, cash=10.0, equity=100.0)
    assert cur.loc["USD", "share"] == pytest.approx(0.7) and cur.loc["PLN", "value"] == pytest.approx(30.0)


def test_liquidity_days_to_exit():
    vol = pd.DataFrame({"A": [1000.0] * 40, "B": [0.0] * 40})
    t = risk.liquidity_table(pd.Series({"A": 500.0, "B": 10.0, "C": 5.0, "D": 0.0}), vol, participation=0.2)
    assert t.loc["A", "days_to_exit"] == pytest.approx(2.5)            # 500 / (20% x 1000)
    assert np.isnan(t.loc["B", "days_to_exit"]) and np.isnan(t.loc["C", "days_to_exit"]) and "D" not in t.index


# ------------------------------------------------------------------ czynniki

def test_ols_matches_scipy_linregress():
    rng = np.random.default_rng(4)
    x = rng.normal(size=300)
    y = 0.5 + 2.0 * x + rng.normal(scale=0.7, size=300)
    res = factors.ols(y, x, ["x"])
    ref = stats.linregress(x, y)
    assert res["coef"][1] == pytest.approx(ref.slope) and res["coef"][0] == pytest.approx(ref.intercept)
    assert res["se"][1] == pytest.approx(ref.stderr) and res["p"][1] == pytest.approx(ref.pvalue, rel=1e-6)
    assert res["r2"] == pytest.approx(ref.rvalue ** 2)


def test_factor_regression_recovers_known_loadings():
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2022-01-03", periods=750)
    f = pd.DataFrame(rng.normal(0, 0.008, (750, 6)), index=idx, columns=factors.FACTORS)
    f["RF"] = 0.0002
    true = {"Mkt-RF": 1.2, "SMB": 0.4, "HML": -0.6, "RMW": 0.0, "CMA": 0.0, "MOM": 0.3}
    r_usd = f["RF"] + sum(b * f[k] for k, b in true.items()) + 0.0003 + rng.normal(0, 0.002, 750)
    fx = pd.Series(4.0 * np.cumprod(1 + rng.normal(0, 0.005, 750)), index=idx)
    r_pln = (1 + r_usd) * (1 + fx.pct_change().fillna(0)) - 1
    assert np.allclose(factors.to_usd_returns(r_pln, fx), r_usd)
    res = factors.factor_regression(r_pln, fx, f, freq="D")
    for k, b in true.items():
        assert res["table"].at[k, "beta"] == pytest.approx(b, abs=0.05)
    assert res["alpha_annual"] == pytest.approx(0.0003 * 252, abs=0.03) and res["r2"] > 0.9
    assert bool(res["table"].at["HML", "significant"]) and not bool(res["table"].at["CMA", "significant"])
    weekly = factors.factor_regression(r_pln, fx, f, freq="W")
    assert weekly["table"].at["Mkt-RF", "beta"] == pytest.approx(1.2, abs=0.12) and weekly["n"] < 160
    text = " ".join(factors.interpret(res))
    assert "agresywny" in text and "growth" in text and "mniejszych spółek" in text and "momentum" in text
    assert "inwestycje" in text    # CMA trafia do listy "bez wyraźnej ekspozycji"


def test_factor_regression_on_sample(portfolio, market):
    from data.synthetic import SyntheticMarketData
    f = SyntheticMarketData().ff_factors(portfolio.dates[0], portfolio.dates[-1])
    res = factors.factor_regression(portfolio.returns, market.fx["USD"], f)
    assert 0.3 < res["table"].at["Mkt-RF", "beta"] < 1.5 and 0.2 < res["r2"] <= 1.0
    assert len(factors.interpret(res)) >= 3
    with pytest.raises(ValueError):
        factors.factor_regression(portfolio.returns.iloc[:10], market.fx["USD"], f)


# ------------------------------------------------------------------ CFD

def _cfd_portfolio():
    closed, open_, cash = make_frames(
        positions_open=[
            {"symbol": "US500", "type": "BUY", "volume": 0.1, "open_time": "2025-01-06 10:00", "open_price": 5000.0,
             "margin": 5000.0, "swap": -30.0},
            {"symbol": "PKN.PL", "type": "BUY", "volume": 100, "open_time": "2025-01-06 10:00", "open_price": 100.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 30000.0},
                  {"type": "Stock purchase", "time": "2025-01-06 10:00", "amount": -10000.0, "symbol": "PKN.PL"},
                  {"type": "swap", "time": "2025-01-06 23:59", "amount": -10.0, "symbol": "US500"},
                  {"type": "swap", "time": "2025-01-07 23:59", "amount": -10.0, "symbol": "US500"},
                  {"type": "swap", "time": "2025-01-08 23:59", "amount": -10.0, "symbol": "US500"}])
    prices = pd.DataFrame({"US500": [5000.0] * 5, "PKN.PL": [100.0] * 5}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, _fx([4.0] * 5), as_of="2025-01-10")
    return pf, open_, cash


def test_cfd_leverage_and_stop_out_by_hand():
    pf, open_, cash = _cfd_portfolio()
    s = cfd.leverage_summary(pf, open_)
    nominal = 0.1 * 50 * 5000 * 4.0                       # 100 000 PLN
    assert s["cfd_nominal_gross"] == pytest.approx(nominal) and s["stock_exposure"] == pytest.approx(10000)
    assert s["equity"] == pytest.approx(29970)
    assert s["gross_leverage"] == pytest.approx((10000 + nominal) / 29970)
    assert s["collateral"] == pytest.approx(19970) and s["margin_level"] == pytest.approx(19970 / 5000)
    # stop out przy 50% depozytu: można stracić 19 970 - 2 500 = 17 470 PLN = 17,47% nominału
    assert s["loss_to_stop_out"] == pytest.approx(17470) and s["distance_to_stop_out"] == pytest.approx(0.1747)
    t = cfd.open_cfd_table(pf, open_)
    assert len(t) == 1 and t["leverage"].iloc[0] == pytest.approx(20.0) and t["side"].iloc[0] == "długa"


def test_swap_cost_annual_rate_by_hand():
    pf, open_, cash = _cfd_portfolio()
    sw = cfd.swap_costs(pf, cash)
    # nominał 100 000 PLN utrzymywany pon-pt: 4 noce po 1 dniu + piątek liczony jako 1 (koniec danych) = 5 dni
    assert sw["total"] == pytest.approx(-30.0)
    assert sw["nominal_years"] == pytest.approx(100000 * 5 / 365)
    assert sw["annual_rate"] == pytest.approx(30.0 / (100000 * 5 / 365))
    assert sw["current_annual_cost"] == pytest.approx(sw["annual_rate"] * 100000)
    # raport bez operacji swap: bierzemy kolumnę Swap z pozycji
    no_swaps = cash[cash["kind"] != "SWAP"]
    assert cfd.swap_costs(pf, no_swaps, None, open_)["total"] == pytest.approx(-30.0)


def test_cfd_metrics_on_sample(portfolio, export):
    s = cfd.leverage_summary(portfolio, export.open)
    assert s["cfd_nominal_gross"] > 0 and 0.3 < s["gross_leverage"] < 3 and s["margin_required"] > 0
    sw = cfd.swap_costs(portfolio, export.cash, export.closed, export.open)
    assert sw["total"] < 0 and 0.01 < sw["annual_rate"] < 0.12       # generator: 6,5% długie / 1% krótkie
