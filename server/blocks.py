"""Klocki, z których serwer składa ekrany. Interfejs mobilny umie narysować każdy z nich,
więc nowy ekran to tylko nowa funkcja w Pythonie - bez zmian we froncie.

Rodzaje bloków: statement, metrics, chart, table, note, bullets, controls, section, download, feed.
Formaty liczb (`fmt`): pln (kwota w walucie wyświetlania), zl (zawsze złote), pct, num, int, days,
x (mnożnik), text.
Kolory serii: numer miejsca w palecie 0-7 albo nazwa: 'neutral', 'pos', 'neg'.
"""
from __future__ import annotations

import contextvars
import math
import re
from datetime import date, datetime

import numpy as np
import pandas as pd

from server.tooltips import TOOLTIPS

# stałe przypisanie kolorów do bytów (kolor idzie za nazwą, nie za kolejnością)
SERIES_COLORS = {"Portfel": 0, "WIG20": 1, "Nasdaq 100": 2, "S&P 500": 3, "MSCI World ETF": 4, "Wpłaty netto": "neutral"}
_CCY_SUFFIX = re.compile(r" \(w [A-Z]{3}\)$")


def _color(colors: dict, name: str, default):
    """Kolor idzie za bytem: "S&P 500 (w PLN)" i "S&P 500 (w USD)" to ta sama seria."""
    return colors.get(name, colors.get(_CCY_SUFFIX.sub("", name), default))


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


CURRENCY_SIGN = {"PLN": "zł", "USD": "$", "EUR": "€"}
_currency: contextvars.ContextVar[str] = contextvars.ContextVar("display_currency", default="PLN")


def set_currency(code: str) -> None:
    """Waluta wyświetlania dla bieżącego żądania - używa jej f_pln oraz format "pln" po stronie telefonu."""
    _currency.set(code if code in CURRENCY_SIGN else "PLN")


def currency() -> str:
    return _currency.get()


def cur_sign() -> str:
    return CURRENCY_SIGN[_currency.get()]


def _money(x, digits: int, sign: str) -> str:
    return "–" if x is None or not math.isfinite(float(x)) else f"{f_num(x, digits)} {sign}"


def f_pln(x, digits: int = 0) -> str:
    """Kwota w walucie wyświetlania (nazwa historyczna: dawniej zawsze złote)."""
    return _money(x, digits, cur_sign())


def f_zl(x, digits: int = 0) -> str:
    """Kwota zawsze w złotych (podatki)."""
    return _money(x, digits, "zł")


def f_pct(x, digits: int = 1, sign: bool = False) -> str:
    if x is None or not math.isfinite(float(x)):
        return "–"
    s = f_num(float(x) * 100, digits) + "%"
    return "+" + s if sign and float(x) > 0 else s


# --------------------------------------------------------------------------- #
# Bloki
# --------------------------------------------------------------------------- #

_ORPHAN = re.compile(r"(?<![^\s„(])([wziouaWZIOUA])\s+")


def no_orphans(text: str | None) -> str | None:
    """Polska typografia: jednoliterowe spójniki i przyimki nie zostają na końcu wiersza."""
    return _ORPHAN.sub("\\1\u00a0", text) if text else text


def statement(text: str, sub: str | None = None) -> dict:
    """Jedno zdanie prostym językiem - otwarcie ekranu."""
    return {"kind": "statement", "text": no_orphans(text), "sub": no_orphans(sub)}


def metric(label: str, value, fmt: str = "num", tip: str | None = None, digits: int | None = None,
           note: str | None = None, sign: bool = False) -> dict:
    """Wiersz z liczbą. `tip` to klucz ze słownika TOOLTIPS albo gotowy tekst objaśnienia."""
    return {"label": label, "value": value, "fmt": fmt, "digits": digits, "note": note, "sign": sign,
            "tip": TOOLTIPS.get(tip, tip) if tip else TOOLTIPS.get(label)}


def metrics(items: list[dict], title: str | None = None) -> dict:
    return {"kind": "metrics", "title": title, "items": items}


def note(text: str, tone: str = "info") -> dict:
    return {"kind": "note", "text": text, "tone": tone}


