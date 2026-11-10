import numpy as np
import pandas as pd
import pytest

from core import tax
from tests.conftest import make_frames

META = pd.DataFrame({
    "asset_class": ["STOCK", "CFD", "STOCK", "ETF"], "currency": ["USD", "USD", "PLN", "EUR"],
    "country": ["USA", "USA", "Polska", "Niemcy"], "multiplier": [1.0, 50.0, 1.0, 1.0]},
    index=["AAPL.US", "US500", "PKN.PL", "EUNL.DE"])
# kursy NBP tylko w dni robocze; 2025-01-10 to piątek, 2025-01-13 poniedziałek
DAYS = pd.bdate_range("2024-12-20", "2025-12-31")
FX = pd.DataFrame({"USD": np.linspace(4.00, 4.50, len(DAYS)), "EUR": 4.30, "PLN": 1.0}, index=DAYS)


def usd(day):
    return float(FX.at[pd.Timestamp(day), "USD"])


def test_nbp_rate_is_from_previous_business_day():
    rate, date = tax.nbp_rate_before(FX["USD"], "2025-01-13 15:30")        # poniedziałek -> kurs z piątku
    assert date == pd.Timestamp("2025-01-10") and rate == usd("2025-01-10")
    rate, date = tax.nbp_rate_before(FX["USD"], "2025-01-15")              # środa -> wtorek
    assert date == pd.Timestamp("2025-01-14")
    rate, date = tax.nbp_rate_before(FX["USD"], "2025-01-12")              # niedziela -> piątek
    assert date == pd.Timestamp("2025-01-10")
    assert tax.nbp_rate_before(FX["USD"], "2020-01-01")[1] == DAYS[0]      # przed początkiem danych: najbliższy kurs


def test_rounding_to_full_zloty():
    assert tax.round_pln(104.49) == 104 and tax.round_pln(104.50) == 105 and tax.round_pln(0.5) == 1
    assert tax.round_pln(-104.5) == -105 and tax.round_pln(0.0) == 0


def _fifo_case():
    """Dwa zakupy AAPL (10 szt. po 100 USD, 10 szt. po 120 USD); sprzedaż 15 szt. po 150 USD.
    XTB zapisuje to jako dwie zamknięte pozycje (10 + 5) i jedną otwartą (5)."""
    return make_frames(
        positions_closed=[
            {"symbol": "AAPL.US", "type": "BUY", "volume": 10, "open_time": "2025-01-13 16:00", "open_price": 100.0,
             "close_time": "2025-06-16 16:00", "close_price": 150.0, "purchase_value": 4100.0, "sale_value": 6400.0,
             "gross_pl": 2300.0, "commission": -4.0},
            {"symbol": "AAPL.US", "type": "BUY", "volume": 5, "open_time": "2025-03-03 16:00", "open_price": 120.0,
             "close_time": "2025-06-16 16:00", "close_price": 150.0, "purchase_value": 2500.0, "sale_value": 3200.0, "gross_pl": 700.0},
            {"symbol": "PKN.PL", "type": "BUY", "volume": 100, "open_time": "2025-02-03 10:00", "open_price": 50.0,
             "close_time": "2025-09-01 10:00", "close_price": 45.0, "purchase_value": 5000.0, "sale_value": 4500.0, "gross_pl": -500.0},
            {"symbol": "US500", "type": "BUY", "volume": 0.1, "open_time": "2025-04-01 10:00", "open_price": 5000.0,
             "close_time": "2025-04-03 10:00", "close_price": 5100.0, "gross_pl": 2000.0, "swap": -15.0},
            {"symbol": "US500", "type": "SELL", "volume": 0.1, "open_time": "2025-05-01 10:00", "open_price": 5000.0,
             "close_time": "2025-05-02 10:00", "close_price": 5050.0, "gross_pl": -1000.0, "swap": -5.0},
        ],
        positions_open=[{"symbol": "AAPL.US", "type": "BUY", "volume": 5, "open_time": "2025-03-03 16:00", "open_price": 120.0,
                         "purchase_value": 2500.0}],
        cash_ops=[
            {"type": "DIVIDENT", "time": "2025-05-15 12:00", "amount": 100.0, "symbol": "AAPL.US", "comment": "AAPL.US USD 0.2500/ SHR"},
            {"type": "Withholding Tax", "time": "2025-05-15 12:00", "amount": -15.0, "symbol": "AAPL.US", "comment": "AAPL.US USD WHT 15%"},
            {"type": "DIVIDENT", "time": "2025-08-20 12:00", "amount": 400.0, "symbol": "PKN.PL", "comment": "PKN.PL PLN 4.0000/ SHR"},
            {"type": "Withholding Tax", "time": "2025-08-20 12:00", "amount": -76.0, "symbol": "PKN.PL", "comment": "PKN.PL PLN WHT 19%"},
            {"type": "Free-funds Interest", "time": "2025-03-31 23:00", "amount": 10.0},
            {"type": "Free-funds Interest Tax", "time": "2025-03-31 23:00", "amount": -1.9},
        ])


