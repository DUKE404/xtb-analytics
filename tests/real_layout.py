"""Pliki testowe w układzie prawdziwego eksportu xStation 5 (2026): jeden .xlsx na rachunek, arkusze
"Closed Positions", "Open Positions", "Cash Operations", wiersze metadanych nad nagłówkiem, wiersze
zbiorcze per instrument w otwartych pozycjach i wiersz sumy na końcu zamkniętych.

Dwa rachunki tego samego klienta: PLN (50324864) i USD (51672516), z przelewami między nimi
("Currency conversion, PLN to USD from TA: 50324864 to: 51672516").
"""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook

PLN_ACC, USD_ACC = "50324864", "51672516"
REPORT_TIME = datetime(2026, 11, 11, 9, 59, 32)

CLOSED_HEADER = ["Instrument", "Ticker", "Category", "Type", "Volume", "Open Price", "Open Time (UTC)", "Close Price",
                 "Close Time (UTC)", "Product", "Profit/Loss", "Gross Profit", "Purchase Value", "Sale Value", "Stop Loss",
                 "Take Profit", "Commission", "Margin", "Swap", "Rollover", "Open Conversion Rate",
                 "Close Conversion Rate", "Close Origin", "Position ID", "Comment"]
OPEN_HEADER = ["Product", "Instrument/Position", "Ticker", "Category", "Type", "Volume", "Value", "Current price",
               "Open price", "Open time (UTC)", "Stop Loss", "Take Profit", "Net Profit %", "Net Profit", "Gross Profit",
               "Margin", "Open Commission", "Swap", "Rollover"]
CASH_HEADER = ["Type", "Instrument", "Ticker", "Category", "Time", "Amount", "ID", "Comment", "Product", "Position ID"]


def _closed_row(name, ticker, volume, open_price, open_time, close_price, close_time, rate_open, rate_close, pos_id,
                category="STOCK"):
    purchase = round(volume * open_price * rate_open, 2)
    sale = round(volume * close_price * rate_close, 2)
    pl = round(sale - purchase, 2)
    return [name, ticker, category, "BUY", volume, open_price, open_time, close_price, close_time, "My Trades", pl, pl,
            purchase, sale, None, None, 0, 0, 0, 0, rate_open, rate_close, "User", pos_id, None]


def _closed_sheet(wb, account, rows):
    ws = wb.create_sheet("Closed Positions")
    ws.append(["Account number", account])
    ws.append(["Closed Positions"])
    ws.append(["Date from (UTC)", datetime(2006, 1, 1)])
    ws.append(["Date to (UTC)", datetime(2026, 11, 11, 23, 59, 59)])
    ws.append(CLOSED_HEADER)
    for r in rows:
        ws.append(r)
    total = [None] * len(CLOSED_HEADER)
    total[0] = "Profit/loss"
    total[10] = round(sum(r[10] for r in rows), 2)
    total[11] = total[10]
    ws.append(total)


def _open_sheet(wb, account, currency, groups, text_numbers=False):
    """groups: [(nazwa, ticker, kategoria, [(id, wolumen, cena bieżąca, cena otwarcia, czas, kurs do waluty rachunku)])]"""
    ws = wb.create_sheet("Open Positions")
    body, total_value, total_profit = [], 0.0, 0.0
    for name, ticker, category, positions in groups:
        rows, vol, val, cost = [], 0.0, 0.0, 0.0
        for pid, volume, price, open_price, when, rate in positions:
            value = round(volume * price * rate, 2)
            profit = round(volume * (price - open_price) * rate, 2)
            pct = round((price / open_price - 1) * 100, 2)
            rows.append(["My Trades", pid, ticker, None, "BUY", volume, value, price, open_price, when, None, None,
                         pct, profit, profit, 0, 0, 0, 0])
            vol, val, cost = vol + volume, val + value, cost + volume * open_price
            total_profit += profit
        body.append(["My Trades", name, ticker, category, None, round(vol, 4), round(val, 2), None,
                     round(cost / vol, 2), None, None, None, None, None, None, None, None, None, None])
        body += rows
        total_value += val
    ws.append(["Account number", account])
    ws.append(["Open Positions"])
    ws.append(["Data as of report generated", REPORT_TIME])
    ws.append(["Product", "Metric", "Amount", "Currency"])
    ws.append(["My Trades", "Open position value", round(total_value, 2), currency])
    ws.append(["My Trades", "Open position profit", round(total_profit, 2), currency])
    ws.append([])
    ws.append(["Note", "Summary values are calculated for all open positions."])
    ws.append(OPEN_HEADER)
    for r in body:
        if text_numbers:     # liczby zapisane jako tekst z przecinkiem - tak też bywa w eksportach
            r = [str(v).replace(".", ",") if isinstance(v, float) else v for v in r]
        ws.append(r)


