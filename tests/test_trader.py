import numpy as np
import pandas as pd
import pytest

from core import regimes, trader
from tests.conftest import make_frames

META = pd.DataFrame({
    "asset_class": ["STOCK", "CFD", "STOCK"], "currency": ["USD", "USD", "PLN"], "multiplier": [1.0, 50.0, 1.0],
    "spread_bps": [4.0, 2.0, 20.0]}, index=["AAPL.US", "US500", "PKN.PL"])
FX = pd.DataFrame({"USD": 4.0, "PLN": 1.0}, index=pd.bdate_range("2025-01-01", "2025-03-31"))


def _closed(rows):
    closed, _, _ = make_frames(positions_closed=rows)
    return closed


def _simple_trades():
    return _closed([
        {"symbol": "PKN.PL", "type": "BUY", "volume": 10, "open_time": "2025-01-06 10:00", "open_price": 100.0,
         "close_time": "2025-01-08 10:00", "close_price": 130.0, "purchase_value": 1000.0, "sale_value": 1300.0, "gross_pl": 300.0},
        {"symbol": "PKN.PL", "type": "BUY", "volume": 10, "open_time": "2025-01-07 11:00", "open_price": 100.0,
         "close_time": "2025-01-17 11:00", "close_price": 90.0, "purchase_value": 1000.0, "sale_value": 900.0, "gross_pl": -100.0,
         "commission": -5.0},
        {"symbol": "US500", "type": "SELL", "volume": 0.1, "open_time": "2025-01-13 09:30", "open_price": 6000.0,
         "close_time": "2025-01-13 15:30", "close_price": 5950.0, "gross_pl": 1000.0, "swap": -20.0},
        {"symbol": "US500", "type": "BUY", "volume": 0.1, "open_time": "2025-01-14 09:30", "open_price": 6000.0,
         "close_time": "2025-01-16 09:30", "close_price": 5990.0, "gross_pl": -200.0, "swap": -10.0},
    ])


def test_trades_table_and_summary_by_hand():
    t = trader.trades_table(_simple_trades(), META, FX)
    assert t["net_pl"].tolist() == pytest.approx([300.0, 980.0, -210.0, -105.0])      # posortowane po zamknięciu
    cfd = t[t["symbol"] == "US500"].iloc[0]
    assert cfd["size_pln"] == pytest.approx(0.1 * 50 * 6000 * 4.0)                    # nominał w PLN
    assert cfd["hold_days"] == pytest.approx(0.25)
    s = trader.summary(t)
    assert s["n"] == 4 and s["hit_rate"] == 0.5
    assert s["avg_win"] == pytest.approx(640.0) and s["avg_loss"] == pytest.approx(-157.5)
    assert s["payoff"] == pytest.approx(640 / 157.5) and s["profit_factor"] == pytest.approx(1280 / 315)
    assert s["expectancy"] == pytest.approx(965 / 4) and s["total"] == pytest.approx(965.0)
    assert s["hold_win"] == pytest.approx((2 + 0.25) / 2) and s["hold_loss"] == pytest.approx((10 + 2) / 2)
    assert s["max_losing_streak"] == 2 and s["max_winning_streak"] == 2
    assert s["best"] == 980.0 and s["worst"] == -210.0


def test_weekday_hour_symbol_breakdowns():
    t = trader.trades_table(_simple_trades(), META, FX)
    wd = trader.by_weekday(t)
    assert wd.loc["poniedziałek", "n"] == 2 and wd.loc["poniedziałek", "net"] == pytest.approx(1280.0)
    assert wd.loc["wtorek", "hit_rate"] == 0.0
    hr = trader.by_hour(t)
    assert hr.loc[9, "n"] == 2 and hr.loc[10, "net"] == pytest.approx(300.0)
    sym = trader.by_symbol(t)
    assert sym.loc["US500", "net"] == pytest.approx(770.0) and sym.index[0] == "US500"
    assert trader.summary(t.iloc[0:0])["n"] == 0 and len(trader.by_weekday(t.iloc[0:0])) == 0


def test_size_after_loss_detects_planted_pattern():
    rng = np.random.default_rng(0)
    rows, last_loss, t0 = [], False, pd.Timestamp("2025-01-06 10:00")
    for i in range(80):
        vol = 0.1 * (1.8 if last_loss else 1.0) * rng.uniform(0.9, 1.1)
        pl = float(rng.choice([400.0, -400.0]))
        rows.append({"symbol": "US500", "type": "BUY", "volume": vol, "open_time": t0 + pd.Timedelta(days=i),
                     "open_price": 6000.0, "close_time": t0 + pd.Timedelta(days=i, hours=5), "close_price": 6000.0, "gross_pl": pl})
        last_loss = pl < 0
    res = trader.size_after_loss(trader.trades_table(_closed(rows), META, FX))
    assert res["detected"] and res["ratio"] == pytest.approx(1.8, rel=0.1) and res["p_value"] < 0.001
    flat = [dict(r, volume=0.1) for r in rows]
    assert not trader.size_after_loss(trader.trades_table(_closed(flat), META, FX))["detected"]