def test_fifo_by_hand_with_nbp_rates():
    closed, open_, cash = _fifo_case()
    m = tax.fifo_matches(closed, open_, META, FX)
    aapl = m[m["symbol"] == "AAPL.US"].reset_index(drop=True)
    assert aapl["qty"].tolist() == [10, 5] and aapl["buy_price"].tolist() == [100.0, 120.0]
    # kursy D-1: zakup w pon. 13.01 -> piątek 10.01; zakup w pon. 03.03 -> piątek 28.02; sprzedaż w pon. 16.06 -> piątek 13.06
    assert aapl["buy_rate_date"].tolist() == [pd.Timestamp("2025-01-10"), pd.Timestamp("2025-02-28")]
    assert (aapl["sell_rate_date"] == pd.Timestamp("2025-06-13")).all()
    cost = 10 * 100 * usd("2025-01-10") + 5 * 120 * usd("2025-02-28")
    revenue = 15 * 150 * usd("2025-06-13")
    assert aapl["cost"].sum() == pytest.approx(cost) and aapl["revenue"].sum() == pytest.approx(revenue)
    pkn = m[m["symbol"] == "PKN.PL"].iloc[0]
    assert pkn["buy_rate"] == 1.0 and pkn["income"] == pytest.approx(-500.0)
    assert "US500" not in set(m["symbol"])                                   # CFD nie wchodzą do FIFO


def test_fifo_takes_oldest_lot_first_across_positions():
    """XTB zamknęło NOWSZĄ pozycję, ale podatkowo sprzedane są najstarsze akcje."""
    closed, open_, _ = make_frames(
        positions_closed=[{"symbol": "PKN.PL", "type": "BUY", "volume": 10, "open_time": "2025-03-03 10:00", "open_price": 80.0,
                           "close_time": "2025-06-02 10:00", "close_price": 100.0}],
        positions_open=[{"symbol": "PKN.PL", "type": "BUY", "volume": 10, "open_time": "2025-01-13 10:00", "open_price": 50.0}])
    m = tax.fifo_matches(closed, open_, META, FX)
    assert len(m) == 1 and m["buy_price"].iloc[0] == 50.0 and m["income"].iloc[0] == pytest.approx(10 * (100 - 50))


def test_annual_report_by_hand():
    closed, open_, cash = _fifo_case()
    rep = tax.build_report(2025, closed, open_, cash, META, FX)
    sec_rev = 15 * 150 * usd("2025-06-13") + 4500
    sec_cost = 10 * 100 * usd("2025-01-10") + 5 * 120 * usd("2025-02-28") + 5000 + 4.0      # + prowizja
    assert rep.securities["revenue"] == pytest.approx(sec_rev) and rep.securities["cost"] == pytest.approx(sec_cost)
    assert rep.derivatives == {"revenue": 2000.0, "cost": 1020.0, "income": 980.0, "n": 2}
    income = sec_rev - sec_cost + 980
    assert rep.total["income"] == pytest.approx(income) and rep.total["base"] == tax.round_pln(income)
    assert rep.total["tax"] == tax.round_pln(tax.round_pln(income) * 0.19)
    assert rep.foreign_income.loc["USA", "income"] == pytest.approx(sec_rev - 4500 - (sec_cost - 5000 - 4.0))
    assert rep.interest == {"gross": 10.0, "tax": pytest.approx(1.9)}
    alt = tax.build_report(2025, closed, open_, cash, META, FX, method="report")
    assert alt.securities["revenue"] == pytest.approx(6400 + 3200 + 4500) and alt.securities["cost"] == pytest.approx(4100 + 2500 + 5000 + 4)
    empty = tax.build_report(2024, closed, open_, cash, META, FX)
    assert empty.total["tax"] == 0 and len(empty.fifo) == 0
    assert tax.available_years(closed, cash) == [2025]


