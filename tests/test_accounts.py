"""Prawdziwy układ eksportu xStation, kilka rachunków naraz i waluta wyświetlania."""
import json

import numpy as np
import pandas as pd
import pytest

from core import stress, tax
from core.portfolio import CASH_BUCKET, reconstruct
from core.risk import currency_exposure
from data.loader import convert_export
from data.synthetic import SyntheticMarketData
from data.xtb_parser import merge_exports, parse_accounts, parse_export
from server.app import create_app
from server.context import build_context
from server.sessions import SessionStore
from tests import real_layout as R
from tests.conftest import make_frames
from tests.test_server import check_blocks, strict_json, synthetic_provider
from tests.helpers import authed_client


@pytest.fixture(scope="module")
def files():
    return [R.pln_account(), R.usd_account()]


@pytest.fixture(scope="module")
def accounts(files):
    return parse_accounts(files)


# --------------------------------------------------------------------------- parser: prawdziwy układ

def test_real_layout_is_parsed(accounts):
    pln, usd = accounts
    assert (pln.account, pln.currency) == (R.PLN_ACC, "PLN")
    assert (usd.account, usd.currency) == (R.USD_ACC, "USD")      # z nazwy pliku i z arkusza otwartych pozycji
    assert usd.as_of == pd.Timestamp("2026-11-11")                # "Data as of report generated"
    assert (len(usd.closed), len(usd.open), len(usd.cash)) == (1, 4, len(R.USD_CASH))
    assert (len(pln.closed), len(pln.open), len(pln.cash)) == (1, 3, len(R.PLN_CASH))


def test_closed_positions_columns_with_utc_headers(accounts):
    c = accounts[1].closed.iloc[0]
    assert c["symbol"] == "SNOW.US" and c["type"] == "BUY" and c["position"] == "2700000002"
    assert c["open_time"] == pd.Timestamp("2026-04-10 13:34:13") and c["close_time"] == pd.Timestamp("2026-10-29 15:33:58")
    assert (c["open_price"], c["close_price"], c["gross_pl"]) == (129.44, 328.99, 199.55)
    assert (c["purchase_value"], c["sale_value"]) == (129.44, 328.99)


def test_open_positions_skip_instrument_summary_rows(accounts):
    o = accounts[1].open
    assert o["symbol"].tolist() == ["ORCL.US", "RKLB.US", "RKLB.US", "SMH.UK"]      # bez wierszy zbiorczych
    rk = o[o["symbol"] == "RKLB.US"]
    assert rk["volume"].tolist() == [25.0, 10.0] and rk["position"].tolist() == ["2836382388", "2840000001"]
    assert rk["market_price"].tolist() == [62.71, 62.71]
    # raport podaje wartość rynkową i wynik; wartość zakupu = wartość - wynik
    assert rk["purchase_value"].round(2).tolist() == [1858.25, 537.50]


def test_text_numbers_with_decimal_comma_give_the_same_result(accounts):
    again = parse_accounts([R.usd_account(text_numbers=True)])[0]
    pd.testing.assert_frame_equal(again.open, accounts[1].open)


def test_cash_operation_kinds(accounts):
    kinds = accounts[1].cash.groupby("kind")["amount"].sum().round(2).to_dict()
    assert kinds["TRANSFER"] == pytest.approx(2000 + 835.64 + 1089.97)
    assert kinds["STOCK_SALE"] == 328.99 and kinds["FEE"] == -0.01 and kinds["WHT"] == -0.30 and kinds["DIVIDEND"] == 2.0
    pk = accounts[0].cash.set_index("kind")["amount"]
    assert pk["INTEREST_TAX"] == -2.36 and pk["INTEREST"] == 12.40 and pk["DEPOSIT"] == 30000.0


def test_instrument_hints_from_report(accounts):
    hints = accounts[1].instruments
    assert hints["SMH.UK"] == {"name": "VanEck Semiconductor UCITS ETF", "category": "ETF", "currency": "USD"}
    assert "currency" not in accounts[0].instruments["META.US"]      # na rachunku PLN wartości są przeliczone


def test_single_file_still_works_with_parse_export():
    exp = parse_export([R.usd_account()])
    assert exp.currency == "USD" and exp.account == R.USD_ACC and len(exp.open) == 4


def test_newer_export_of_the_same_account_wins(files):
    name, data = R.usd_account()
    older = (name.replace("2026-11-11", "2026-10-01"), data)
    accs = parse_accounts([files[0], older, files[1]])
    assert [a.account for a in accs] == [R.PLN_ACC, R.USD_ACC]
    assert len(accs[1].open) == 4 and len(accs[1].files) == 1
    assert any("starszy eksport" in n for n in accs[1].notes)


