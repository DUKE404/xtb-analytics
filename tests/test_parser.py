import io

import numpy as np
import pandas as pd
import pytest

from data.mapping import auto_map, build_mapping
from data.xtb_parser import XTBFormatError, cash_kind, clean_symbol, parse_datetime, parse_export, parse_number


@pytest.mark.parametrize("raw,expected", [
    ("1 234,56", 1234.56), ("1,234.56", 1234.56), ("1.234,56", 1234.56), ("-12,5", -12.5),
    ("12.5", 12.5), (7, 7.0), ("1 000", 1000.0), ("0,0001", 0.0001), ("1,234,567", 1234567.0),
    ("-1 250,00 PLN", -1250.0),
])
def test_parse_number(raw, expected):
    assert parse_number(raw) == pytest.approx(expected)


def test_parse_number_empty():
    assert np.isnan(parse_number("")) and np.isnan(parse_number(None)) and np.isnan(parse_number("-"))


@pytest.mark.parametrize("raw,expected", [
    ("2024-03-05 14:35:22", "2024-03-05 14:35:22"),
    ("05.03.2024 14:35:22", "2024-03-05 14:35:22"),
    ("05/03/2024 14:35", "2024-03-05 14:35:00"),
    ("2024/03/05", "2024-03-05 00:00:00"),
    ("5.3.2024", "2024-03-05 00:00:00"),
    ("03/25/2024 10:00:00", "2024-03-25 10:00:00"),   # zapis amerykański: miesiąc > 12 na drugiej pozycji
    ("2024-03-05T09:00:01", "2024-03-05 09:00:01"),
    (45356.5, "2024-03-05 12:00:00"),                  # numer seryjny Excela
])
def test_parse_datetime(raw, expected):
    assert parse_datetime(raw) == pd.Timestamp(expected)


def test_parse_datetime_bad():
    assert pd.isna(parse_datetime("nie data")) and pd.isna(parse_datetime(None))


def test_cash_kinds():
    assert cash_kind("deposit") == "DEPOSIT" and cash_kind("Wpłata") == "DEPOSIT"
    assert cash_kind("Wypłata") == "WITHDRAWAL"
    assert cash_kind("DIVIDENT") == "DIVIDEND" and cash_kind("Dywidenda") == "DIVIDEND"
    assert cash_kind("Withholding Tax") == "WHT" and cash_kind("Podatek u źródła") == "WHT"
    assert cash_kind("Free-funds Interest Tax") == "INTEREST_TAX"
    assert cash_kind("Free-funds Interest") == "INTEREST"
    assert cash_kind("Stock purchase") == "STOCK_PURCHASE" and cash_kind("Zakup akcji/ETF") == "STOCK_PURCHASE"
    assert cash_kind("Sprzedaż akcji/ETF") == "STOCK_SALE"
    assert cash_kind("close trade") == "CLOSE_TRADE" and cash_kind("swap") == "SWAP"
    assert cash_kind("coś dziwnego") == "OTHER"


def test_clean_symbol():
    assert clean_symbol(" aapl.us_9 ") == "AAPL.US"
    assert clean_symbol("AAPL.US_4") == "AAPL.US_4"


def test_sample_roundtrip(export, sample_data):
    assert len(export.closed) == len(sample_data["closed"])
    assert len(export.open) == len(sample_data["open"])
    assert len(export.cash) == len(sample_data["cash"])
    assert export.as_of == pd.Timestamp("2026-11-06")
    assert export.currency == "PLN"
    assert export.closed["open_time"].dtype.kind == "M" and export.cash["time"].dtype.kind == "M"
    assert export.cash["amount"].sum() == pytest.approx(sample_data["balance"], abs=0.01)
    assert set(export.closed["type"]) <= {"BUY", "SELL"}
    assert export.closed["gross_pl"].sum() == pytest.approx(sample_data["closed"]["Gross P/L"].sum(), abs=0.01)
    # wiersz "Total" nie może trafić do danych
    assert not export.closed["symbol"].eq("").any()


PL_CLOSED = """Raport;;;;
;;;;
Pozycja;Symbol;Typ;Wolumen;Czas otwarcia;Cena otwarcia;Czas zamknięcia;Cena zamknięcia;Wartość zakupu;Wartość sprzedaży;Prowizja;Swap;Zysk/strata brutto;Komentarz
1001;PKN.PL;Kupno;10;02.01.2025 10:15:00;62,50;14.02.2025 15:01:10;70,10;625,00;701,00;0;0;76,00;
1002;aapl.us_9;Kupno;2,5;03.01.2025 16:00:00;"1 180,25";20.03.2025 20:00:00;190,00;1 850,10;1 900,00;-1,5;0;49,90;test
1003;US500;Sprzedaż;0,05;10.01.2025 09:00:00;5 900,5;10.01.2025 18:00:00;5 880,0;0;0;0;-3,2;205,40;
Suma;;;;;;;;;;;;331,30;
"""
PL_CASH = """ID;Typ;Czas;Komentarz;Symbol;Kwota
1;Wpłata;01.01.2025 08:00:00;przelew;;10 000,00
2;Zakup akcji/ETF;02.01.2025 10:15:00;OPEN BUY 10 @ 62.50;PKN.PL;-625,00
3;Dywidenda;05.02.2025 12:00:00;PKN.PL PLN 4.1500/ SHR;;41,50
4;Podatek u źródła;05.02.2025 12:00:00;PKN.PL PLN WHT 19%;;-7,89
"""