def test_foreign_dividend_tax_credit():
    """100 zł brutto z USA, 15 zł pobrane u źródła: 19 zł podatku, 15 zł odliczenia, 4 zł dopłaty."""
    closed, open_, cash = _fifo_case()
    rep = tax.build_report(2025, closed, open_, cash, META, FX, method="report")
    d = rep.dividends
    assert d["gross"] == 100.0 and d["wht"] == 15.0 and d["tax"] == 19 and d["credit"] == 15 and d["due"] == 4
    assert list(d["by_country"].index) == ["USA"]                           # polska dywidenda rozliczona przez płatnika
    assert d["domestic_gross"] == 400.0 and d["domestic_tax_withheld"] == 76.0
    # podatek u źródła wyższy niż 19%: odliczenie ograniczone do 19% brutto, dopłata 0
    high = cash.copy()
    high.loc[high["kind"].eq("WHT") & high["symbol"].eq("AAPL.US"), "amount"] = -30.0
    d2 = tax.dividend_tax(tax.dividends(high, META), 2025)
    assert d2["credit"] == 19 and d2["due"] == 0


def test_dividend_recomputed_at_nbp_rate_when_possible():
    closed, open_, cash = _fifo_case()
    qty = pd.DataFrame({"AAPL.US": 100.0}, index=DAYS)                       # 100 akcji x 0,25 USD = 25 USD
    rows = tax.dividends(cash, META, FX, qty)
    aapl = rows[rows["symbol"] == "AAPL.US"].iloc[0]
    rate = usd("2025-05-14")
    assert aapl["method"] == "kurs NBP D-1" and aapl["rate_date"] == pd.Timestamp("2025-05-14")
    assert aapl["gross"] == pytest.approx(25 * rate) and aapl["wht"] == pytest.approx(25 * rate * 0.15)
    assert rows[rows["symbol"] == "PKN.PL"].iloc[0]["method"] == "kwota z raportu"
    # stan posiadania nie pasuje do wypłaty -> zostaje kwota z raportu
    off = tax.dividends(cash, META, FX, pd.DataFrame({"AAPL.US": 1000.0}, index=DAYS))
    assert off[off["symbol"] == "AAPL.US"].iloc[0]["method"] == "kwota z raportu"


def test_exports():
    closed, open_, cash = _fifo_case()
    rep = tax.build_report(2025, closed, open_, cash, META, FX)
    csv = tax.fifo_csv(rep)
    lines = csv.strip().split("\n")
    assert lines[0].startswith("Rok;Symbol;Kraj") and len(lines) == 1 + 3 + 1 and "RAZEM" in lines[-1]
    assert ";2025-01-10;" in lines[1] and "," in lines[1] and "." not in lines[1].replace("AAPL.US", "")
    assert "USA" in tax.dividends_csv(rep) and "PIT/ZG" in tax.summary_csv(rep) and "porada" in tax.summary_csv(rep)
    pdf = tax.report_pdf(rep, "server/fonts")
    assert pdf[:5] == b"%PDF-" and len(pdf) > 5000
    assert tax.report_pdf(rep, None)[:5] == b"%PDF-"                         # bez kroju z polskimi znakami też działa


def test_tax_on_sample(export, mapping, market, portfolio):
    meta = mapping.set_index("symbol")
    years = tax.available_years(export.closed, export.cash)
    assert 2025 in years and 2026 in years
    rep = tax.build_report(2025, export.closed, export.open, export.cash, meta, market.fx, portfolio.qty)
    alt = tax.build_report(2025, export.closed, export.open, export.cash, meta, market.fx, portfolio.qty, method="report")
    assert rep.securities["revenue"] > 0 and rep.total["tax"] >= 0
    # kursy NBP D-1 kontra kurs brokera z marżą 0,5%: wyniki bliskie, ale nie identyczne
    assert rep.securities["revenue"] == pytest.approx(alt.securities["revenue"], rel=0.03)
    assert rep.securities["revenue"] != alt.securities["revenue"]
    assert rep.dividends["gross"] > 0 and rep.dividends["due"] >= 0
    assert (rep.dividend_rows.loc[rep.dividend_rows["foreign"], "method"] == "kurs NBP D-1").all()
    assert not rep.notes or all("strat" in n.lower() for n in rep.notes)