# --------------------------------------------------------------------------- scalanie rachunków

def test_merge_marks_transfers_between_own_accounts_as_internal(accounts):
    both = merge_exports(accounts)
    assert both.currency == "MIX" and [a["selected"] for a in both.accounts] == [True, True]
    assert (both.cash["kind"] == "INTERNAL").sum() == 6 and (both.cash["kind"] == "TRANSFER").sum() == 0
    assert set(both.cash["currency"]) == {"PLN", "USD"} and set(both.open["account"]) == {R.PLN_ACC, R.USD_ACC}
    only_usd = merge_exports(accounts, [R.USD_ACC])
    assert only_usd.currency == "USD" and (only_usd.cash["kind"] == "TRANSFER").sum() == 3     # dla jednego rachunku to wpłaty
    assert [a["selected"] for a in only_usd.accounts] == [False, True]


def test_merge_requires_a_known_account(accounts):
    from data.xtb_parser import XTBFormatError
    with pytest.raises(XTBFormatError):
        merge_exports(accounts, ["000"])


# --------------------------------------------------------------------------- silnik: gotówka w kilku walutach

def _fx(rates: dict, days) -> pd.DataFrame:
    return pd.DataFrame(rates, index=pd.DatetimeIndex(days))


def test_usd_cash_is_revalued_daily_in_pln():
    """100 USD wpłacone przy kursie 4,00; kurs rośnie do 4,20 -> portfel wart 420 zł, wynik +20 zł z samej waluty."""
    closed, open_, cash = make_frames(cash_ops=[{"type": "Deposit", "time": "2026-03-02 10:00", "amount": 100.0}])
    days = pd.bdate_range("2026-03-02", "2026-03-04")
    fx = _fx({"USD": [4.00, 4.10, 4.20], "PLN": [1.0, 1.0, 1.0]}, days)
    pf = reconstruct(closed, open_, cash, pd.DataFrame(columns=["asset_class", "currency", "multiplier"]),
                     pd.DataFrame(index=days), fx, as_of="2026-03-04", account_currency="USD", base="PLN")
    assert pf.equity.tolist() == pytest.approx([400.0, 410.0, 420.0])
    assert pf.flows.sum() == pytest.approx(400.0)
    assert pf.pnl[CASH_BUCKET].tolist() == pytest.approx([0.0, 10.0, 10.0])
    assert pf.cash_fx_pnl["USD"].sum() == pytest.approx(20.0)
    assert (1 + pf.returns).prod() - 1 == pytest.approx(0.05)
    # ten sam rachunek oglądany w dolarach nie ma żadnego wyniku
    usd = fx.div(fx["USD"], axis=0)
    pf_usd = reconstruct(closed, open_, cash, pf.meta, pd.DataFrame(index=days), usd, as_of="2026-03-04",
                         account_currency="USD", base="USD")
    assert pf_usd.equity.tolist() == pytest.approx([100.0] * 3) and pf_usd.pnl.values.sum() == pytest.approx(0.0)


def test_internal_transfer_is_not_a_flow_and_shows_conversion_cost():
    """1000 zł wpłaty; 400 zł zamienione na 99 USD przy kursie rynkowym 4,00 -> 4 zł kosztu wymiany, zero przepływu."""
    _, _, cash = make_frames(cash_ops=[
        {"type": "Deposit", "time": "2026-03-02 09:00", "amount": 1000.0},
        {"type": "Transfer", "time": "2026-03-03 09:00", "amount": -400.0},
        {"type": "Transfer", "time": "2026-03-03 09:00", "amount": 99.0}])
    cash["currency"] = ["PLN", "PLN", "USD"]
    cash.loc[1:, "kind"] = "INTERNAL"
    closed, open_, _ = make_frames()
    days = pd.bdate_range("2026-03-02", "2026-03-04")
    fx = _fx({"USD": [4.0, 4.0, 4.0], "PLN": [1.0, 1.0, 1.0]}, days)
    pf = reconstruct(closed, open_, cash, pd.DataFrame(columns=["asset_class", "currency", "multiplier"]),
                     pd.DataFrame(index=days), fx, as_of="2026-03-04", base="PLN")
    assert pf.flows.tolist() == pytest.approx([1000.0, 0.0, 0.0])
    assert pf.equity.tolist() == pytest.approx([1000.0, 996.0, 996.0])
    assert pf.cash_by_currency.iloc[-1].to_dict() == pytest.approx({"PLN": 600.0, "USD": 396.0})
    assert pf.pnl[CASH_BUCKET].sum() == pytest.approx(-4.0)


