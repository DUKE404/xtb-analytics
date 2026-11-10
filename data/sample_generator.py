"""Generator realistycznego, przykładowego eksportu xStation 5 (ok. 2 lata historii).

Układ pliku naśladuje prawdziwy raport: kilka wierszy z danymi konta, nagłówek tabeli
w 11. wierszu od kolumny B, arkusze CLOSED POSITION HISTORY / OPEN POSITION ddmmrrrr /
CASH OPERATION HISTORY. Ceny pochodzą z syntetycznego rynku (data/synthetic.py), więc
plik jest spójny z notowaniami używanymi w trybie demo.

W zachowaniu "tradera" celowo zaszyto dwa wzorce, które powinna wykryć analityka:
większe pozycje CFD po stracie oraz szybsze realizowanie zysków niż strat.
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from data.mapping import build_mapping
from data.synthetic import SyntheticMarket

STOCK_WEIGHTS = {
    "EUNL.DE": 3.0, "VWCE.DE": 2.5, "CSPX.UK": 2.5, "ETFBW20TR.PL": 1.5,
    "PKN.PL": 1.2, "PKO.PL": 1.2, "PZU.PL": 1.0, "KGH.PL": 0.8, "CDR.PL": 1.0, "DNP.PL": 0.6, "ALE.PL": 0.7,
    "AAPL.US": 1.4, "MSFT.US": 1.4, "NVDA.US": 1.2, "AMZN.US": 1.0, "KO.US": 0.8, "JNJ.US": 0.7, "XOM.US": 0.6,
}
FRACTIONAL = {"EUNL.DE", "VWCE.DE", "CSPX.UK", "NVDA.US", "MSFT.US"}
# dywidendy: symbol -> lista (miesiąc, dzień, kwota na akcję w walucie instrumentu)
DIVIDENDS = {
    "AAPL.US": [(2, 13, 0.25), (5, 15, 0.26), (8, 14, 0.26), (11, 13, 0.26)],
    "MSFT.US": [(3, 13, 0.83), (6, 12, 0.83), (9, 11, 0.83), (12, 11, 0.91)],
    "KO.US": [(4, 1, 0.51), (7, 1, 0.51), (10, 1, 0.51), (12, 15, 0.51)],
    "JNJ.US": [(3, 4, 1.24), (6, 10, 1.30), (9, 9, 1.30), (12, 9, 1.30)],
    "XOM.US": [(3, 10, 0.99), (6, 10, 0.99), (9, 10, 0.99), (12, 10, 0.99)],
    "NVDA.US": [(3, 27, 0.01), (6, 26, 0.01), (9, 25, 0.01), (12, 23, 0.01)],
    "PKN.PL": [(9, 1, 6.00)], "PKO.PL": [(8, 14, 5.48)], "PZU.PL": [(10, 14, 4.47)],
    "KGH.PL": [(7, 3, 1.50)], "CDR.PL": [(7, 2, 1.00)],
}
CFD_VOLUMES = {"US500": (0.01, 0.03), "US100": (0.01, 0.03), "DE40": (0.01, 0.02), "W20": (0.1, 0.4), "GOLD": (0.01, 0.03)}
CFD_WEIGHTS = {"US500": 3, "US100": 2.5, "DE40": 2, "W20": 1.5, "GOLD": 1.5}
SWAP_RATE = {"BUY": -0.065, "SELL": -0.010}  # rocznie, od wartości nominalnej
FX_CONVERSION_FEE = 0.005                    # 0,5% przy transakcjach na akcjach w obcej walucie


def _first_bday_on_or_after(dates: pd.DatetimeIndex, year: int, month: int, day: int):
    try:
        target = pd.Timestamp(year, month, day)
    except ValueError:
        return None
    i = dates.searchsorted(target)
    return dates[i] if i < len(dates) and dates[i].month == month else None


def generate(seed: int = 12, start: str = "2024-11-04", as_of: str = "2026-11-06",
             market: SyntheticMarket | None = None) -> dict:
    """Zwraca dict z ramkami `closed`, `open`, `cash` (kolumny jak w raporcie XTB, EN) i datą `as_of`."""
    rng = np.random.default_rng(seed)
    mkt = market or SyntheticMarket(end=as_of)
    symbols = list(STOCK_WEIGHTS) + list(CFD_VOLUMES)
    mp = build_mapping(symbols).set_index("symbol")
    dates = pd.bdate_range(start, as_of)
    px = {s: mkt.asset(s)["close"] for s in symbols}
    fx = mkt.fx

    def fxr(sym, d):
        return float(fx.at[d, mp.at[sym, "currency"]])

    cash = 0.0
    cash_rows, closed_rows = [], []
    lots, cfds = [], []            # otwarte pozycje akcyjne i CFD
    counters = {"pos": 1_650_000_000, "op": 880_000_000}
    last_cfd_loss = False

    def nid(kind):
        counters[kind] += int(rng.integers(3, 900))
        return counters[kind]

    def op(t, typ, amount, comment="", symbol=""):
        nonlocal cash
        amount = round(float(amount), 2)
        if amount == 0:
            return
        cash += amount
        cash_rows.append({"ID": nid("op"), "Type": typ, "Time": t, "Comment": comment, "Symbol": symbol, "Amount": amount})

    def tstamp(d, lo_h, hi_h):
        return d + pd.Timedelta(hours=int(rng.integers(lo_h, hi_h)), minutes=int(rng.integers(0, 60)),
                                seconds=int(rng.integers(0, 60)))

    def session_hours(sym):
        cur_suffix = sym.split(".")[-1] if "." in sym else ""
        return (15, 22) if cur_suffix == "US" else (9, 17)

    special = {pd.Timestamp("2025-06-16"): 20000.0, pd.Timestamp("2026-02-18"): -8000.0}
    month_seen = set()
    stock_syms = list(STOCK_WEIGHTS)
    stock_p = np.array(list(STOCK_WEIGHTS.values())) / sum(STOCK_WEIGHTS.values())
    cfd_syms = list(CFD_WEIGHTS)
    cfd_p = np.array(list(CFD_WEIGHTS.values())) / sum(CFD_WEIGHTS.values())

    for di, d in enumerate(dates):
        # 1. wpłaty / wypłaty
        if di == 0:
            op(tstamp(d, 8, 9), "deposit", 50000.0, "Deposit - bank transfer")
        ym = (d.year, d.month)
        if d.day >= 10 and ym not in month_seen and di > 5:
            month_seen.add(ym)
            op(tstamp(d, 8, 10), "deposit", 2500.0, "Deposit - BLIK")
        if d in special:
            amt = special[d]
            op(tstamp(d, 8, 10), "deposit" if amt > 0 else "withdrawal", amt,
               "Deposit - bank transfer" if amt > 0 else "Withdrawal to bank account")

        # 2. dywidendy (wg stanu posiadania z końca poprzedniego dnia)
        for sym, sched in DIVIDENDS.items():
            for (mo, day, dps) in sched:
                if _first_bday_on_or_after(dates, d.year, mo, day) == d:
                    qty = sum(l["qty"] for l in lots if l["symbol"] == sym and l["open_time"].normalize() < d)
                    if qty > 0:
                        cur = mp.at[sym, "currency"]
                        gross = qty * dps * fxr(sym, d)
                        wht = 0.19 if cur == "PLN" else 0.15
                        t = tstamp(d, 10, 12)
                        op(t, "DIVIDENT", gross, f"{sym} {cur} {dps:.4f}/ SHR", sym)
                        op(t, "Withholding Tax", -gross * wht, f"{sym} {cur} WHT {int(wht * 100)}%", sym)

        # 3. sprzedaże akcji (raz w tygodniu, w piątek) - zyski realizowane chętniej niż straty
        if d.dayofweek == 4:
            for lot in list(lots):
                if (d - lot["open_time"].normalize()).days < 10:
                    continue
                price = float(px[lot["symbol"]].at[d])
                ret = price / lot["open_price"] - 1
                p = 0.10 if ret > 0.30 else 0.05 if ret > 0.15 else 0.004 if ret < -0.15 else 0.005
                if rng.random() < p:
                    sym = lot["symbol"]
                    cprice = round(price * (1 + rng.normal(0, 0.002)), 2)
                    foreign = mp.at[sym, "currency"] != "PLN"
                    sale = round(lot["qty"] * cprice * fxr(sym, d) * (1 - FX_CONVERSION_FEE * foreign), 2)
                    t = tstamp(d, *session_hours(sym))
                    op(t, "Stock sale", sale, f"CLOSE BUY {lot['qty']:g} @ {cprice}", sym)
                    closed_rows.append({
                        "Position": lot["id"], "Symbol": sym, "Type": "BUY", "Volume": lot["qty"],
                        "Open time": lot["open_time"], "Open price": lot["open_price"], "Close time": t,
                        "Close price": cprice, "Open origin": "mobile", "Close origin": "mobile",
                        "Purchase value": lot["purchase_value"], "Sale value": sale, "SL": 0, "TP": 0,
                        "Margin": lot["purchase_value"], "Commission": 0.0, "Swap": 0.0, "Rollover": 0.0,
                        "Gross P/L": round(sale - lot["purchase_value"], 2), "Comment": ""})
                    lots.remove(lot)

        # 4. zakupy akcji / ETF
        if rng.random() < (0.11 if cash < 15000 else 0.30) and cash > 6500:
            sym = stock_syms[int(rng.choice(len(stock_syms), p=stock_p))]
            price = round(float(px[sym].at[d]) * (1 + rng.normal(0, 0.002)), 2)
            foreign = mp.at[sym, "currency"] != "PLN"
            unit = price * fxr(sym, d) * (1 + FX_CONVERSION_FEE * foreign)
            budget = min(rng.uniform(2000, 7000) * (1.4 if cash > 15000 else 1.0), cash - 4000)
            qty = budget / unit
            qty = round(qty, 4) if sym in FRACTIONAL else float(np.floor(qty))
            if qty > 0:
                value = round(qty * unit, 2)
                t = tstamp(d, *session_hours(sym))
                pid = nid("pos")
                op(t, "Stock purchase", -value, f"OPEN BUY {qty:g} @ {price}", sym)
                lots.append({"id": pid, "symbol": sym, "qty": qty, "open_time": t, "open_price": price,
                             "purchase_value": value})

        # 5. CFD: zamknięcia pozycji wielodniowych
        for c in list(cfds):
            held = (d - c["open_time"].normalize()).days
            if held < 1:
                continue
            price = float(px[c["symbol"]].at[d])
            ret = c["dir"] * (price / c["open_price"] - 1)
            p = 0.60 if ret > 0.012 else 0.15 if ret < -0.015 else 0.12
            if (held >= 25 or rng.random() < p) and di < len(dates) - 5:
                last_cfd_loss = _close_cfd(c, d, price, rng, mp, fxr, op, closed_rows, tstamp) < 0
                cfds.remove(c)

        # 6. CFD: nowe pozycje
        forced = {len(dates) - 4: "US500", len(dates) - 2: "W20"}.get(di)  # 2 pozycje CFD otwarte na koniec
        if forced or (rng.random() < 0.14 and len(cfds) < 3 and di > 3 and cash > 3000 and di < len(dates) - 5):
            sym = forced or cfd_syms[int(rng.choice(len(cfd_syms), p=cfd_p))]
            lo, hi = CFD_VOLUMES[sym]
            vol = rng.uniform(lo, hi) * (1.7 if last_cfd_loss else 1.0)
            vol = round(vol, 2) if sym != "W20" else round(vol, 1)
            vol = max(vol, 0.01)
            direction = 1 if rng.random() < 0.65 else -1
            if forced:
                direction = 1 if forced == "US500" else -1
            prev_close = float(px[sym].iloc[max(px[sym].index.get_loc(d) - 1, 0)])
            intraday = rng.random() < 0.35 and not forced
            digits = 1 if prev_close > 1000 else 2
            mult = float(mp.at[sym, "multiplier"])
            if intraday:
                oprice = round(prev_close * (1 + rng.normal(0, 0.003)), digits)
                t_open = tstamp(d, 9, 12)
            else:
                oprice = round(float(px[sym].at[d]) * (1 + rng.normal(0, 0.001)), digits)
                t_open = tstamp(d, 15, 20)
            nominal = vol * mult * oprice * fxr(sym, d)
            c = {"id": nid("pos"), "symbol": sym, "dir": direction, "vol": vol, "open_time": t_open,
                 "open_price": oprice, "margin": round(nominal * float(mp.at[sym, "margin_rate"]), 2),
                 "swap": 0.0, "digits": digits}
            if intraday:
                last_cfd_loss = _close_cfd(c, d, float(px[sym].at[d]), rng, mp, fxr, op, closed_rows, tstamp,
                                           after=t_open) < 0
            else:
                cfds.append(c)

        # 7. swapy za utrzymanie pozycji przez noc (w piątek za 3 noce)
        for c in cfds:
            nights = 3 if d.dayofweek == 4 else 1
            nominal = c["vol"] * float(mp.at[c["symbol"], "multiplier"]) * float(px[c["symbol"]].at[d]) * fxr(c["symbol"], d)
            rate = SWAP_RATE["BUY" if c["dir"] > 0 else "SELL"]
            sw = round(nominal * rate / 365 * nights, 2)
            if sw != 0:
                c["swap"] = round(c["swap"] + sw, 2)
                op(d + pd.Timedelta(hours=23, minutes=59, seconds=30), "swap", sw, f"Swap for position {c['id']}", c["symbol"])

        # 8. odsetki od wolnych środków (ostatni dzień roboczy miesiąca)
        nxt = dates[di + 1] if di + 1 < len(dates) else None
        if nxt is not None and nxt.month != d.month and cash > 0:
            interest = round(cash * 0.02 / 12, 2)
            t = d + pd.Timedelta(hours=23, minutes=30)
            op(t, "Free-funds Interest", interest, f"Free-funds Interest {d.strftime('%Y-%m')}")
            op(t, "Free-funds Interest Tax", -round(interest * 0.19, 2), f"Free-funds Interest Tax {d.strftime('%Y-%m')}")

    # --- stan końcowy: pozycje otwarte
    last = dates[-1]
    open_rows = []
    for lot in lots:
        sym = lot["symbol"]
        mprice = float(px[sym].at[last])
        value = lot["qty"] * mprice * fxr(sym, last)
        open_rows.append({"Position": lot["id"], "Symbol": sym, "Type": "BUY", "Volume": lot["qty"],
                          "Open time": lot["open_time"], "Open price": lot["open_price"], "Market price": mprice,
                          "Purchase value": lot["purchase_value"], "SL": 0, "TP": 0,
                          "Margin": lot["purchase_value"], "Commission": 0.0, "Swap": 0.0, "Rollover": 0.0,
                          "Gross P/L": round(value - lot["purchase_value"], 2), "Comment": ""})
    for c in cfds:
        sym = c["symbol"]
        mprice = float(px[sym].at[last])
        pl = c["dir"] * c["vol"] * float(mp.at[sym, "multiplier"]) * (mprice - c["open_price"]) * fxr(sym, last)
        open_rows.append({"Position": c["id"], "Symbol": sym, "Type": "BUY" if c["dir"] > 0 else "SELL",
                          "Volume": c["vol"], "Open time": c["open_time"], "Open price": c["open_price"],
                          "Market price": mprice, "Purchase value": 0.0, "SL": 0, "TP": 0, "Margin": c["margin"],
                          "Commission": 0.0, "Swap": c["swap"], "Rollover": 0.0, "Gross P/L": round(pl, 2), "Comment": ""})

    closed_cols = ["Position", "Symbol", "Type", "Volume", "Open time", "Open price", "Close time", "Close price",
                   "Open origin", "Close origin", "Purchase value", "Sale value", "SL", "TP", "Margin",
                   "Commission", "Swap", "Rollover", "Gross P/L", "Comment"]
    open_cols = ["Position", "Symbol", "Type", "Volume", "Open time", "Open price", "Market price",
                 "Purchase value", "SL", "TP", "Margin", "Commission", "Swap", "Rollover", "Gross P/L", "Comment"]
    cash_cols = ["ID", "Type", "Time", "Comment", "Symbol", "Amount"]
    return {
        "closed": pd.DataFrame(closed_rows, columns=closed_cols).sort_values("Close time").reset_index(drop=True),
        "open": pd.DataFrame(open_rows, columns=open_cols).sort_values("Open time").reset_index(drop=True),
        "cash": pd.DataFrame(cash_rows, columns=cash_cols).sort_values("Time").reset_index(drop=True),
        "as_of": last,
        "balance": round(cash, 2),
    }


def _close_cfd(c, d, ref_price, rng, mp, fxr, op, closed_rows, tstamp, after=None) -> float:
    sym = c["symbol"]
    cprice = round(ref_price * (1 + rng.normal(0, 0.001)), c["digits"])
    mult = float(mp.at[sym, "multiplier"])
    pl = round(c["dir"] * c["vol"] * mult * (cprice - c["open_price"]) * fxr(sym, d), 2)
    t = tstamp(d, 16, 22)
    if after is not None and t <= after:
        t = after + pd.Timedelta(minutes=int(rng.integers(20, 240)))
    op(t, "close trade", pl, f"Profit of position #{c['id']}" if pl >= 0 else f"Loss of position #{c['id']}", sym)
    closed_rows.append({
        "Position": c["id"], "Symbol": sym, "Type": "BUY" if c["dir"] > 0 else "SELL", "Volume": c["vol"],
        "Open time": c["open_time"], "Open price": c["open_price"], "Close time": t, "Close price": cprice,
        "Open origin": "xStation", "Close origin": "xStation", "Purchase value": 0.0, "Sale value": 0.0,
        "SL": 0, "TP": 0, "Margin": c["margin"], "Commission": 0.0, "Swap": c["swap"], "Rollover": 0.0,
        "Gross P/L": pl, "Comment": ""})
    return pl


# --------------------------------------------------------------------------- #
# Zapis w układzie raportu xStation 5
# --------------------------------------------------------------------------- #

def write_xlsx(data: dict, path: str) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    as_of = pd.Timestamp(data["as_of"])
    wb = Workbook()
    wb.remove(wb.active)
    equity = data["balance"] + float(data["open"]["Gross P/L"].sum()) + float(
        data["open"].loc[data["open"]["Purchase value"] > 0, "Purchase value"].sum())

    def sheet(title, df, total_cols=(), cash_strings=False):
        ws = wb.create_sheet(title[:31])
        ws["B2"] = "Name and surname"; ws["C2"] = "Account"; ws["D2"] = "Currency"; ws["E2"] = "Balance"
        ws["F2"] = "Equity"; ws["G2"] = "Margin"; ws["H2"] = "Free margin"
        ws["B3"] = "Jan Przykładowy"; ws["C3"] = 1234567; ws["D3"] = "PLN"; ws["E3"] = data["balance"]
        ws["F3"] = round(equity, 2); ws["G3"] = float(data["open"]["Margin"].where(data["open"]["Purchase value"] == 0, 0).sum())
        ws["B5"] = "Report"; ws["C5"] = title
        ws["B6"] = "Date"; ws["C6"] = as_of.strftime("%d.%m.%Y")
        ws["B8"] = "DANE PRZYKŁADOWE - wygenerowane syntetycznie, nie są to prawdziwe transakcje"
        hdr_row = 11
        for j, col in enumerate(df.columns, start=2):
            cell = ws.cell(row=hdr_row, column=j, value=col)
            cell.font = Font(bold=True)
        for i, rec in enumerate(df.itertuples(index=False), start=hdr_row + 1):
            for j, val in enumerate(rec, start=2):
                if isinstance(val, pd.Timestamp):
                    val = val.strftime("%d.%m.%Y %H:%M:%S") if cash_strings else val.to_pydatetime()
                elif isinstance(val, (np.floating, np.integer)):
                    val = val.item()
                ws.cell(row=i, column=j, value=val)
        if total_cols and len(df):
            r = hdr_row + 1 + len(df)
            ws.cell(row=r, column=2, value="Total")
            for col in total_cols:
                ws.cell(row=r, column=2 + list(df.columns).index(col), value=round(float(df[col].sum()), 2))
        for j in range(2, 2 + len(df.columns)):
            ws.column_dimensions[ws.cell(row=hdr_row, column=j).column_letter].width = 18

    sheet("CLOSED POSITION HISTORY", data["closed"], total_cols=("Commission", "Swap", "Rollover", "Gross P/L"))
    sheet(f"OPEN POSITION {as_of.strftime('%d%m%Y')}", data["open"], total_cols=("Swap", "Gross P/L"))
    sheet("CASH OPERATION HISTORY", data["cash"], total_cols=("Amount",), cash_strings=True)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    wb.save(path)
    return path


DEFAULT_SAMPLE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample", "xtb_sample_export.xlsx")
SAMPLE_AS_OF = "2026-11-06"


def ensure_sample(path: str = DEFAULT_SAMPLE_PATH) -> str:
    if not os.path.exists(path):
        write_xlsx(generate(), path)
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Generator przykładowego eksportu xStation 5")
    ap.add_argument("--out", default=DEFAULT_SAMPLE_PATH)
    ap.add_argument("--seed", type=int, default=12)
    args = ap.parse_args()
    data = generate(seed=args.seed)
    print(write_xlsx(data, args.out), "| zamknięte:", len(data["closed"]), "| otwarte:", len(data["open"]),
          "| operacje:", len(data["cash"]), "| saldo gotówki:", data["balance"])


if __name__ == "__main__":
    main()