def feed(items: list[dict], title: str | None = None, caption: str | None = None) -> dict:
    """Lista nagłówków: {"title", "source", "time" (ISO, UTC), "url" (http/https albo pusty), "tag"}."""
    clean = []
    for it in items:
        url = str(it.get("url") or "")
        clean.append({"title": str(it.get("title") or ""), "source": it.get("source") or None, "time": it.get("time") or None,
                      "url": url if url.lower().startswith(("https://", "http://")) else None, "tag": it.get("tag") or None})
    return {"kind": "feed", "title": title, "caption": caption, "items": clean}


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


def controls(items: list[dict], submit: str | None = None, grid: bool = False) -> dict:
    """Bez `submit` zmiana kontrolki od razu przelicza ekran; z `submit` dopiero po dotknięciu przycisku.
    `grid`: krótkie pola liczbowe w dwóch kolumnach."""
    return {"kind": "controls", "items": items, "submit": submit, "grid": grid}


def chart(spec: dict, title: str | None = None, caption: str | None = None) -> dict:
    return {"kind": "chart", "title": title, "caption": caption, "chart": spec}


def table(df: pd.DataFrame, columns: list[tuple], title: str | None = None, caption: str | None = None,
          limit: int = 12) -> dict:
    """columns: (kolumna w df, nagłówek, fmt[, cyfry[, ze znakiem]]). Pierwsza kolumna jest przypięta przy przewijaniu.
    `ze znakiem`: liczba jest zmianą lub wynikiem - plus na zielono z „+”, minus na czerwono."""
    cols = [{"key": c[0], "label": c[1], "fmt": c[2] if len(c) > 2 else "text",
             "digits": c[3] if len(c) > 3 else None, "signed": bool(c[4]) if len(c) > 4 else False} for c in columns]
    rows = df[[c["key"] for c in cols]].to_dict("records") if len(df) else []
    return {"kind": "table", "title": title, "caption": caption, "columns": cols, "rows": rows, "limit": limit}


# --------------------------------------------------------------------------- #
# Wykresy
# --------------------------------------------------------------------------- #

def line(df: pd.DataFrame, fmt: str = "num", digits: int | None = None, colors: dict | None = None,
         area: bool = False, zero: bool = False, max_points: int = 1500, rebase: bool = False,
         delta: str | None = None, group: str | None = None, zoom: bool = True) -> dict:
    """Linie na jednej osi. Długie serie są przerzedzane (ostatni punkt zawsze zostaje).

    Wykres w telefonie można przybliżać (zakresy, suwak, dwa palce). `rebase`: serie liczone od 100 na początku
    widocznego okna (porównanie z indeksami). `delta`: pod wykresem zmiana w widocznym oknie - "abs" (kwota)
    albo "rel" (procent). `group`: wykresy jednego ekranu z tą samą grupą przybliżają się razem."""
    if len(df) > max_points:
        step = int(math.ceil(len(df) / max_points))
        keep = sorted(set(range(0, len(df), step)) | {len(df) - 1})
        df = df.iloc[keep]
    colors = SERIES_COLORS | (colors or {})
    series = [{"name": str(c), "values": _round(df[c].values), "color": _color(colors, str(c), i % 8)}
              for i, c in enumerate(df.columns)]
    out = {"type": "line", "x": _dates(df.index), "series": series, "fmt": fmt, "digits": digits,
           "area": area, "zero": zero, "zoom": zoom}
    if rebase:
        out["rebase"] = True
    if delta:
        out["delta"] = delta
    if group:
        out["group"] = group
    return out


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
            "series": [{"name": str(c), "values": _round(df[c].values), "color": _color(colors, str(c), i % 8)}
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


def curve(labels: list[str], series: list[dict], fmt: str = "pct", digits: int | None = None) -> dict:
    """Linie nad osią kategorii (np. krzywa rentowności według zapadalności).
    series: [{"name", "values" (po jednej na etykietę, None = brak), "color"}]."""
    return {"type": "curve", "labels": [str(x) for x in labels], "fmt": fmt, "digits": digits,
            "series": [{"name": str(s["name"]), "values": _round([math.nan if v is None else v for v in s["values"]]),
                        "color": s.get("color", i % 8)} for i, s in enumerate(series)]}