def test_convert_export_uses_rate_of_operation_day():
    closed, open_, cash = make_frames(cash_ops=[
        {"type": "Deposit", "time": "2026-03-02 10:00", "amount": 100.0},
        {"type": "Dividend", "time": "2026-03-07 10:00", "amount": 10.0, "symbol": "AAPL.US"}])      # sobota -> kurs z piątku
    from data.xtb_parser import XTBExport
    exp = XTBExport(closed=closed, open=open_, cash=cash, currency="USD")
    fx = _fx({"USD": [4.00, 4.10, 4.20, 4.30, 4.40]}, pd.bdate_range("2026-03-02", "2026-03-06"))
    out = convert_export(exp, fx, "PLN")
    assert out.cash["amount"].tolist() == pytest.approx([400.0, 44.0]) and out.currency == "PLN"
    assert exp.cash["amount"].tolist() == [100.0, 10.0]          # oryginał nietknięty


# --------------------------------------------------------------------------- cały kontekst: rachunki i waluta

@pytest.fixture(scope="module")
def ctx_pln(files):
    return build_context(files, provider=SyntheticMarketData(), base="PLN")


@pytest.fixture(scope="module")
def ctx_usd(files):
    return build_context(files, provider=SyntheticMarketData(), base="USD")


def test_cash_balances_match_the_reports(ctx_pln):
    rate = float(ctx_pln.market.fx["USD"].iloc[-1])
    cash = ctx_pln.cash_by_currency()
    assert cash["PLN"] == pytest.approx(R.PLN_CASH_END)
    assert cash["USD"] == pytest.approx(R.USD_CASH_END * rate)
    assert ctx_pln.account_currencies == ("PLN", "USD")


def test_only_real_deposits_are_flows_when_both_accounts_selected(ctx_pln):
    pf = ctx_pln.pf
    assert pf.flows.sum() == pytest.approx(30000.0)
    assert pf.equity.iloc[-1] - pf.flows.sum() == pytest.approx(pf.pnl.values.sum())      # tożsamość wyniku


def test_display_currency_changes_units_not_the_portfolio(ctx_pln, ctx_usd):
    rate = float(ctx_pln.market.fx["USD"].iloc[-1])
    assert ctx_usd.base == "USD" and ctx_usd.pf.base == "USD"
    assert ctx_usd.pf.equity.iloc[-1] * rate == pytest.approx(ctx_pln.pf.equity.iloc[-1], rel=1e-9)
    assert ctx_usd.pf.equity.iloc[-1] - ctx_usd.pf.flows.sum() == pytest.approx(ctx_usd.pf.pnl.values.sum())
    # stopa zwrotu w dolarach i w złotych różni się dokładnie o zmianę kursu w czasie - nie może wyjść ta sama
    twr = lambda c: float((1 + c.pf.returns).prod() - 1)
    assert twr(ctx_pln) != pytest.approx(twr(ctx_usd), abs=1e-4)
    assert ctx_usd.bench_label("SP500") == "S&P 500" and ctx_pln.bench_label("SP500") == "S&P 500 (w PLN)"
    assert ctx_usd.bench_label("NDX") == "Nasdaq 100"


def test_single_account_selection(files):
    ctx = build_context(files, provider=SyntheticMarketData(), base="USD", accounts=[R.USD_ACC])
    assert [a["selected"] for a in ctx.accounts] == [False, True]
    assert ctx.pf.flows.sum() == pytest.approx(2000 + 835.64 + 1089.97)      # przelewy z rachunku PLN to teraz wpłaty
    assert ctx.cash_by_currency() == pytest.approx({"USD": R.USD_CASH_END})
    assert set(ctx.pf.symbols) == {"SNOW.US", "ORCL.US", "RKLB.US", "SMH.UK"}
    excluded = build_context(files, provider=SyntheticMarketData(), exclude=[R.PLN_ACC])
    assert [a["selected"] for a in excluded.accounts] == [False, True]


def test_report_hints_reach_the_mapping(ctx_pln):
    m = ctx_pln.mapping.set_index("symbol")
    assert m.at["SMH.UK", "currency"] == "USD" and m.at["SMH.UK", "asset_class"] == "ETF"
    assert m.at["RKLB.US", "yf_ticker"] == "RKLB" and m.at["NVO.US", "country"] == "Dania"