def test_disposition_effect_detected_when_losers_held_longer():
    rng = np.random.default_rng(1)
    rows, t0 = [], pd.Timestamp("2025-01-06 10:00")
    for i in range(60):
        win = i % 2 == 0
        hold = rng.uniform(1, 3) if win else rng.uniform(8, 15)
        rows.append({"symbol": "PKN.PL", "type": "BUY", "volume": 10, "open_time": t0 + pd.Timedelta(days=i),
                     "open_price": 100.0, "close_time": t0 + pd.Timedelta(days=i + hold), "close_price": 100.0,
                     "purchase_value": 1000.0, "sale_value": 1100.0 if win else 900.0, "gross_pl": 100.0 if win else -100.0})
    res = trader.disposition_effect(trader.trades_table(_closed(rows), META, FX))
    assert res["detected"] and res["ratio"] > 3 and res["hold_win"] < 3 < res["hold_loss"]


def test_early_exit_measures_move_after_close():
    idx = pd.bdate_range("2025-01-01", periods=80)
    prices = pd.DataFrame({"PKN.PL": np.linspace(100, 180, 80), "US500": np.linspace(6000, 5000, 80)}, index=idx)
    rows = []
    for i in range(8):                                   # zyskowne długie na rosnącym PKN i krótkie na spadającym US500
        d = idx[5 + 3 * i]
        rows.append({"symbol": "PKN.PL", "type": "BUY", "volume": 10, "open_time": d, "open_price": 100.0,
                     "close_time": d + pd.Timedelta(days=1), "close_price": 110.0, "purchase_value": 1000.0,
                     "sale_value": 1100.0, "gross_pl": 100.0})
        rows.append({"symbol": "US500", "type": "SELL", "volume": 0.1, "open_time": d, "open_price": 6000.0,
                     "close_time": d + pd.Timedelta(days=1), "close_price": 5990.0, "gross_pl": 200.0})
    res = trader.early_exit(trader.trades_table(_closed(rows), META, FX), prices, lookahead=20)
    assert res["n"] == 16 and res["share_continued"] == 1.0 and res["avg_move"] > 0.03 and res["detected"]
    assert res["left_on_table"] > 0
    assert trader.early_exit(trader.trades_table(_closed(rows[:2]), META, FX), prices)["detected"] is False


def test_position_sizing_formulas():
    assert trader.kelly_fraction(0.6, 1.0) == pytest.approx(0.2)                 # 0,6 - 0,4/1
    assert trader.kelly_fraction(0.5, 2.0) == pytest.approx(0.25)
    assert trader.half_kelly(0.5, 2.0) == pytest.approx(0.125)
    assert trader.half_kelly(0.4, 1.0) == 0.0                                     # ujemna przewaga -> nie grać
    assert np.isnan(trader.kelly_fraction(1.2, 1.0))
    # 100 000 zł, ryzyko 1%, zmienność dzienna 2%, stop 2 sigma, 1 dzień -> 1000 / 0,04 = 25 000 zł
    assert trader.volatility_position_size(100000, 0.01, 0.02, 2.0, 1.0) == pytest.approx(25000.0)
    assert trader.volatility_position_size(100000, 0.01, 0.02, 2.0, 4.0) == pytest.approx(12500.0)


