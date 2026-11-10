"""Klocki, z których serwer składa ekrany. Interfejs mobilny umie narysować każdy z nich,
więc nowy ekran to tylko nowa funkcja w Pythonie - bez zmian we froncie.

Rodzaje bloków: statement, metrics, chart, table, note, bullets, controls, section, download.
Formaty liczb (`fmt`): pln, pct, num, int, days, x (mnożnik), text.
Kolory serii: numer miejsca w palecie 0-7 albo nazwa: 'neutral', 'pos', 'neg'.
"""
from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np
import pandas as pd

from server.tooltips import TOOLTIPS

# stałe przypisanie kolorów do bytów (kolor idzie za nazwą, nie za kolejnością)
SERIES_COLORS = {"Portfel": 0, "WIG20": 1, "WIG": 2, "S&P 500 (w PLN)": 3, "MSCI World ETF (w PLN)": 4,
                 "Wpłaty netto": "neutral"}


def jsonable(obj):
    """Rekurencyjnie zamienia typy numpy/pandas na czysty JSON (NaN i nieskończoność -> null)."""
    if isinstance(obj, str):
        return obj.replace(" - ", " – ")      # półpauza w zdaniach zamiast łącznika
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        f = float(obj)
        return f if math.isfinite(f) else None
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return None if pd.isna(obj) else pd.Timestamp(obj).strftime("%Y-%m-%d")
    if obj is pd.NaT:
        return None
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [jsonable(v) for v in obj.tolist()]
    if isinstance(obj, pd.Series):
        return [jsonable(v) for v in obj.tolist()]
    if isinstance(obj, pd.DataFrame):
        return [jsonable(r) for r in obj.to_dict("records")]
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return str(obj)


def _dates(index) -> list[str]:
    return [pd.Timestamp(d).strftime("%Y-%m-%d") for d in index]


def _round(values, digits=6) -> list:
    return [None if (v is None or not math.isfinite(float(v))) else round(float(v), digits) for v in values]


# --------------------------------------------------------------------------- #
# Formatowanie po polsku (do zdań składanych na serwerze)
# --------------------------------------------------------------------------- #

def f_num(x, digits: int = 2) -> str:
    if x is None or not math.isfinite(float(x)):
        return "–"
    s = f"{float(x):,.{digits}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def f_pln(x, digits: int = 0) -> str:
    return "–" if x is None or not math.isfinite(float(x)) else f"{f_num(x, digits)} zł"


def f_pct(x, digits: int = 1, sign: bool = False) -> str:
    if x is None or not math.isfinite(float(x)):
        return "–"
    s = f_num(float(x) * 100, digits) + "%"
    return "+" + s if sign and float(x) > 0 else s


# --------------------------------------------------------------------------- #
# Bloki
# --------------------------------------------------------------------------- #

def statement(text: str, sub: str | None = None) -> dict:
    """Jedno zdanie prostym językiem - otwarcie ekranu."""
    return {"kind": "statement", "text": text, "sub": sub}


def metric(label: str, value, fmt: str = "num", tip: str | None = None, digits: int | None = None,
           note: str | None = None, sign: bool = False) -> dict:
    """Wiersz z liczbą. `tip` to klucz ze słownika TOOLTIPS albo gotowy tekst objaśnienia."""
    return {"label": label, "value": value, "fmt": fmt, "digits": digits, "note": note, "sign": sign,
            "tip": TOOLTIPS.get(tip, tip) if tip else TOOLTIPS.get(label)}


def metrics(items: list[dict], title: str | None = None) -> dict:
    return {"kind": "metrics", "title": title, "items": items}


def note(text: str, tone: str = "info") -> dict:
    return {"kind": "note", "text": text, "tone": tone}


def bullets(items: list[str], title: str | None = None) -> dict:
    return {"kind": "bullets", "title": title, "items": items}


def section(title: str, blocks: list[dict], open_: bool = True) -> dict:
    return {"kind": "section", "title": title, "blocks": [b for b in blocks if b], "open": open_}


def download(label: str, path: str, filename: str) -> dict:
    return {"kind": "download", "label": label, "path": path, "filename": filename}


def control(name: str, type_: str, label: str, value, options: list | None = None, tip: str | None = None,
            **extra) -> dict:
    """type_: segmented | select | number | slider | toggle | text. options: [(wartość, etykieta), ...]."""
    out = {"name": name, "type": type_, "label": label, "value": value, "tip": tip, **extra}
    if options is not None:
        out["options"] = [{"value": o[0], "label": o[1]} if isinstance(o, (tuple, list)) else {"value": o, "label": str(o)}
                          for o in options]
    return out