def test_price_check_flags_quotes_far_from_report(ctx_pln):
    assert any("odbiega od ceny w raporcie XTB" in w for w in ctx_pln.pf.warnings)     # syntetyczne ceny nie pasują do raportu


def test_tax_is_always_in_pln_and_independent_of_display_currency(ctx_pln, ctx_usd):
    from server.screens import tax as tax_screen
    a, b = tax_screen.build(ctx_pln, 2026, "nbp"), tax_screen.build(ctx_usd, 2026, "nbp")
    assert a.total == pytest.approx(b.total)
    for k in ("gross", "wht", "tax", "credit", "due"):
        assert a.dividends[k] == pytest.approx(b.dividends[k])
    f = a.fifo.set_index("symbol")
    usd_rate = ctx_pln.market.fx_pln["USD"]
    r_buy, _ = tax.nbp_rate_before(usd_rate, pd.Timestamp("2026-04-10"))
    r_sell, _ = tax.nbp_rate_before(usd_rate, pd.Timestamp("2026-10-29"))
    assert f.at["SNOW.US", "cost"] == pytest.approx(129.44 * r_buy)
    assert f.at["SNOW.US", "revenue"] == pytest.approx(328.99 * r_sell)
    # dywidendy z obu rachunków, każda przeliczona na złote
    d = a.dividend_rows.set_index("symbol")
    assert set(d.index) == {"NVO.US", "ORCL.US"} and (d["gross"] > 0).all()
    assert d.at["ORCL.US", "gross"] > 2.0 * 3.0                                   # 2 USD to nie 2 zł


def test_fifo_queues_are_separate_per_account():
    """Ten sam walor na dwóch rachunkach: sprzedaż z rachunku B zdejmuje zakup z rachunku B, nie starszy z A."""
    closed, open_, _ = make_frames(
        positions_closed=[{"symbol": "AAA.PL", "type": "BUY", "volume": 10, "open_time": "2026-02-02", "open_price": 20.0,
                           "close_time": "2026-03-02", "close_price": 30.0}],
        positions_open=[{"symbol": "AAA.PL", "type": "BUY", "volume": 10, "open_time": "2026-01-05", "open_price": 10.0}])
    closed["account"], open_["account"] = "B", "A"
    meta = pd.DataFrame({"asset_class": ["STOCK"], "currency": ["PLN"], "country": ["Polska"]}, index=["AAA.PL"])
    f = tax.fifo_matches(closed, open_, meta, None)
    assert f["cost"].sum() == pytest.approx(200.0) and f["income"].sum() == pytest.approx(100.0)
    closed["account"] = "A"                                    # ten sam rachunek: schodzi najstarszy zakup po 10 zł
    assert tax.fifo_matches(closed, open_, meta, None)["cost"].sum() == pytest.approx(100.0)


# --------------------------------------------------------------------------- ryzyko i stres w walucie bazowej

def test_fx_shock_is_relative_to_base_currency():
    shocks = {"USDPLN": 0.15, "EURPLN": 0.05}
    assert stress.fx_shock("USD", "PLN", shocks) == pytest.approx(0.15)
    assert stress.fx_shock("PLN", "USD", shocks) == pytest.approx(1 / 1.15 - 1)
    assert stress.fx_shock("EUR", "USD", shocks) == pytest.approx(1.05 / 1.15 - 1)
    assert stress.fx_shock("USD", "USD", shocks) == 0.0 and stress.fx_shock("PLN", "PLN", shocks) == 0.0


def test_stress_scenario_includes_foreign_cash_and_base():
    meta = pd.DataFrame({"asset_class": ["STOCK", "STOCK"], "currency": ["USD", "PLN"], "country": ["USA", "Polska"],
                         "sector": ["Technologia", "Banki"]}, index=["AAPL.US", "PKO.PL"])
    exp = pd.Series({"AAPL.US": 1000.0, "PKO.PL": 1000.0})
    shocks = {"USDPLN": 0.10}
    pln = stress.apply_scenario(exp, meta, shocks, base="PLN", cash={"PLN": 500.0, "USD": 200.0})
    assert pln.at["AAPL.US", "pnl"] == pytest.approx(100.0) and pln.at["PKO.PL", "pnl"] == 0.0
    assert pln.at["Gotówka USD", "pnl"] == pytest.approx(20.0) and "Gotówka PLN" not in pln.index
    usd = stress.apply_scenario(exp, meta, shocks, base="USD", cash={"PLN": 500.0, "USD": 200.0})
    assert usd.at["AAPL.US", "pnl"] == 0.0
    assert usd.at["PKO.PL", "pnl"] == pytest.approx(1000.0 * (1 / 1.1 - 1))
    assert usd.at["Gotówka PLN", "pnl"] == pytest.approx(500.0 * (1 / 1.1 - 1))


