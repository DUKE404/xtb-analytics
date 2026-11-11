"""Kalendarz: dzienne stopy zwrotu ułożone w miesiące, serie wzrostów i spadków, terminy raportów spółek."""
from __future__ import annotations

import calendar as _cal

import numpy as np
import pandas as pd

MONTHS_PL = ["styczeń", "luty", "marzec", "kwiecień", "maj", "czerwiec", "lipiec", "sierpień", "wrzesień",
             "październik", "listopad", "grudzień"]
WEEKDAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
WEEKDAYS_SHORT = ["Pn", "Wt", "Śr", "Cz", "Pt"]


def month_grid(returns: pd.Series, pnl: pd.Series | None = None, bench: pd.Series | None = None,
               months: int | None = 6) -> list[dict]:
    """Miesiące z dziennymi stopami, najnowszy pierwszy. Dzień bez sesji = None.

    Każdy miesiąc: etykieta, rok, miesiąc, `lead` (dzień tygodnia 1. dnia, 0 = poniedziałek), dni i sumy.
    """
    r = returns.dropna()
    if not len(r):
        return []
    keys = sorted({(d.year, d.month) for d in r.index}, reverse=True)
    if months:
        keys = keys[:months]
    out = []
    for y, m in keys:
        sel = r[(r.index.year == y) & (r.index.month == m)]
        n = _cal.monthrange(y, m)[1]
        days = [None] * n
        for d, v in sel.items():
            days[d.day - 1] = {"v": float(v),
                               "pnl": float(pnl.get(d, np.nan)) if pnl is not None else None,
                               "b": float(bench.get(d, np.nan)) if bench is not None and d in bench.index else None}
        out.append({"label": f"{MONTHS_PL[m - 1]} {y}", "year": y, "month": m, "lead": _cal.weekday(y, m, 1),
                    "days": days, "total": float((1 + sel).prod() - 1),
                    "pnl_total": float(pnl.reindex(sel.index).sum()) if pnl is not None else None,
                    "up": int((sel > 0).sum()), "down": int((sel < 0).sum())})
    return out


def robust_scale(returns: pd.Series, q: float = 0.95) -> float:
    """Skala kolorów: 95. percentyl |stopy dziennej| - jeden skrajny dzień nie gasi pozostałych."""
    a = returns.dropna().abs()
    a = a[a > 0]
    return float(np.quantile(a, q)) if len(a) else 0.01


def streaks(returns: pd.Series) -> dict:
    """Bieżąca seria (dni na plusie > 0, na minusie < 0) i najdłuższe serie wzrostów i spadków."""
    s = np.sign(returns.dropna().values)
    s = s[s != 0]
    best_up = best_down = run = 0
    prev = 0
    for x in s:
        run = run + 1 if x == prev else 1
        prev = x
        if x > 0:
            best_up = max(best_up, run)
        else:
            best_down = max(best_down, run)
    current = int(run * prev) if len(s) else 0
    return {"current": current, "longest_up": int(best_up), "longest_down": int(best_down)}


def day_stats(returns: pd.Series) -> dict:
    r = returns.dropna()
    r = r[r != 0]
    if not len(r):
        return {"n": 0, "positive": np.nan, "avg_up": np.nan, "avg_down": np.nan, "best": np.nan, "worst": np.nan,
                "best_day": None, "worst_day": None, "win_loss": np.nan}
    up, down = r[r > 0], r[r < 0]
    return {"n": int(len(r)), "positive": float((r > 0).mean()),
            "avg_up": float(up.mean()) if len(up) else np.nan, "avg_down": float(down.mean()) if len(down) else np.nan,
            "best": float(r.max()), "worst": float(r.min()), "best_day": r.idxmax(), "worst_day": r.idxmin(),
            "win_loss": float(up.mean() / -down.mean()) if len(up) and len(down) else np.nan}


def by_weekday(returns: pd.Series) -> pd.Series:
    """Średnia stopa dzienna według dnia tygodnia (tylko dni sesyjne)."""
    r = returns.dropna()
    r = r[r != 0]
    g = r.groupby(r.index.weekday).mean()
    return pd.Series({WEEKDAYS_SHORT[i]: float(g.get(i, np.nan)) for i in range(5)})


def position_moves(pnl_row: pd.Series, exposure_prev: pd.Series) -> pd.DataFrame:
    """Wynik pozycji w danym dniu: kwota i stopa (wynik / wartość ekspozycji z poprzedniego dnia)."""
    base = exposure_prev.abs()
    keep = base[base > 1e-9].index
    pnl = pnl_row.reindex(keep).fillna(0.0)
    return pd.DataFrame({"pnl": pnl, "ret": pnl / base[keep]})


# --------------------------------------------------------------------------- terminy raportów

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
            "label": (f"wyniki roczne {fiscal_year} (Q4)" if q == 4 else f"wyniki Q{q} {fiscal_year}")
            + ("" if fy == 12 else " r. obr.")}