def controls(items: list[dict], submit: str | None = None) -> dict:
    """Bez `submit` zmiana kontrolki od razu przelicza ekran; z `submit` dopiero po dotknięciu przycisku."""
    return {"kind": "controls", "items": items, "submit": submit}


def chart(spec: dict, title: str | None = None, caption: str | None = None) -> dict:
    return {"kind": "chart", "title": title, "caption": caption, "chart": spec}


def table(df: pd.DataFrame, columns: list[tuple], title: str | None = None, caption: str | None = None,
          limit: int = 12) -> dict:
    """columns: (kolumna w df, nagłówek, fmt[, cyfry]). Pierwsza kolumna jest przypięta przy przewijaniu."""
    cols = [{"key": c[0], "label": c[1], "fmt": c[2] if len(c) > 2 else "text",
             "digits": c[3] if len(c) > 3 else None} for c in columns]
    rows = df[[c["key"] for c in cols]].to_dict("records") if len(df) else []
    return {"kind": "table", "title": title, "caption": caption, "columns": cols, "rows": rows, "limit": limit}


# --------------------------------------------------------------------------- #
# Wykresy
# --------------------------------------------------------------------------- #

def line(df: pd.DataFrame, fmt: str = "num", digits: int | None = None, colors: dict | None = None,
         area: bool = False, zero: bool = False, max_points: int = 400) -> dict:
    """Linie na jednej osi. Długie serie są przerzedzane (ostatni punkt zawsze zostaje)."""
    if len(df) > max_points:
        step = int(math.ceil(len(df) / max_points))
        keep = sorted(set(range(0, len(df), step)) | {len(df) - 1})
        df = df.iloc[keep]
    colors = SERIES_COLORS | (colors or {})
    series = [{"name": str(c), "values": _round(df[c].values), "color": colors.get(str(c), i % 8)}
              for i, c in enumerate(df.columns)]
    return {"type": "line", "x": _dates(df.index), "series": series, "fmt": fmt, "digits": digits,
            "area": area, "zero": zero}


def hbar(series: pd.Series, fmt: str = "num", digits: int | None = None, signed: bool = False,
         color=0, limit: int | None = None) -> dict:
    """Poziome słupki w kolejności serii. `signed`: kolor koduje znak (plus / minus)."""
    s = series.dropna()
    if limit and len(s) > limit:
        s = s.iloc[:limit]
    return {"type": "hbar", "labels": [str(i) for i in s.index], "values": _round(s.values), "fmt": fmt,
            "digits": digits, "signed": signed, "color": color}


def hbar_grouped(df: pd.DataFrame, fmt: str = "pct", digits: int | None = None, colors: dict | None = None) -> dict:
    colors = colors or {}
    return {"type": "hbar", "labels": [str(i) for i in df.index], "fmt": fmt, "digits": digits,
            "series": [{"name": str(c), "values": _round(df[c].values), "color": colors.get(str(c), i)}
                       for i, c in enumerate(df.columns)]}


def bars(df: pd.DataFrame, fmt: str = "pct", digits: int | None = None, colors: dict | None = None) -> dict:
    """Pionowe słupki zgrupowane: wiersze = kategorie na osi X, kolumny = serie."""
    colors = SERIES_COLORS | (colors or {})
    return {"type": "bars", "labels": [str(i) for i in df.index], "fmt": fmt, "digits": digits,
            "series": [{"name": str(c), "values": _round(df[c].values), "color": colors.get(str(c), i % 8)}
                       for i, c in enumerate(df.columns)]}


def heatmap(df: pd.DataFrame, fmt: str = "num", digits: int | None = None, diverging: bool = True,
            labels: bool = False) -> dict:
    return {"type": "heatmap", "rows": [str(i) for i in df.index], "cols": [str(c) for c in df.columns],
            "z": [_round(r) for r in df.values], "fmt": fmt, "digits": digits, "diverging": diverging,
            "labels": labels}


def hist(values, bins: int = 40, fmt: str = "pln", digits: int | None = None, markers: list | None = None) -> dict:
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    counts, edges = np.histogram(v, bins=bins) if len(v) else (np.array([]), np.array([]))
    return {"type": "hist", "edges": _round(edges), "counts": [int(c) for c in counts], "fmt": fmt, "digits": digits,
            "markers": markers or []}


def fan(x, bands: list[tuple], median, fmt: str = "pln", digits: int | None = None, lines: list | None = None) -> dict:
    """Wachlarz: pasma percentyli [(etykieta, dolna, górna), ...] od najszerszego + mediana."""
    return {"type": "fan", "x": [str(v) for v in x],
            "bands": [{"label": b[0], "lo": _round(b[1]), "hi": _round(b[2])} for b in bands],
            "median": _round(median), "fmt": fmt, "digits": digits, "lines": lines or []}
