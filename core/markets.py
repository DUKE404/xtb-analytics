"""Obliczenia do zakładki „Rynki”: zmiany notowań w oknach, migawki krzywej rentowności, spready, kalendarz.

Czyste funkcje na pandas - bez sieci i bez Flaska.
"""
from __future__ import annotations

import math

import pandas as pd

# okna porównań: klucz -> (etykieta, przesunięcie od ostatniej obserwacji)
WINDOWS = {"d1": "Dzień", "w1": "Tydzień", "m1": "Miesiąc", "ytd": "Od początku roku", "y1": "12 mies."}


def value_on_or_before(s: pd.Series, when: pd.Timestamp):
    """Ostatnia znana wartość z dnia `when` albo wcześniej (weekendy i święta biorą poprzednią sesję)."""
    s = s.dropna()
    s = s.loc[:pd.Timestamp(when)]
    return float(s.iloc[-1]) if len(s) else math.nan


def reference_dates(last: pd.Timestamp) -> dict:
    last = pd.Timestamp(last).normalize()
    return {"w1": last - pd.Timedelta(days=7), "m1": last - pd.DateOffset(months=1),
            "ytd": pd.Timestamp(year=last.year - 1, month=12, day=31), "y1": last - pd.DateOffset(years=1)}


def changes(s: pd.Series, diff: bool = False) -> dict:
    """Ostatnia wartość i zmiany: d1 (od poprzedniej sesji), w1, m1, ytd, y1.

    `diff=False`: zmiana względna (0.012 = +1,2%); `diff=True`: różnica poziomów (np. rentowności w pp)."""
    s = s.dropna().sort_index()
    out = {"last": math.nan, "date": None, **{k: math.nan for k in WINDOWS}}
    if not len(s):
        return out
    last, when = float(s.iloc[-1]), s.index[-1]
    out["last"], out["date"] = last, when

    def ch(ref):
        if not math.isfinite(ref):
            return math.nan
        if diff:
            return last - ref
        return last / ref - 1 if ref else math.nan

    out["d1"] = ch(float(s.iloc[-2])) if len(s) >= 2 else math.nan
    first = s.index[0]
    for k, d in reference_dates(when).items():
        out[k] = ch(value_on_or_before(s, d)) if d >= first else math.nan
    return out


def curve_snapshots(yields: pd.DataFrame) -> dict:
    """Krzywa dziś, tydzień i miesiąc temu: {etykieta: (data, Series zapadalność -> %)}."""
    y = yields.dropna(how="all").sort_index()
    if not len(y):
        return {}
    last = y.index[-1]
    refs = {"Dziś": last, "Tydzień temu": last - pd.Timedelta(days=7), "Miesiąc temu": last - pd.DateOffset(months=1)}
    out = {}
    for label, d in refs.items():
        part = y.loc[:d]
        if len(part):
            out[label] = (part.index[-1], part.iloc[-1])
    return out


def spread(yields: pd.DataFrame, long: str, short: str) -> pd.Series:
    if long not in yields.columns or short not in yields.columns:
        return pd.Series(dtype=float)
    return (yields[long] - yields[short]).dropna()


def curve_shape(row: pd.Series) -> str:
    """Opis kształtu krzywej po spreadach 10Y-2Y i 10Y-3M (w punktach procentowych)."""
    s2 = row.get("10Y", math.nan) - row.get("2Y", math.nan)
    s3 = row.get("10Y", math.nan) - row.get("3M", math.nan)
    vals = [v for v in (s2, s3) if isinstance(v, float) and math.isfinite(v)]
    if not vals:
        return "brak danych"
    if all(v < -0.05 for v in vals):
        return "odwrócona"
    if any(v < -0.05 for v in vals):
        return "częściowo odwrócona"
    if all(v > 0.25 for v in vals):
        return "rosnąca (normalna)"
    if max(vals) > 0.25:
        return "lekko rosnąca"
    return "płaska"


def vix_regime(v: float) -> str:
    if not math.isfinite(v):
        return "brak danych"
    if v < 15:
        return "spokój na rynku"
    if v < 20:
        return "zwykła zmienność"
    if v < 30:
        return "podwyższona nerwowość"
    return "silny stres"


def upcoming(events: list[dict], now: pd.Timestamp, countries: set[str] | None, impacts: set[str],
             lookback_hours: float = 2.0) -> list[dict]:
    """Wydarzenia od (teraz − kilka godzin), wybrane kraje i ważności, rosnąco po czasie."""
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    start = now - pd.Timedelta(hours=lookback_hours)
    out = []
    for e in events or []:
        t = pd.Timestamp(e["time"])
        t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
        if t < start:
            continue
        if countries and e.get("country") not in countries:
            continue
        if e.get("impact") not in impacts:
            continue
        out.append({**e, "ts": t})
    out.sort(key=lambda e: e["ts"])
    return out
