import numpy as np
import pandas as pd
import pytest

from core.portfolio import CASH_BUCKET, reconstruct
from tests.conftest import make_frames

META = pd.DataFrame({
    "asset_class": ["STOCK", "CFD", "STOCK"], "currency": ["USD", "USD", "PLN"], "multiplier": [1.0, 50.0, 1.0],
    "sector": ["Technologia", "Indeks akcji", "Energia"], "country": ["USA", "USA", "Polska"],
    "name": ["Apple", "CFD S&P 500", "Orlen"]}, index=["AAPL.US", "US500", "PKN.PL"])
DAYS = pd.bdate_range("2025-01-06", "2025-01-10")  # pon-pt


def _fx(usd):
    return pd.DataFrame({"USD": usd, "PLN": 1.0}, index=DAYS)


def test_stock_position_by_hand():
    """Wpłata 10 000 PLN, kupno 10 akcji po 100 USD przy kursie 4,00; cena 110 i kurs 4,10 na koniec."""
    closed, open_, cash = make_frames(
        positions_open=[{"symbol": "AAPL.US", "type": "BUY", "volume": 10, "open_time": "2025-01-07 16:00",
                         "open_price": 100.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 10000.0},
                  {"type": "Stock purchase", "time": "2025-01-07 16:00", "amount": -4000.0, "symbol": "AAPL.US"}])
    prices = pd.DataFrame({"AAPL.US": [99, 100, 105, 108, 110.0]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, _fx([4.0, 4.0, 4.05, 4.08, 4.10]), as_of="2025-01-10")
    assert pf.equity.tolist() == pytest.approx([10000, 10000, 6000 + 10 * 105 * 4.05, 6000 + 10 * 108 * 4.08, 10510])
    assert pf.cash.iloc[-1] == 6000 and pf.flows.sum() == 10000
    assert pf.qty["AAPL.US"].tolist() == [0, 10, 10, 10, 10]
    assert (1 + pf.returns).prod() - 1 == pytest.approx(0.051)
    assert pf.total_pnl() == pytest.approx(510)
    assert pf.pnl["AAPL.US"].sum() == pytest.approx(510)


def test_cfd_value_is_unrealised_pnl_and_exposure_is_nominal():
    """0,1 lota US500 (mnożnik 50) kupione po 5000; cena 5100 przy kursie 4,00 -> wynik 0,1*50*100*4 = 2000 PLN."""
    closed, open_, cash = make_frames(
        positions_open=[{"symbol": "US500", "type": "BUY", "volume": 0.1, "open_time": "2025-01-06 10:00",
                         "open_price": 5000.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 20000.0}])
    prices = pd.DataFrame({"US500": [5000, 5050, 5100, 5100, 5100.0]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, _fx([4.0] * 5), as_of="2025-01-10")
    assert pf.value["US500"].iloc[-1] == pytest.approx(2000)
    assert pf.exposure["US500"].iloc[-1] == pytest.approx(0.1 * 50 * 5100 * 4.0)
    assert pf.equity.iloc[-1] == pytest.approx(22000)
    assert bool(pf.is_cfd["US500"])


def test_short_cfd_closed_and_intraday():
    closed, open_, cash = make_frames(
        positions_closed=[
            {"symbol": "US500", "type": "SELL", "volume": 0.2, "open_time": "2025-01-06 10:00", "open_price": 5000.0,
             "close_time": "2025-01-08 12:00", "close_price": 4900.0, "gross_pl": 4000.0},
            {"symbol": "US500", "type": "BUY", "volume": 1.0, "open_time": "2025-01-09 10:00", "open_price": 4900.0,
             "close_time": "2025-01-09 15:00", "close_price": 4910.0, "gross_pl": 2000.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 20000.0},
                  {"type": "close trade", "time": "2025-01-08 12:00", "amount": 4000.0, "symbol": "US500"},
                  {"type": "swap", "time": "2025-01-07 23:59", "amount": -5.0, "symbol": "US500"},
                  {"type": "close trade", "time": "2025-01-09 15:00", "amount": 2000.0, "symbol": "US500"}])
    prices = pd.DataFrame({"US500": [5000, 4950, 4900, 4910, 4920.0]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, _fx([4.0] * 5), as_of="2025-01-10")
    assert pf.qty["US500"].tolist() == pytest.approx([-0.2, -0.2, 0, 0, 0])   # intraday nie pojawia się w stanie
    assert pf.value["US500"].iloc[1] == pytest.approx(0.2 * 50 * 50 * 4.0)      # krótka zarabia na spadku
    assert pf.equity.iloc[-1] == pytest.approx(20000 + 4000 - 5 + 2000)
    assert pf.pnl["US500"].sum() == pytest.approx(5995)


def test_twr_ignores_deposits():
    """Dopłata w trakcie nie zmienia stopy zwrotu: +10% przed wpłatą i +10% po = 21%."""
    closed, open_, cash = make_frames(
        positions_open=[{"symbol": "PKN.PL", "type": "BUY", "volume": 100, "open_time": "2025-01-06 10:00", "open_price": 100.0},
                        {"symbol": "PKN.PL", "type": "BUY", "volume": 100, "open_time": "2025-01-08 10:00", "open_price": 110.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 10000.0},
                  {"type": "Stock purchase", "time": "2025-01-06 10:00", "amount": -10000.0, "symbol": "PKN.PL"},
                  {"type": "deposit", "time": "2025-01-08 09:00", "amount": 11000.0},
                  {"type": "Stock purchase", "time": "2025-01-08 10:00", "amount": -11000.0, "symbol": "PKN.PL"}])
    prices = pd.DataFrame({"PKN.PL": [100, 110, 110, 121, 121.0]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, _fx([4.0] * 5), as_of="2025-01-10")
    assert (1 + pf.returns).prod() - 1 == pytest.approx(0.21)
    assert pf.equity.iloc[-1] == pytest.approx(24200)


def test_weekend_operation_moves_to_next_business_day():
    closed, open_, cash = make_frames(cash_ops=[
        {"type": "deposit", "time": "2025-01-06 09:00", "amount": 1000.0},
        {"type": "Free-funds Interest", "time": "2025-01-11 10:00", "amount": 3.0}])   # sobota
    pf = reconstruct(closed, open_, cash, META, pd.DataFrame(), pd.DataFrame(), as_of="2025-01-14")
    assert pf.cash.loc["2025-01-10"] == 1000 and pf.cash.loc["2025-01-13"] == 1003
    assert pf.pnl[CASH_BUCKET].sum() == 3.0


def test_missing_price_falls_back_to_open_price_with_warning():
    closed, open_, cash = make_frames(
        positions_open=[{"symbol": "PKN.PL", "type": "BUY", "volume": 10, "open_time": "2025-01-06 10:00", "open_price": 50.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 1000.0},
                  {"type": "Stock purchase", "time": "2025-01-06 10:00", "amount": -500.0, "symbol": "PKN.PL"}])
    pf = reconstruct(closed, open_, cash, META, pd.DataFrame(), _fx([4.0] * 5), as_of="2025-01-10")
    assert pf.equity.iloc[-1] == pytest.approx(1000)
    assert any("Brak notowań" in w for w in pf.warnings)


# ------------------------------------------------------------------ dane przykładowe (end-to-end)

def test_sample_pnl_identity(portfolio):
    """Suma wyników per instrument = zmiana wartości portfela minus wpłaty netto, każdego dnia."""
    pf = portfolio
    d_equity = pf.equity.diff().fillna(pf.equity.iloc[0])
    assert np.abs(pf.pnl.sum(axis=1) - (d_equity - pf.flows)).max() < 1e-6
    assert pf.pnl.sum().sum() == pytest.approx(pf.total_pnl())


def test_sample_reconciles_with_open_positions_sheet(portfolio, export):
    """Wycena otwartych pozycji z odtworzonego portfela zgadza się z kolumnami raportu XTB."""
    pf, op = portfolio, export.open
    stocks = op[op["purchase_value"] > 0]
    assert pf.value.iloc[-1][~pf.is_cfd].sum() == pytest.approx((stocks["purchase_value"] + stocks["gross_pl"]).sum(), abs=1.0)
    cfd = op[op["purchase_value"] == 0]
    assert len(cfd) >= 1
    assert pf.value.iloc[-1][pf.is_cfd].sum() == pytest.approx(cfd["gross_pl"].sum(), abs=1.0)
    assert pf.cash.iloc[-1] == pytest.approx(export.cash["amount"].sum())
    assert not pf.warnings


def test_sample_is_plausible(portfolio):
    pf = portfolio
    assert len(pf.dates) > 480 and pf.equity.min() > 0 and pf.cash.min() > 0
    assert pf.flows.sum() == pytest.approx(122000)
    assert pf.returns.abs().max() < 0.15