def test_currency_exposure_counts_cash_per_currency():
    meta = pd.DataFrame({"currency": ["USD", "PLN"]}, index=["A", "B"])
    out = currency_exposure(pd.Series({"A": 600.0, "B": 100.0}), meta, {"PLN": 200.0, "USD": 100.0}, 1000.0, "PLN")
    assert out["value"].to_dict() == {"USD": 700.0, "PLN": 300.0} and out["share"].sum() == pytest.approx(1.0)
    assert currency_exposure(pd.Series({"A": 600.0}), meta, 400.0, 1000.0)["value"].to_dict() == {"USD": 600.0, "PLN": 400.0}


# --------------------------------------------------------------------------- API

@pytest.fixture(scope="module")
def client():
    return authed_client()


def _upload(client, files, **form):
    import io
    data = {"files": [(io.BytesIO(b), n) for n, b in files], **form}
    r = client.post("/api/session", data=data, content_type="multipart/form-data")
    assert r.status_code == 201, r.get_data(as_text=True)
    return strict_json(r)


def test_api_session_lists_accounts_and_base(client, files):
    meta = _upload(client, files)
    assert meta["base"] == "PLN" and meta["base_choices"] == ["PLN", "USD", "EUR"]
    assert [(a["id"], a["currency"], a["selected"]) for a in meta["accounts"]] == [(R.PLN_ACC, "PLN", True), (R.USD_ACC, "USD", True)]
    assert sorted(meta["files_used"]) == sorted(n for n, _ in files) and not meta["unmapped"]
    usd_only = _upload(client, files, base="USD", exclude=json.dumps([R.PLN_ACC]))
    assert usd_only["base"] == "USD" and [a["selected"] for a in usd_only["accounts"]] == [False, True]


def test_api_view_switches_currency_and_accounts_in_place(client, files):
    meta = _upload(client, files)
    sid, equity_pln = meta["sid"], meta["equity"]
    r = client.put(f"/api/session/{sid}/view", json={"base": "USD"})
    usd = strict_json(r)
    assert r.status_code == 200 and usd["sid"] == sid and usd["base"] == "USD" and usd["equity"] < equity_pln / 3
    r = client.put(f"/api/session/{sid}/view", json={"accounts": [R.USD_ACC]})
    one = strict_json(r)
    assert one["base"] == "USD" and [a["selected"] for a in one["accounts"]] == [False, True] and one["equity"] < usd["equity"]
    assert client.put(f"/api/session/{sid}/view", json={"accounts": []}).status_code == 400
    # poprawka tickera nie resetuje wyboru
    r = client.put(f"/api/session/{sid}/mapping", json={"mapping": [{"symbol": "RKLB.US", "sector": "Kosmos"}]})
    kept = strict_json(r)
    assert kept["base"] == "USD" and [a["selected"] for a in kept["accounts"]] == [False, True]
    assert client.put(f"/api/session/{sid}/view", json={"base": "XXX", "accounts": [R.PLN_ACC, R.USD_ACC]}).get_json()["base"] == "PLN"


def test_every_screen_renders_for_two_accounts_in_usd(client, files):
    from server.screens import SCREENS
    sid = _upload(client, files, base="USD")["sid"]
    for name in SCREENS:
        r = client.get(f"/api/session/{sid}/screen/{name}")
        assert r.status_code == 200, f"{name}: {r.get_data(as_text=True)[:300]}"
        out = strict_json(r)
        assert out["currency"] == "USD"
        check_blocks(out["blocks"], {})
    text = client.get(f"/api/session/{sid}/screen/overview").get_data(as_text=True)
    assert " $" in text and " zł" not in text
    tax_text = client.get(f"/api/session/{sid}/screen/tax").get_data(as_text=True)
    assert " zł" in tax_text and '"fmt": "zl"' in tax_text and '"fmt": "pln"' not in tax_text      # podatki zawsze w złotych
    stress_text = client.get(f"/api/session/{sid}/screen/sim-stress").get_data(as_text=True)
    assert "Gotówka PLN" in stress_text
