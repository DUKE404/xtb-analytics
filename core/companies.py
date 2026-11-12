"""Lista spółek: zmiana dzienna, średnia cena zakupu, kwartał obrotowy raportu, szerokość ruchu w portfelu."""
from __future__ import annotations

import calendar as _cal

import numpy as np
import pandas as pd


def day_change(price, prev_close) -> float:
    """Zmiana od poprzedniego zamknięcia (ułamek); NaN, gdy brak którejś ceny."""
    try:
        price, prev_close = float(price), float(prev_close)
    except (TypeError, ValueError):
        return float("nan")
    return price / prev_close - 1.0 if prev_close > 0 and price > 0 else float("nan")


def quote_from_history(closes: pd.Series) -> dict | None:
    """Notowanie z dwóch ostatnich zamknięć - zapas, gdy nie ma bieżącej ceny ze źródła."""
    s = closes.dropna()
    if len(s) < 2:
        return None
    return {"price": float(s.iloc[-1]), "prev_close": float(s.iloc[-2]), "time": s.index[-1].strftime("%Y-%m-%d"),
            "source": "zamknięcie sesji"}


def avg_open_price(open_positions: pd.DataFrame) -> pd.Series:
    """Średnia cena otwarcia per instrument, ważona wolumenem (BUY i SELL nie są mieszane: liczy się przewaga)."""
    if not len(open_positions):
        return pd.Series(dtype=float)
    o = open_positions[["symbol", "type", "volume", "open_price"]].copy()
    o["signed"] = np.where(o["type"] == "SELL", -1.0, 1.0) * o["volume"]
    o["cost"] = o["signed"] * o["open_price"]
    g = o.groupby("symbol")[["signed", "cost"]].sum()
    return (g["cost"] / g["signed"]).where(g["signed"].abs() > 1e-12)


def since_purchase(price, avg_open, side: float = 1.0) -> float:
    """Wynik pozycji od ceny zakupu w procentach ceny (dla krótkiej pozycji ze znakiem odwróconym)."""
    try:
        price, avg_open = float(price), float(avg_open)
    except (TypeError, ValueError):
        return float("nan")
    if not (avg_open > 0 and price > 0):
        return float("nan")
    return (price / avg_open - 1.0) * (1.0 if side >= 0 else -1.0)


def off_high(closes: pd.Series, price=None, window: int = 252) -> float:
    """Odległość ceny od najwyższego zamknięcia z ostatnich `window` sesji (0 = na szczycie, -0,2 = 20% poniżej)."""
    s = closes.dropna().iloc[-window:]
    if not len(s):
        return float("nan")
    last = float(price) if price is not None and price == price and price > 0 else float(s.iloc[-1])
    top = max(float(s.max()), last)
    return last / top - 1.0


def breadth(changes: pd.Series, weights: pd.Series) -> dict:
    """Ile pozycji rośnie, ile spada i jaki jest ich łączny wkład w zmianę kapitału (suma waga x zmiana)."""
    c = changes.dropna()
    w = weights.reindex(c.index).fillna(0.0)
    return {"up": int((c > 0).sum()), "down": int((c < 0).sum()), "flat": int((c == 0).sum()), "n": int(len(c)),
            "contribution": float((c * w).sum()),
            "best": c.idxmax() if len(c) else None, "worst": c.idxmin() if len(c) else None}


def fiscal_period(report_date, fy_end_month: int | None) -> dict:
    """Jaki kwartał obejmuje raport opublikowany `report_date`, przy roku obrotowym kończącym się w `fy_end_month`.

    Kwartał to ostatni koniec kwartału obrotowego co najmniej 10 dni przed publikacją. Kwartał kończący rok
    obrotowy (Q4) to jednocześnie wyniki roczne.
    """
    d = pd.Timestamp(report_date).normalize()
    fy = int(fy_end_month) if fy_end_month and 1 <= int(fy_end_month) <= 12 else 12
    ends = {((fy - 1 - 3 * k) % 12) + 1 for k in range(4)}
    cut = d - pd.Timedelta(days=10)
    y, m = cut.year, cut.month
    if pd.Timestamp(year=y, month=m, day=_cal.monthrange(y, m)[1]) > cut:
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    while m not in ends:
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    q = ((m - fy) % 12) // 3
    q = 4 if q == 0 else q
    fiscal_year = y if m <= fy else y + 1
    end = pd.Timestamp(year=y, month=m, day=_cal.monthrange(y, m)[1])
    return {"quarter": q, "fiscal_year": fiscal_year, "period_end": end, "annual": q == 4,
            "label": (f"roczny {fiscal_year} (Q4)" if q == 4 else f"Q{q} {fiscal_year}") + ("" if fy == 12 else " r. obr.")}
