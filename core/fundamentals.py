"""Fundamenty spółek: odwrócony DCF, wzrosty historyczne, odległość od cen docelowych analityków.

Wszystko tu jest arytmetyką na danych wejściowych - bez prognoz własnych i bez ocen "tanio / drogo".
"""
from __future__ import annotations

import math

import numpy as np


def dcf_value(fcf: float, growth: float, discount: float, terminal_growth: float, years: int = 10) -> float:
    """Wartość bieżąca: `years` lat przepływów rosnących w tempie `growth`, potem wzrost wieczny `terminal_growth`.

    Przepływy na koniec roku; wartość rezydualna wzorem Gordona. Wymaga discount > terminal_growth.
    """
    if discount <= terminal_growth:
        return float("nan")
    value, cf = 0.0, float(fcf)
    for t in range(1, years + 1):
        cf *= 1.0 + growth
        value += cf / (1.0 + discount) ** t
    terminal = cf * (1.0 + terminal_growth) / (discount - terminal_growth)
    return value + terminal / (1.0 + discount) ** years


def reverse_dcf(target_value: float, fcf: float, discount: float, terminal_growth: float, years: int = 10,
                lo: float = -0.60, hi: float = 1.50) -> float:
    """Jaki roczny wzrost przepływów przez `years` lat uzasadnia dzisiejszą wycenę `target_value`?

    Zwraca NaN, gdy pytanie nie ma sensu (ujemny przepływ, brak danych) albo odpowiedź leży poza [lo, hi].
    """
    if not (fcf and fcf > 0 and target_value and target_value > 0) or discount <= terminal_growth:
        return float("nan")
    f = lambda g: dcf_value(fcf, g, discount, terminal_growth, years) - target_value
    if f(lo) > 0 or f(hi) < 0:
        return float("nan")
    for _ in range(80):                       # wartość rośnie monotonicznie ze wzrostem -> bisekcja
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def cagr(values) -> float:
    """Średnioroczne tempo zmiany między pierwszą a ostatnią wartością szeregu rocznego (obie muszą być dodatnie)."""
    v = [float(x) for x in values if x is not None and x == x]
    if len(v) < 2 or v[0] <= 0 or v[-1] <= 0:
        return float("nan")
    return (v[-1] / v[0]) ** (1.0 / (len(v) - 1)) - 1.0


def implied_growth_grid(target_value: float, fcf: float, discounts, terminal_growths, years: int = 10) -> list[dict]:
    """Wrażliwość: wzrost wpisany w cenę dla kilku stóp dyskonta i wzrostów wiecznych."""
    return [{"discount": d, **{f"g{int(round(tg * 1000))}": reverse_dcf(target_value, fcf, d, tg, years)
                               for tg in terminal_growths}} for d in discounts]


def target_gap(price: float, target: float) -> float:
    """O ile cena docelowa jest wyżej (+) lub niżej (-) od bieżącej."""
    if not price or not target or price <= 0 or not math.isfinite(target):
        return float("nan")
    return target / price - 1.0


def net_debt(total_debt, total_cash) -> float:
    d = float(total_debt) if total_debt is not None and total_debt == total_debt else np.nan
    c = float(total_cash) if total_cash is not None and total_cash == total_cash else np.nan
    return d - c if np.isfinite(d) and np.isfinite(c) else float("nan")