def test_cost_breakdown_by_hand():
    closed = _simple_trades()
    t = trader.trades_table(closed, META, FX)
    _, open_, cash = make_frames(
        positions_open=[{"symbol": "AAPL.US", "type": "BUY", "volume": 5, "open_time": "2025-01-20 16:00",
                         "open_price": 200.0, "purchase_value": 4020.0}],
        cash_ops=[{"type": "commission", "time": "2025-01-17 11:00", "amount": -5.0, "symbol": "PKN.PL"},
                  {"type": "swap", "time": "2025-01-13 23:59", "amount": -30.0, "symbol": "US500"},
                  {"type": "Withholding Tax", "time": "2025-02-01 12:00", "amount": -7.0, "symbol": "AAPL.US"}])
    c = trader.cost_breakdown(t, open_, cash, META, FX)
    pkn_legs = 1000 + 1300 + 1000 + 900
    cfd_legs = 0.1 * 50 * 4.0 * (6000 + 5950 + 6000 + 5990)
    spread = pkn_legs * 20 / 1e4 / 2 + cfd_legs * 2 / 1e4 / 2 + 4020 * 4 / 1e4 / 2
    assert c["items"]["Prowizje"] == pytest.approx(5.0) and c["items"]["Swapy"] == pytest.approx(30.0)
    assert c["items"]["Spready (szacunek)"] == pytest.approx(spread)
    assert c["items"]["Przewalutowania (szacunek)"] == pytest.approx(4020 * 0.005)   # tylko akcje w walucie obcej
    assert c["total"] == pytest.approx(35 + spread + 20.1) and c["taxes_withheld"] == pytest.approx(7.0)
    assert c["turnover"] == pytest.approx(pkn_legs + cfd_legs + 4020)
    # raport bez operacji kosztowych: kolumny Commission / Swap z pozycji
    c2 = trader.cost_breakdown(t, open_, cash.iloc[0:0], META, FX)
    assert c2["items"]["Prowizje"] == pytest.approx(5.0) and c2["items"]["Swapy"] == pytest.approx(30.0)
    ratios = trader.cost_ratios(100.0, 900.0, 10000.0, 2.0)
    assert ratios["share_of_gross"] == pytest.approx(0.10) and ratios["per_year_of_equity"] == pytest.approx(0.005)


def test_sample_patterns_are_found(export, mapping, market):
    """W dane przykładowe celowo wszyto dwa wzorce - analityka ma je znaleźć."""
    meta = mapping.set_index("symbol")
    t = trader.trades_table(export.closed, meta, market.fx)
    assert len(t) == len(export.closed) and t["net_pl"].sum() == pytest.approx(
        export.closed[["gross_pl", "commission", "swap", "rollover"]].sum().sum())
    cfd = t[t["asset_class"] == "CFD"]
    assert trader.size_after_loss(cfd)["detected"]
    multi_day = cfd[cfd["hold_days"] >= 1]
    disp = trader.disposition_effect(multi_day)
    assert disp["detected"] and disp["ratio"] > 1.5
    s = trader.summary(t)
    assert 0.2 < s["hit_rate"] < 0.9 and s["n"] > 60


# ------------------------------------------------------------------ reżimy

def _two_regime_series(seed=3, n=1500):
    rng = np.random.default_rng(seed)
    state = np.zeros(n, dtype=int)
    for i in range(1, n):
        p = 0.02 if state[i - 1] == 0 else 0.05
        state[i] = 1 - state[i - 1] if rng.random() < p else state[i - 1]
    x = np.where(state == 0, rng.normal(0.0006, 0.006, n), rng.normal(-0.001, 0.022, n))
    return pd.Series(x, index=pd.bdate_range("2019-01-01", periods=n)), state


def test_hmm_recovers_planted_regimes():
    x, state = _two_regime_series()
    res = regimes.gaussian_hmm(x.values, 2)
    assert (res["states"] == state).mean() > 0.9
    assert res["stds"][0] == pytest.approx(0.006, rel=0.15) and res["stds"][1] == pytest.approx(0.022, rel=0.15)
    assert res["transition"].sum(axis=1) == pytest.approx([1.0, 1.0])
    assert res["transition"][0, 0] > 0.95 and res["transition"][1, 1] > 0.9
    assert np.allclose(res["posterior"].sum(axis=1), 1.0)
    with pytest.raises(ValueError):
        regimes.gaussian_hmm(x.values[:20], 2)


def test_three_states_sorted_by_volatility():
    x, _ = _two_regime_series(n=2000)
    res = regimes.gaussian_hmm(x.values, 3)
    assert list(res["stds"]) == sorted(res["stds"]) and set(np.unique(res["states"])) <= {0, 1, 2}


def test_volatility_threshold_regimes_and_stats():
    x, state = _two_regime_series()
    st = regimes.volatility_regimes(x, 2)
    assert (st.values == state).mean() > 0.75
    stats = regimes.regime_stats(x, regimes.hmm_regimes(x, 2), 2)
    assert list(stats.index) == ["spokojny", "nerwowy"] and stats["share"].sum() == pytest.approx(1.0)
    assert stats.at["nerwowy", "volatility"] > 2.5 * stats.at["spokojny", "volatility"]
    assert stats.at["spokojny", "ann_return"] > stats.at["nerwowy", "ann_return"]
    assert stats["days"].sum() == len(x)


def test_spans():
    s = pd.Series([0, 1, 1, 0, 0, 1])
    assert regimes.spans(s, 1) == [(1, 2), (5, 5)] and regimes.spans(s, 0) == [(0, 0), (3, 4)]