def test_polish_csv_with_decimal_commas():
    exp = parse_export([("zamkniete.csv", PL_CLOSED.encode("cp1250")), ("gotowka.csv", PL_CASH.encode("utf-8"))])
    c = exp.closed.set_index("position")
    assert len(c) == 3                                     # bez wiersza "Suma"
    assert c.at["1001", "open_price"] == 62.5 and c.at["1001", "type"] == "BUY"
    assert c.at["1002", "symbol"] == "AAPL.US" and c.at["1002", "volume"] == 2.5
    assert c.at["1002", "open_price"] == 1180.25 and c.at["1002", "commission"] == -1.5
    assert c.at["1003", "type"] == "SELL" and c.at["1003", "swap"] == -3.2
    assert c.at["1001", "close_time"] == pd.Timestamp("2025-02-14 15:01:10")
    assert exp.cash["amount"].tolist() == [10000.0, -625.0, 41.5, -7.89]
    assert exp.cash["kind"].tolist() == ["DEPOSIT", "STOCK_PURCHASE", "DIVIDEND", "WHT"]
    # symbol dywidendy wyciągnięty z komentarza
    assert exp.cash["symbol"].tolist() == ["", "PKN.PL", "PKN.PL", "PKN.PL"]
    assert any("otwartych pozycji" in n for n in exp.notes)


def test_missing_columns_message():
    bad = "Pozycja;Symbol;Typ;Wolumen;Czas otwarcia;Czas zamknięcia;Cena zamknięcia\n1;PKN.PL;Kupno;1;02.01.2025;03.01.2025;10\n"
    with pytest.raises(XTBFormatError) as err:
        parse_export([("zamkniete.csv", bad.encode()), ("gotowka.csv", PL_CASH.encode())])
    msg = str(err.value)
    assert "Cena otwarcia" in msg and "Zamknięte pozycje" in msg and "Kolumny znalezione" in msg


def test_missing_cash_table():
    with pytest.raises(XTBFormatError) as err:
        parse_export([("zamkniete.csv", PL_CLOSED.encode())])
    assert "operacji gotówkowych" in str(err.value)


def test_unrecognised_file():
    with pytest.raises(XTBFormatError) as err:
        parse_export([("cos.csv", b"a;b;c\n1;2;3\n")])
    assert "Nie rozpoznano" in str(err.value)
    with pytest.raises(XTBFormatError):
        parse_export([("plik.pdf", b"%PDF")])


def test_xlsx_with_string_cells(tmp_path):
    """Excel, w którym wszystko jest tekstem (zdarza się przy eksporcie przez inne narzędzia)."""
    import csv
    closed = pd.DataFrame(list(csv.reader(io.StringIO(PL_CLOSED), delimiter=";")))
    cash = pd.DataFrame(list(csv.reader(io.StringIO(PL_CASH), delimiter=";")))
    path = tmp_path / "raport.xlsx"
    with pd.ExcelWriter(path) as w:
        closed.to_excel(w, sheet_name="Zamknięte pozycje", header=False, index=False)
        cash.to_excel(w, sheet_name="Operacje gotówkowe", header=False, index=False)
    exp = parse_export(str(path))
    assert len(exp.closed) == 3 and len(exp.cash) == 4
    assert exp.closed["gross_pl"].sum() == pytest.approx(331.30)


def test_mapping_rules():
    assert auto_map("PKN.PL")["yf_ticker"] == "PKN.WA" and auto_map("PKN.PL")["stooq_ticker"] == "pkn"
    a = auto_map("AAPL.US")
    assert a["yf_ticker"] == "AAPL" and a["stooq_ticker"] == "aapl.us" and a["currency"] == "USD"
    assert auto_map("SAP.DE")["yf_ticker"] == "SAP.DE" and auto_map("SAP.DE")["currency"] == "EUR"
    c = auto_map("US500")
    assert c["asset_class"] == "CFD" and c["multiplier"] == 50 and c["yf_ticker"] == "^GSPC"
    assert auto_map("CSPX.UK")["currency"] == "USD" and auto_map("CSPX.UK")["asset_class"] == "ETF"
    assert auto_map("BARC.UK")["price_factor"] == 0.01           # pensy -> funty
    assert auto_map("TSLA.US_4")["asset_class"] == "CFD"


def test_mapping_overrides():
    ov = pd.DataFrame([{"symbol": "PKN.PL", "yf_ticker": "XXX.WA", "sector": "Moje", "currency": "pln"}])
    mp = build_mapping(["PKN.PL", "AAPL.US"], ov).set_index("symbol")
    assert mp.at["PKN.PL", "yf_ticker"] == "XXX.WA" and mp.at["PKN.PL", "sector"] == "Moje"
    assert mp.at["PKN.PL", "currency"] == "PLN" and mp.at["AAPL.US", "yf_ticker"] == "AAPL"