def _cash_sheet(wb, account, rows):
    ws = wb.create_sheet("Cash Operations")
    ws.append(["Account number", account])
    ws.append(["Cash Operations"])
    ws.append(["Date from (UTC)", datetime(2006, 1, 1)])
    ws.append(["Date to (UTC)", datetime(2026, 11, 11, 23, 59, 59)])
    ws.append(CASH_HEADER)
    for i, (typ, ticker, when, amount, comment) in enumerate(sorted(rows, key=lambda r: r[2], reverse=True)):
        ws.append([typ, None, ticker, "STOCK" if ticker else None, when, amount, 900000 + i, comment, "My Trades", None])


def _save(wb) -> bytes:
    del wb["Sheet"]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _transfer(pln, usd, when):
    comment = f"Currency conversion, PLN to USD from TA: {PLN_ACC} to: {USD_ACC}, Exchange rate:{usd / pln:.6f}"
    return ("Transfer", None, when, -pln, comment), ("Transfer", None, when, usd, comment)


T1 = _transfer(7800.00, 2000.00, datetime(2026, 4, 9, 10, 0, 5))
T2 = _transfer(3257.00, 835.64, datetime(2026, 10, 21, 17, 19, 51))
T3 = _transfer(4300.00, 1089.97, datetime(2026, 11, 10, 14, 31, 15))

# stan gotówki na koniec: suma kwot poniżej (sprawdzana w testach)
PLN_CASH = [
    ("Deposit", None, datetime(2026, 4, 1, 8, 15, 0), 30000.00, "Adyen BLIK deposit, id 1"),
    T1[0], T2[0], T3[0],
    ("Stock purchase", "NVO.US", datetime(2026, 5, 11, 16, 17, 25), -1017.31, "OPEN BUY NVO.US 6 @ 46.88"),
    ("Stock sell", "NVO.US", datetime(2026, 10, 21, 17, 19, 21), 908.37, "CLOSE BUY NVO.US 6 @ 39.25"),
    ("Dividend", "NVO.US", datetime(2026, 8, 20, 9, 0, 0), 21.60, "NVO.US USD 1/ SHR"),
    ("Withholding tax", "NVO.US", datetime(2026, 8, 20, 9, 0, 0), -5.83, "NVO.US USD WHT 27%"),
    ("Stock purchase", "META.US", datetime(2026, 6, 2, 14, 40, 0), -2110.42, "OPEN BUY META.US 0.9391 @ 600.10"),
    ("Stock purchase", "TSLA.US", datetime(2026, 6, 3, 14, 45, 0), -5412.00, "OPEN BUY TSLA.US 5 @ 290.00"),
    ("Stock purchase", "NBIS.US", datetime(2026, 7, 7, 15, 0, 0), -3690.00, "OPEN BUY NBIS.US 10 @ 100.00"),
    ("Free funds interest", None, datetime(2026, 11, 2, 6, 0, 0), 12.40, "Free-funds Interest 2026-10"),
    ("Free funds interest tax", None, datetime(2026, 11, 2, 6, 0, 0), -2.36, "Free-funds Interest Tax 2026-10"),
]
USD_CASH = [
    T1[1], T2[1], T3[1],
    ("Stock purchase", "SNOW.US", datetime(2026, 4, 10, 13, 34, 13), -129.44, "OPEN BUY SNOW.US 1 @ 129.44"),
    ("Stock sell", "SNOW.US", datetime(2026, 10, 29, 15, 33, 58), 328.99, "CLOSE BUY SNOW.US 1 @ 328.99"),
    ("SEC fee", "SNOW.US", datetime(2026, 10, 30, 6, 0, 0), -0.01, "Sec Fee adj SNOW.US 20261029"),
    ("Stock purchase", "ORCL.US", datetime(2026, 4, 14, 14, 0, 0), -700.00, "OPEN BUY ORCL.US 4 @ 175.00"),
    ("Dividend", "ORCL.US", datetime(2026, 7, 24, 9, 0, 0), 2.00, "ORCL.US USD 0.5/ SHR"),
    ("Withholding tax", "ORCL.US", datetime(2026, 7, 24, 9, 0, 0), -0.30, "ORCL.US USD WHT 15%"),
    ("Stock purchase", "RKLB.US", datetime(2026, 9, 24, 18, 30, 35), -1858.25, "OPEN BUY RKLB.US 25 @ 74.33"),
    ("Stock purchase", "RKLB.US", datetime(2026, 10, 2, 15, 0, 0), -537.50, "OPEN BUY RKLB.US 10 @ 53.75"),
    ("Stock purchase", "SMH.UK", datetime(2026, 11, 10, 14, 33, 10), -884.90, "OPEN BUY SMH.UK 10 @ 88.49"),
    ("Free funds interest", None, datetime(2026, 11, 2, 6, 0, 0), 0.52, "Free-funds Interest 2026-10"),
]


