"""Wycena odporna na złe dane: skala notowań inna niż ceny transakcji, CFD z tym samym tickerem co akcja,
mnożniki CFD czytane z raportu, ujemna wartość portfela."""
import numpy as np
import pandas as pd
import pytest

from core.portfolio import reconstruct
from data.mapping import build_mapping
from data.synthetic import SyntheticMarketData
from data.xtb_parser import category_class, cfd_symbol, parse_accounts
from server.context import build_context
from tests import real_layout as R
from tests.conftest import make_frames

META = pd.DataFrame({
    "asset_class": ["STOCK", "CFD", "STOCK"], "currency": ["PLN", "PLN", "PLN"], "multiplier": [1.0, 50.0, 1.0],
    "sector": ["Technologia", "Indeks akcji", "Energia"], "country": ["USA", "USA", "Polska"],
    "name": ["Nvidia", "CFD S&P 500", "Orlen"]}, index=["NVDA.US", "US500", "PKN.PL"])
DAYS = pd.bdate_range("2025-01-06", "2025-01-10")
FX = pd.DataFrame({"PLN": 1.0}, index=DAYS)


def test_split_adjusted_quotes_are_rescaled_to_trade_prices():
    """Akcja kupiona po 900 i sprzedana po 1000 przed splitem 10:1; źródło podaje ceny po splicie (90 -> 100).
    Bez korekty portfel 'traciłby' 90% wartości pozycji na czas jej trzymania."""
    closed, open_, cash = make_frames(
        positions_closed=[{"symbol": "NVDA.US", "type": "BUY", "volume": 1, "open_time": "2025-01-06 10:00",
                           "open_price": 900.0, "close_time": "2025-01-10 10:00", "close_price": 1000.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 1000.0},
                  {"type": "Stock purchase", "time": "2025-01-06 10:00", "amount": -900.0, "symbol": "NVDA.US"},
                  {"type": "Stock sell", "time": "2025-01-10 10:00", "amount": 1000.0, "symbol": "NVDA.US"}])
    prices = pd.DataFrame({"NVDA.US": [90.0, 92.0, 95.0, 97.0, 100.0]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, FX, as_of="2025-01-10")
    assert pf.equity.tolist() == pytest.approx([1000.0, 1020.0, 1050.0, 1070.0, 1100.0])
    assert pf.prices["NVDA.US"].iloc[:4].tolist() == pytest.approx([900.0, 920.0, 950.0, 970.0])     # cena efektywna
    assert any("innej skali" in w and "NVDA.US" in w for w in pf.warnings)
    assert (1 + pf.returns).prod() - 1 == pytest.approx(0.10)


def test_quotes_close_to_trade_prices_are_left_alone():
    closed, open_, cash = make_frames(
        positions_open=[{"symbol": "PKN.PL", "type": "BUY", "volume": 10, "open_time": "2025-01-06 10:00", "open_price": 58.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 1000.0},
                  {"type": "Stock purchase", "time": "2025-01-06 10:00", "amount": -580.0, "symbol": "PKN.PL"}])
    prices = pd.DataFrame({"PKN.PL": [60.0, 61.0, 62.0, 63.0, 64.0]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, FX, as_of="2025-01-10")
    assert pf.value["PKN.PL"].tolist() == pytest.approx([600.0, 610.0, 620.0, 630.0, 640.0])       # zysk z dnia zakupu zostaje
    assert not any("innej skali" in w for w in pf.warnings)


def test_open_position_with_wrong_level_ends_at_report_price():
    closed, open_, cash = make_frames(
        positions_open=[{"symbol": "PKN.PL", "type": "BUY", "volume": 2, "open_time": "2025-01-06 10:00",
                         "open_price": 500.0, "market_price": 600.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 1000.0},
                  {"type": "Stock purchase", "time": "2025-01-06 10:00", "amount": -1000.0, "symbol": "PKN.PL"}])
    prices = pd.DataFrame({"PKN.PL": [5.0, 5.2, 5.1, 5.5, 5.4]}, index=DAYS)             # np. inna waluta / zły ticker
    pf = reconstruct(closed, open_, cash, META, prices, FX, as_of="2025-01-10")
    assert pf.value["PKN.PL"].iloc[0] == pytest.approx(1000.0) and pf.value["PKN.PL"].iloc[-1] == pytest.approx(1200.0)
    assert pf.equity.min() >= 1000.0 - 1e-9


def test_cfd_with_mismatched_contract_level_has_no_phantom_loss():
    """CFD otwarty po 5000, a źródło notowań pokazuje poziom 4000 (inny kontrakt). Liczy się ruch, nie poziom."""
    closed, open_, cash = make_frames(
        positions_closed=[{"symbol": "US500", "type": "BUY", "volume": 0.1, "open_time": "2025-01-06 10:00",
                           "open_price": 5000.0, "close_time": "2025-01-10 10:00", "close_price": 5100.0, "gross_pl": 500.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 2000.0},
                  {"type": "close trade", "time": "2025-01-10 10:00", "amount": 500.0, "symbol": "US500"}])
    prices = pd.DataFrame({"US500": [4000.0, 4020.0, 4040.0, 4060.0, 4080.0]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, FX, as_of="2025-01-10")
    assert pf.value["US500"].iloc[0] == pytest.approx(0.0)                    # bez korekty: 0,1 x 50 x (4000-5000) = -5000
    assert pf.value["US500"].iloc[:4].min() >= 0.0 and pf.value["US500"].iloc[3] == pytest.approx(0.1 * 50 * 75, rel=0.05)
    assert pf.equity.tolist()[-1] == pytest.approx(2500.0) and pf.equity.min() >= 2000.0 - 1e-9


def test_negative_equity_days_are_reported_and_skipped_in_returns():
    """Krótka pozycja na 'akcji' bez pokrycia w gotówce daje ujemną wartość portfela - to błąd danych, nie -100%."""
    closed, open_, cash = make_frames(
        positions_closed=[{"symbol": "PKN.PL", "type": "SELL", "volume": 100, "open_time": "2025-01-07 10:00",
                           "open_price": 60.0, "close_time": "2025-01-09 10:00", "close_price": 60.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 1000.0},
                  {"type": "deposit", "time": "2025-01-10 09:00", "amount": 100.0}])
    prices = pd.DataFrame({"PKN.PL": [60.0] * 5}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, FX, as_of="2025-01-10")
    assert pf.equity.tolist() == pytest.approx([1000.0, -5000.0, -5000.0, 1000.0, 1100.0])
    assert pf.returns.tolist() == pytest.approx([0.0] * 5)                       # złe dni i dzień powrotu pominięte
    w = [x for x in pf.warnings if "ujemna" in x]
    assert w and "PKN.PL" in w[0] and "2025-01-07" in w[0] and "ilość -100" in w[0]


# --------------------------------------------------------------------------- CFD w prawdziwym układzie raportu

def test_category_decides_what_is_a_cfd():
    assert [category_class(c) for c in ("STOCK", "ETF", "CFD", "Stock CFD", "CRYPTO", "INDICES", "", None)] == \
        ["STOCK", "ETF", "CFD", "CFD", "CFD", "CFD", "", ""]
    assert cfd_symbol("NVDA.US") == "NVDA.US_4" and cfd_symbol("US100") == "US100"


def test_cfd_rows_get_own_symbol_and_terms_from_the_report():
    exp = parse_accounts([R.cfd_account()])[0]
    assert exp.closed["symbol"].tolist() == ["NVDA.US_4", "US100", "BITCOIN", "NVDA.US"]
    assert exp.cash["symbol"].tolist() == ["", "NVDA.US_4", "US100", "BITCOIN", "NVDA.US", "NVDA.US"]
    assert exp.instruments["US100"]["multiplier"] == 20.0 and exp.instruments["US100"]["margin_rate"] == 0.05
    assert exp.instruments["NVDA.US_4"] == {"name": "Nvidia", "category": "CFD", "multiplier": 1.0, "margin_rate": 0.2}
    m = build_mapping(exp.symbols, hints=exp.instruments).set_index("symbol")
    assert m.loc[["NVDA.US_4", "US100", "BITCOIN"], "asset_class"].tolist() == ["CFD"] * 3
    assert m.at["NVDA.US", "asset_class"] == "STOCK" and m.at["NVDA.US_4", "yf_ticker"] == m.at["NVDA.US", "yf_ticker"]
    assert m.at["BITCOIN", "margin_rate"] == 0.5


def test_report_multiplier_overrides_builtin_table():
    m = build_mapping(["US100"], hints={"US100": {"category": "CFD", "multiplier": 1.0}}).set_index("symbol")
    assert m.at["US100", "multiplier"] == 1.0 and m.at["US100", "yf_ticker"] == "^NDX"


def test_account_with_short_stock_cfd_never_goes_negative():
    ctx = build_context([R.cfd_account()], provider=SyntheticMarketData())
    pf = ctx.pf
    assert not any("ujemna" in w for w in pf.warnings)
    assert pf.equity.iloc[-1] == pytest.approx(5000.0 - 2000.0 + 1600.0 + 1200.0 + 400.0)
    assert (1 + pf.returns).prod() - 1 == pytest.approx(0.24)
    stock_days = pf.qty["NVDA.US"] != 0
    assert pf.value.loc[stock_days, "NVDA.US"].min() > 0.8 * 3600           # akcja sprzed splitu trzyma wartość zakupu
    assert np.isfinite(pf.returns).all()


# --------------------------------------------------------------------------- wycena "na dziś" (po dacie raportu)

def _open_stock(market_price=110.0):
    return make_frames(
        positions_open=[{"symbol": "PKN.PL", "type": "BUY", "volume": 10, "open_time": "2025-01-06 10:00",
                         "open_price": 100.0, "market_price": market_price}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 1500.0},
                  {"type": "Stock purchase", "time": "2025-01-06 10:00", "amount": -1000.0, "symbol": "PKN.PL"}])


def test_roll_forward_values_report_holdings_at_later_prices():
    """Raport z 8 stycznia (cena 110), wycena na 10 stycznia: skład i gotówka z raportu, cena już 121."""
    closed, open_, cash = _open_stock()
    prices = pd.DataFrame({"PKN.PL": [100.0, 105.0, 110.0, 115.0, 121.0]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, FX, as_of="2025-01-10", report_date="2025-01-08")
    assert pf.as_of == pd.Timestamp("2025-01-10")
    assert pf.value["PKN.PL"].tolist() == pytest.approx([1000.0, 1050.0, 1100.0, 1150.0, 1210.0])
    assert pf.equity.iloc[-1] == pytest.approx(500.0 + 1210.0) and pf.flows.sum() == pytest.approx(1500.0)
    assert pf.qty["PKN.PL"].iloc[-1] == 10 and pf.cash.iloc[-1] == pytest.approx(500.0)
    # bez przedłużenia portfel kończy się w dniu raportu
    short = reconstruct(closed, open_, cash, META, prices, FX, as_of="2025-01-08", report_date="2025-01-08")
    assert short.equity.iloc[-1] == pytest.approx(1600.0) and pf.equity.loc["2025-01-08"] == pytest.approx(1600.0)


def test_roll_forward_keeps_report_scale_when_quotes_are_mis_scaled():
    """Notowania 10x niższe niż ceny z raportu: skala ustalona w dniu raportu obowiązuje także po nim."""
    closed, open_, cash = _open_stock()
    prices = pd.DataFrame({"PKN.PL": [10.0, 10.5, 11.0, 11.5, 12.1]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, FX, as_of="2025-01-10", report_date="2025-01-08")
    assert pf.value["PKN.PL"].loc["2025-01-08"] == pytest.approx(1100.0)            # dokładnie cena z raportu
    assert pf.value["PKN.PL"].iloc[-1] == pytest.approx(1210.0)                     # +10% od dnia raportu, jak notowania


def test_roll_forward_continues_cfd_result_from_the_report():
    """CFD: w raporcie +500 przy 5100 (otwarcie 5000). Notowania z innego poziomu rosną potem o kolejne 2%."""
    closed, open_, cash = make_frames(
        positions_open=[{"symbol": "US500", "type": "BUY", "volume": 0.1, "open_time": "2025-01-06 10:00",
                         "open_price": 5000.0, "market_price": 5100.0, "gross_pl": 500.0}],
        cash_ops=[{"type": "deposit", "time": "2025-01-06 09:00", "amount": 2000.0}])
    prices = pd.DataFrame({"US500": [4000.0, 4040.0, 4080.0, 4120.8, 4161.6]}, index=DAYS)
    pf = reconstruct(closed, open_, cash, META, prices, FX, as_of="2025-01-10", report_date="2025-01-08")
    assert pf.value["US500"].loc["2025-01-08"] == pytest.approx(500.0)
    assert pf.value["US500"].iloc[-1] == pytest.approx(500.0 * (5100 * 1.02 - 5000) / 100.0)      # 5202 -> +1010
    assert pf.equity.iloc[-1] == pytest.approx(2000.0 + 1010.0)


def test_valuation_date_rules():
    from data.loader import valuation_date
    assert valuation_date("2026-11-11") == pd.Timestamp("2026-11-11")                        # bez "dziś" = data raportu
    assert valuation_date("2026-11-11", "2026-11-12") == pd.Timestamp("2026-11-12")
    assert valuation_date("2026-11-11", "2026-11-15") == pd.Timestamp("2026-11-13")          # niedziela -> piątek
    assert valuation_date("2026-11-11", "2026-11-01") == pd.Timestamp("2026-11-11")          # nigdy przed raportem


def test_context_rolls_forward_only_when_asked_or_live():
    same = build_context(None)
    assert same.pf_full.as_of == same.report_date == pd.Timestamp("2026-11-06")
    assert not any("Wycena na" in w for w in same.pf_full.warnings)
    ctx = build_context(None, today="2026-11-13")
    pf = ctx.pf_full
    assert pf.as_of == pd.Timestamp("2026-11-13") and ctx.report_date == pd.Timestamp("2026-11-06")
    assert pf.flows.sum() == pytest.approx(same.pf_full.flows.sum())                         # żadnych nowych wpłat
    assert (pf.qty.iloc[-1] == same.pf_full.qty.iloc[-1]).all()                              # skład z raportu
    assert pf.equity.iloc[-1] - pf.flows.sum() == pytest.approx(pf.pnl.values.sum())         # tożsamość wyniku
    assert pf.equity.loc["2026-11-06"] == pytest.approx(same.pf_full.equity.iloc[-1])        # historia bez zmian
    w = [x for x in pf.warnings if "Wycena na 13.11.2026" in x]
    assert w and "06.11.2026" in w[0] and "wgraj nowy eksport" in w[0]