def pln_account(text_numbers: bool = False) -> tuple[str, bytes]:
    wb = Workbook()
    _closed_sheet(wb, PLN_ACC, [
        _closed_row("Novo Nordisk", "NVO.US", 6, 46.88, datetime(2026, 5, 11, 16, 17, 25), 39.25,
                    datetime(2026, 10, 21, 17, 19, 21), 3.6167, 3.8572, 2700000001),
    ])
    _cash_sheet(wb, PLN_ACC, PLN_CASH)
    _open_sheet(wb, PLN_ACC, "PLN", [
        ("Meta Platforms", "META.US", "STOCK", [(2800000001, 0.9391, 641.53, 600.10, datetime(2026, 6, 2, 14, 40, 0), 3.9320)]),
        ("Tesla", "TSLA.US", "STOCK", [(2800000002, 5, 345.00, 290.00, datetime(2026, 6, 3, 14, 45, 0), 3.9320)]),
        ("Nebius Group", "NBIS.US", "STOCK", [(2800000003, 10, 104.00, 100.00, datetime(2026, 7, 7, 15, 0, 0), 3.9320)]),
    ], text_numbers)
    return f"PLN_{PLN_ACC}_2006-01-01_2026-11-11.xlsx", _save(wb)


def usd_account(text_numbers: bool = False) -> tuple[str, bytes]:
    wb = Workbook()
    _closed_sheet(wb, USD_ACC, [
        _closed_row("Snowflake", "SNOW.US", 1, 129.44, "2026-04-10 13:34:13", 328.99, "2026-10-29 15:33:58", 1, 1, 2700000002),
    ])
    _cash_sheet(wb, USD_ACC, USD_CASH)
    _open_sheet(wb, USD_ACC, "USD", [
        ("Rocket Lab", "RKLB.US", "STOCK", [(2836382388, 25, 62.71, 74.33, datetime(2026, 9, 24, 18, 30, 35), 1),
                                            (2840000001, 10, 62.71, 53.75, datetime(2026, 10, 2, 15, 0, 0), 1)]),
        ("Oracle", "ORCL.US", "STOCK", [(2800000010, 4, 180.00, 175.00, datetime(2026, 4, 14, 14, 0, 0), 1)]),
        ("VanEck Semiconductor UCITS ETF", "SMH.UK", "ETF", [(2930797533, 10, 94.26, 88.49, datetime(2026, 11, 10, 14, 33, 10), 1)]),
    ], text_numbers)
    return f"USD_{USD_ACC}_2006-01-01_2026-11-11.xlsx", _save(wb)


PLN_CASH_END = round(sum(r[3] for r in PLN_CASH), 2)
USD_CASH_END = round(sum(r[3] for r in USD_CASH), 2)


# --------------------------------------------------------------------------- rachunek z CFD i akcją sprzed splitu

CFD_ACC = "50999001"


def _cfd_row(name, ticker, category, side, volume, open_price, open_time, close_price, close_time, mult, rate, margin_rate, pos_id):
    sgn = 1 if side == "BUY" else -1
    pl = round(sgn * volume * mult * (close_price - open_price) * rate, 2)
    margin = round(volume * mult * open_price * rate * margin_rate, 2)
    return [name, ticker, category, side, volume, open_price, open_time, close_price, close_time, "My Trades", pl, pl,
            None, None, None, None, 0, margin, 0, 0, rate, rate, "User", pos_id, None]


def cfd_account() -> tuple[str, bytes]:
    """Mały rachunek PLN: krótki CFD na akcję (ten sam ticker co akcja), CFD na indeks, CFD spoza wbudowanej tabeli
    oraz zwykła akcja kupiona i sprzedana po cenach sprzed splitu."""
    d = datetime
    cfds = [
        _cfd_row("Nvidia", "NVDA.US", "CFD", "SELL", 10, 900.0, d(2026, 3, 3, 15, 0), 950.0, d(2026, 3, 10, 15, 0), 1, 4.0, 0.2, 3100000001),
        _cfd_row("US100", "US100", "CFD", "BUY", 0.1, 18000.0, d(2026, 3, 12, 15, 0), 18200.0, d(2026, 3, 19, 15, 0), 20, 4.0, 0.05, 3100000002),
        _cfd_row("Bitcoin", "BITCOIN", "CRYPTO", "BUY", 0.05, 60000.0, d(2026, 3, 20, 15, 0), 66000.0, d(2026, 3, 27, 15, 0), 1, 4.0, 0.5, 3100000003),
    ]
    stock = _closed_row("Nvidia", "NVDA.US", 1, 900.0, d(2026, 4, 1, 15, 0), 1000.0, d(2026, 4, 15, 15, 0), 4.0, 4.0, 3100000004)
    cash = [("Deposit", None, d(2026, 3, 2, 9, 0), 5000.00, "Adyen BLIK deposit, id 2")]
    for row in cfds:
        cash.append(("Close trade", row[1], row[8], row[10], f"CLOSE {row[3]} {row[4]} {row[1]} @ {row[7]}"))
    cash += [("Stock purchase", "NVDA.US", d(2026, 4, 1, 15, 0), -3600.00, "OPEN BUY NVDA.US 1 @ 900.00"),
             ("Stock sell", "NVDA.US", d(2026, 4, 15, 15, 0), 4000.00, "CLOSE BUY NVDA.US 1 @ 1000.00")]
    wb = Workbook()
    _closed_sheet(wb, CFD_ACC, cfds + [stock])
    ws = wb.create_sheet("Cash Operations")
    for line in (["Account number", CFD_ACC], ["Cash Operations"], ["Date from (UTC)", d(2006, 1, 1)], ["Date to (UTC)", d(2026, 4, 30)], CASH_HEADER):
        ws.append(line)
    for i, (typ, ticker, when, amount, comment) in enumerate(cash):
        category = None if not ticker else ("STOCK" if typ.startswith("Stock") else "CFD")
        ws.append([typ, None, ticker, category, when, amount, 950000 + i, comment, "My Trades", None])
    return f"PLN_{CFD_ACC}_2006-01-01_2026-04-30.xlsx", _save(wb)
