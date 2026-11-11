"""Parser eksportu z xStation 5 (xlsx / csv).

Rozpoznaje arkusze i kolumny po nazwach (PL i EN), toleruje różne formaty dat
i separatory dziesiętne. Gdy formatu nie da się rozpoznać, rzuca XTBFormatError
z czytelną informacją, czego brakuje.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd


class XTBFormatError(ValueError):
    """Plik nie wygląda jak eksport xStation 5 (komunikat jest przeznaczony dla użytkownika)."""


# --------------------------------------------------------------------------- #
# Normalizacja tekstu, liczb i dat
# --------------------------------------------------------------------------- #

def norm(text) -> str:
    """Małe litery, bez polskich znaków, znaki nie-alfanumeryczne -> spacja."""
    if text is None or (isinstance(text, float) and np.isnan(text)):
        return ""
    s = str(text).strip().lower().replace("ł", "l")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def parse_number(value) -> float:
    """'1 234,56' / '1,234.56' / '1.234,56' / '-12,5' / 12.5 -> float. Puste -> NaN."""
    if value is None:
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    s = str(value).strip().replace(" ", "").replace(" ", "").replace("'", "")
    if s in ("", "-", "nan", "NaN", "None"):
        return np.nan
    s = re.sub(r"[^\d,.\-+eE]", "", s)
    if "," in s and "." in s:
        # ostatni z separatorów jest dziesiętny
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # '1,234,567' (tysiące) kontra '12,5' (dziesiętny)
        if s.count(",") > 1:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


_YMD = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_DMY = re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_EXCEL_EPOCH = datetime(1899, 12, 30)


def parse_datetime(value) -> pd.Timestamp:
    """Elastyczny parser daty: ISO, dd.mm.rrrr, dd/mm/rrrr, numer seryjny Excela."""
    if value is None:
        return pd.NaT
    if isinstance(value, pd.Timestamp):
        return value.tz_localize(None) if value.tzinfo else value
    if isinstance(value, datetime):
        return pd.Timestamp(value.replace(tzinfo=None))
    if isinstance(value, date):
        return pd.Timestamp(value)
    if isinstance(value, (int, float, np.integer, np.floating)):
        if np.isnan(value):
            return pd.NaT
        if 20000 < float(value) < 80000:  # numer seryjny Excela (lata 1954-2119)
            return pd.Timestamp(_EXCEL_EPOCH + timedelta(days=float(value))).round("s")
        return pd.NaT
    s = str(value).strip()
    if not s or s.lower() in ("nan", "nat", "none", "-"):
        return pd.NaT
    m = _YMD.match(s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = _DMY.match(s)
        if not m:
            try:
                return pd.Timestamp(pd.to_datetime(s, dayfirst=True)).tz_localize(None)
            except Exception:
                return pd.NaT
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mo > 12 and d <= 12:  # zapis amerykański mm/dd/rrrr
            d, mo = mo, d
    hh = int(m.group(4) or 0)
    mm = int(m.group(5) or 0)
    ss = int(m.group(6) or 0)
    try:
        return pd.Timestamp(year=y, month=mo, day=d, hour=hh, minute=mm, second=ss)
    except ValueError:
        return pd.NaT


# --------------------------------------------------------------------------- #
# Słowniki nazw kolumn (po normalizacji) - PL i EN
# --------------------------------------------------------------------------- #

CLOSED_COLS = {
    "position": ["position id", "position", "pozycja", "id pozycji", "numer pozycji", "order", "zlecenie"],
    # "ticker" ma pierwszeństwo: w nowszych raportach kolumna "Instrument" to nazwa spółki, a symbol jest w "Ticker"
    "symbol": ["ticker", "symbol", "walor", "instrument"],
    "type": ["type", "typ", "side", "kierunek", "strona"],
    "volume": ["volume", "wolumen", "ilosc", "quantity", "liczba"],
    "open_time": ["open time", "czas otwarcia", "data otwarcia", "open date", "otwarcie"],
    "open_price": ["open price", "cena otwarcia", "kurs otwarcia"],
    "close_time": ["close time", "czas zamkniecia", "data zamkniecia", "close date", "zamkniecie"],
    "close_price": ["close price", "cena zamkniecia", "kurs zamkniecia"],
    "purchase_value": ["purchase value", "wartosc zakupu", "wartosc nabycia", "wartosc kupna"],
    "sale_value": ["sale value", "wartosc sprzedazy"],
    "margin": ["margin", "depozyt", "depozyt zabezpieczajacy"],
    "commission": ["commission", "open commission", "prowizja"],
    "swap": ["swap", "punkty swapowe"],
    "rollover": ["rollover"],
    "gross_pl": ["gross p l", "gross pl", "gross profit", "profit loss", "profit", "zysk strata brutto", "zysk brutto",
                 "wynik brutto", "zysk strata", "zysk", "wynik", "p l"],
    "comment": ["comment", "komentarz"],
    # kolumny pomocnicze (nieobowiązkowe): nazwa i kategoria instrumentu, kurs przeliczenia
    "name": ["instrument", "instrument position", "nazwa", "nazwa instrumentu"],
    "category": ["category", "kategoria"],
    "open_rate": ["open conversion rate", "kurs przeliczenia otwarcia"],
}
CLOSED_REQUIRED = ["symbol", "type", "volume", "open_time", "open_price", "close_time", "close_price"]

OPEN_COLS = {
    k: v for k, v in CLOSED_COLS.items() if k not in ("close_time", "close_price", "sale_value")
}
OPEN_COLS["market_price"] = ["market price", "current price", "cena rynkowa", "aktualna cena", "cena biezaca"]
OPEN_COLS["position"] = CLOSED_COLS["position"] + ["instrument position"]      # w arkuszu otwartych pozycji ID stoi w "Instrument/Position"
OPEN_COLS["value"] = ["value", "market value", "wartosc rynkowa", "wartosc"]
OPEN_REQUIRED = ["symbol", "type", "volume", "open_time", "open_price"]

CASH_COLS = {
    "id": ["id", "identyfikator", "numer"],
    "type": ["type", "typ", "rodzaj", "typ operacji"],
    "time": ["time", "czas", "data", "date", "data operacji"],
    "comment": ["comment", "komentarz", "opis"],
    "symbol": ["ticker", "symbol", "instrument"],
    "amount": ["amount", "kwota", "wartosc"],
}
CASH_REQUIRED = ["type", "time", "amount"]

_ALL_ALIASES = {a for d in (CLOSED_COLS, OPEN_COLS, CASH_COLS) for v in d.values() for a in v}

PRETTY = {
    "position": "Position / Pozycja", "symbol": "Symbol", "type": "Type / Typ",
    "volume": "Volume / Wolumen", "open_time": "Open time / Czas otwarcia",
    "open_price": "Open price / Cena otwarcia", "close_time": "Close time / Czas zamknięcia",
    "close_price": "Close price / Cena zamknięcia", "amount": "Amount / Kwota",
    "time": "Time / Czas", "gross_pl": "Gross P/L / Zysk/strata brutto",
    "market_price": "Market price / Cena rynkowa",
}
KIND_PL = {"closed": "Zamknięte pozycje (Closed Positions)", "open": "Otwarte pozycje (Open Positions)",
           "cash": "Operacje gotówkowe (Cash Operations)"}

# Rodzaje operacji gotówkowych: kolejność ma znaczenie (pierwsze trafienie wygrywa).
_CASH_KIND_RULES = [
    ("INTEREST_TAX", ["interest tax", "podatek od odsetek"]),
    ("INTEREST", ["interest", "odsetki"]),
    ("WHT", ["withholding", "podatek u zrodla", "podatek od dywidend", "wht"]),
    ("DIVIDEND", ["divident", "dividend", "dywidend"]),
    ("STOCK_PURCHASE", ["stock purchase", "zakup akcji", "zakup etf", "kupno akcji", "purchase"]),
    ("STOCK_SALE", ["stock sale", "stock sell", "sprzedaz akcji", "sprzedaz etf", "sale", "sell"]),
    ("CLOSE_TRADE", ["close trade", "zamkniecie pozycji", "zamkniecie transakcji", "profit loss",
                     "zysk strata", "closed position"]),
    ("SWAP", ["swap"]),
    ("ROLLOVER", ["rollover"]),
    ("COMMISSION", ["commission", "prowizja"]),
    ("FX_CONVERSION", ["currency conversion", "przewalutowanie", "conversion"]),
    ("DEPOSIT", ["deposit", "wplata", "zasilenie"]),
    ("WITHDRAWAL", ["withdrawal", "wyplata"]),
    ("TRANSFER", ["transfer", "przelew"]),
    ("FEE", ["sec fee", "iftt", "ftt", "stamp duty", "fee", "oplata", "tax", "podatek"]),
]
EXTERNAL_FLOW_KINDS = ("DEPOSIT", "WITHDRAWAL", "TRANSFER")


def cash_kind(type_raw) -> str:
    t = norm(type_raw)
    for kind, keys in _CASH_KIND_RULES:
        if any(k in t for k in keys):
            return kind
    return "OTHER"


def _side(value) -> str:
    t = norm(value)
    if any(k in t for k in ("sell", "sprzedaz", "short", "krotka")):
        return "SELL"
    if any(k in t for k in ("buy", "kupno", "long", "dluga", "zakup")):
        return "BUY"
    return ""


def clean_symbol(value) -> str:
    """'aapl.us_9 ' -> 'AAPL.US' (sufiks _9 oznacza w XTB akcje rzeczywiste; _4 = CFD na akcje zostaje)."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    s = str(value).strip().upper()
    return re.sub(r"_9$", "", s)


# --------------------------------------------------------------------------- #
# Wynik parsowania
# --------------------------------------------------------------------------- #

CLOSED_OUT = ["position", "symbol", "type", "volume", "open_time", "open_price", "close_time", "close_price",
              "purchase_value", "sale_value", "margin", "commission", "swap", "rollover", "gross_pl", "comment"]
OPEN_OUT = ["position", "symbol", "type", "volume", "open_time", "open_price", "market_price",
            "purchase_value", "margin", "commission", "swap", "rollover", "gross_pl", "comment"]
CASH_OUT = ["id", "type_raw", "kind", "time", "comment", "symbol", "amount"]


def _empty(cols) -> pd.DataFrame:
    df = pd.DataFrame({c: pd.Series(dtype="object") for c in cols})
    for c in cols:
        if c.endswith("_time") or c == "time":
            df[c] = pd.Series(dtype="datetime64[ns]")
        elif c not in ("position", "symbol", "type", "comment", "type_raw", "kind", "id"):
            df[c] = pd.Series(dtype="float64")
    return df


@dataclass
class XTBExport:
    closed: pd.DataFrame = field(default_factory=lambda: _empty(CLOSED_OUT))
    open: pd.DataFrame = field(default_factory=lambda: _empty(OPEN_OUT))
    cash: pd.DataFrame = field(default_factory=lambda: _empty(CASH_OUT))
    as_of: pd.Timestamp | None = None
    currency: str = "PLN"
    notes: list[str] = field(default_factory=list)
    # podpowiedzi z raportu: symbol -> {"name", "category", "currency"} (to, co XTB sam podaje o instrumencie)
    instruments: dict = field(default_factory=dict)
    original_currency: str | None = None     # waluta rachunku przed przeliczeniem kwot
    account: str = ""                        # numer rachunku (gdy raport go podaje)
    files: list = field(default_factory=list)      # nazwy plików, z których powstał
    accounts: list = field(default_factory=list)   # po scaleniu: opis rachunków składowych

    @property
    def symbols(self) -> list[str]:
        s = set(self.closed["symbol"]) | set(self.open["symbol"])
        return sorted(x for x in s if x)

    def summary(self) -> dict:
        return {
            "zamknięte pozycje": len(self.closed),
            "otwarte pozycje": len(self.open),
            "operacje gotówkowe": len(self.cash),
            "instrumenty": len(self.symbols),
            "stan na": None if self.as_of is None else self.as_of.date().isoformat(),
            "waluta konta": self.original_currency or self.currency,
        }


# --------------------------------------------------------------------------- #
# Czytanie surowych tabel
# --------------------------------------------------------------------------- #

def _read_csv_raw(data: bytes) -> pd.DataFrame:
    text = None
    for enc in ("utf-8-sig", "cp1250", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise XTBFormatError("Nie udało się odczytać pliku CSV (nieznane kodowanie znaków).")
    head = "\n".join(text.splitlines()[:30])
    delim = max([";", "\t", ",", "|"], key=lambda d: head.count(d))
    # własne czytanie zamiast pd.read_csv: wiersze nad nagłówkiem mają zwykle inną liczbę pól
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim, quotechar='"')]
    width = max((len(r) for r in rows), default=0)
    rows = [[(c if c.strip() != "" else None) for c in r] + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(rows, dtype=object)


def norm_header(text) -> str:
    """Nazwa kolumny po normalizacji; dopisek strefy czasowej ("Open Time (UTC)") jest pomijany."""
    return re.sub(r"\s+utc$", "", norm(text))


def _find_header(raw: pd.DataFrame) -> int | None:
    best, best_score = None, 0
    for i in range(min(40, len(raw))):
        cells = [norm_header(c) for c in raw.iloc[i].tolist()]
        score = sum(1 for c in cells if c and c in _ALL_ALIASES)
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 3 else None


def _table(raw: pd.DataFrame, header_idx: int) -> pd.DataFrame:
    header = [norm_header(c) for c in raw.iloc[header_idx].tolist()]
    body = raw.iloc[header_idx + 1:].copy()
    keep = [j for j, h in enumerate(header) if h]
    body = body.iloc[:, keep]
    cols, seen = [], set()
    for j in keep:
        h = header[j]
        while h in seen:
            h += " _dup"
        seen.add(h)
        cols.append(h)
    body.columns = cols
    body = body.dropna(how="all").reset_index(drop=True)
    return body


def _map(df: pd.DataFrame, aliases: dict) -> tuple[dict, list]:
    found = {}
    for canon, names in aliases.items():
        for n in names:
            if n in df.columns:
                found[canon] = n
                break
    return found, [c for c in aliases if c not in found]


def _kind_by_name(name: str) -> str | None:
    n = norm(name)
    if any(k in n for k in ("closed", "zamkni", "close")):
        return "closed"
    if any(k in n for k in ("cash", "gotowk", "operac", "srodk")):
        return "cash"
    if any(k in n for k in ("open", "otwart")):
        return "open"
    return None


def _kind_by_columns(df: pd.DataFrame) -> str | None:
    c_found, _ = _map(df, CLOSED_COLS)
    k_found, _ = _map(df, CASH_COLS)
    if "close_time" in c_found and "open_time" in c_found:
        return "closed"
    if "open_time" in c_found:
        return "open"
    if {"amount", "time", "type"} <= set(k_found):
        return "cash"
    return None


def _account_currency(raw: pd.DataFrame, header_idx: int) -> str | None:
    top = raw.iloc[:header_idx]
    for i in range(len(top)):
        row = top.iloc[i].tolist()
        for j, cell in enumerate(row):
            if norm(cell) in ("currency", "waluta", "waluta konta", "account currency"):
                cands = row[j + 1:j + 3]
                if i + 1 < len(raw):
                    cands.append(raw.iloc[i + 1, j])
                for c in cands:
                    s = c.strip().upper() if isinstance(c, str) else ""     # puste komórki to NaN, nie waluta
                    if re.fullmatch(r"[A-Z]{3}", s):
                        return s
    return None


_ASOF_LABELS = ("as of", "date to", "data do", "stan na", "report date", "data raportu")


def _report_date(raw: pd.DataFrame, header_idx: int) -> pd.Timestamp | None:
    """Data raportu z wierszy nad tabelą, np. "Data as of report generated | 2026-11-11 09:59:32" lub "Date to (UTC)"."""
    best = None
    top = raw.iloc[:header_idx]
    for i in range(len(top)):
        row = top.iloc[i].tolist()
        for j, cell in enumerate(row):
            if isinstance(cell, str) and any(k in norm(cell) for k in _ASOF_LABELS):
                for c in row[j + 1:]:
                    ts = parse_datetime(c)
                    if not pd.isna(ts) and 2000 <= ts.year <= 2100:
                        best = ts.normalize() if best is None else max(best, ts.normalize())
                        break
    return best


def _account_number(raw: pd.DataFrame, header_idx: int) -> str | None:
    top = raw.iloc[:header_idx]
    for i in range(len(top)):
        row = top.iloc[i].tolist()
        for j, cell in enumerate(row):
            if norm(cell) in ("account number", "account", "numer rachunku", "rachunek", "numer konta", "konto"):
                for c in row[j + 1:j + 4]:
                    digits = _pos_id(c)
                    if digits.isdigit() and len(digits) >= 5:
                        return digits
    return None


def _account_from_filename(name: str) -> str | None:
    m = re.match(r"^[A-Z]{3}_(\d{5,})_", name.replace("\\", "/").split("/")[-1])
    return m.group(1) if m else None


def _currency_from_filename(name: str) -> str | None:
    """Nowsze eksporty nazywają plik od waluty rachunku: "USD_51672516_2006-01-01_2026-11-11.xlsx"."""
    m = re.match(r"^([A-Z]{3})_\d+_", name.replace("\\", "/").split("/")[-1])
    return m.group(1) if m else None


def _date_from_name(name: str) -> pd.Timestamp | None:
    m = re.search(r"(\d{4})[-_.](\d{2})[-_.](\d{2})", name)
    if m:
        try:
            return pd.Timestamp(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"(?<!\d)(\d{2})[-_.]?(\d{2})[-_.]?(20\d{2})(?!\d)", name)
    if m:
        try:
            return pd.Timestamp(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


# --------------------------------------------------------------------------- #
# Budowanie tabel wynikowych
# --------------------------------------------------------------------------- #

def _missing_msg(label: str, kind: str, missing: list, df: pd.DataFrame) -> str:
    miss = ", ".join(PRETTY.get(m, m) for m in missing)
    have = ", ".join(str(c) for c in df.columns[:25])
    return (f"„{label}” rozpoznano jako: {KIND_PL[kind]}, ale brakuje wymaganych kolumn: {miss}. "
            f"Kolumny znalezione w pliku: {have}.")


def _num(df, found, key, default=0.0) -> pd.Series:
    if key not in found:
        return pd.Series(default, index=df.index, dtype="float64")
    return df[found[key]].map(parse_number).astype("float64")


def _txt(df, found, key) -> pd.Series:
    if key not in found:
        return pd.Series("", index=df.index, dtype="object")
    return df[found[key]].map(lambda v: "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip())


def _pos_id(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, (float, np.floating)) and float(v).is_integer():
        return str(int(v))
    return str(v).strip()


CATEGORY_CLASS = {"STOCK": "STOCK", "STOCKS": "STOCK", "AKCJE": "STOCK", "ETF": "ETF", "ETFS": "ETF"}


def _instrument_info(df: pd.DataFrame, found: dict, out: pd.DataFrame, kind: str) -> dict:
    """To, co raport sam mówi o instrumentach: nazwa, kategoria i czy są notowane w walucie rachunku."""
    info: dict[str, dict] = {}
    name_col = found.get("name")
    if name_col == found.get("symbol"):
        name_col = None                      # starszy układ: "Instrument" to symbol, nazwy nie ma
    cat_col = found.get("category")
    is_position = (out["type"] != "") & out["open_time"].notna()
    for i in df.index:
        sym = out.at[i, "symbol"]
        if not sym:
            continue
        d = info.setdefault(sym, {})
        if name_col is not None:
            raw = df.at[i, name_col]
            name = "" if raw is None or (isinstance(raw, float) and np.isnan(raw)) else str(raw).strip()
            # w arkuszu otwartych pozycji ta sama kolumna niesie nazwę (wiersz zbiorczy) albo numer pozycji
            if name and not name.replace(".", "").isdigit() and not (kind == "open" and bool(is_position.at[i])):
                d.setdefault("name", name)
        if cat_col is not None:
            raw = df.at[i, cat_col]
            cat = "" if raw is None or (isinstance(raw, float) and np.isnan(raw)) else str(raw).strip().upper()
            if cat:
                d.setdefault("category", cat)
    # cena x ilość == wartość w walucie rachunku  =>  instrument jest notowany w walucie rachunku
    price = out["open_price"] if kind == "closed" else out.get("market_price")
    value = out["purchase_value"] if kind == "closed" else out.get("_value")
    if price is not None and value is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = value / (out["volume"] * price)
        for sym, r in zip(out["symbol"][is_position], ratio[is_position]):
            if sym and np.isfinite(r) and r > 0:
                info.setdefault(sym, {}).setdefault("_ratios", []).append(float(r))
    return info


def _build_positions(df: pd.DataFrame, label: str, kind: str) -> tuple[pd.DataFrame, dict]:
    aliases, required, out_cols = (
        (CLOSED_COLS, CLOSED_REQUIRED, CLOSED_OUT) if kind == "closed" else (OPEN_COLS, OPEN_REQUIRED, OPEN_OUT)
    )
    found, _ = _map(df, aliases)
    missing = [c for c in required if c not in found]
    if missing:
        raise XTBFormatError(_missing_msg(label, kind, missing, df))
    out = pd.DataFrame(index=df.index)
    out["position"] = df[found["position"]].map(_pos_id) if "position" in found else ""
    out["symbol"] = df[found["symbol"]].map(clean_symbol)
    out["type"] = df[found["type"]].map(_side)
    out["volume"] = _num(df, found, "volume", np.nan).abs()
    out["open_time"] = pd.to_datetime(df[found["open_time"]].map(parse_datetime))
    out["open_price"] = _num(df, found, "open_price", np.nan)
    if kind == "closed":
        out["close_time"] = pd.to_datetime(df[found["close_time"]].map(parse_datetime))
        out["close_price"] = _num(df, found, "close_price", np.nan)
        out["sale_value"] = _num(df, found, "sale_value", np.nan)
    else:
        out["market_price"] = _num(df, found, "market_price", np.nan)
        out["_value"] = _num(df, found, "value", np.nan)
    out["purchase_value"] = _num(df, found, "purchase_value", np.nan)
    for c in ("margin", "commission", "swap", "rollover"):
        out[c] = _num(df, found, c, 0.0).fillna(0.0)
    out["gross_pl"] = _num(df, found, "gross_pl", np.nan)
    out["comment"] = _txt(df, found, "comment")
    if kind == "open" and "purchase_value" not in found and "value" in found:
        # nowszy raport podaje wartość rynkową i wynik zamiast wartości zakupu
        if "category" in found:
            # kategoria stoi tylko w wierszu zbiorczym instrumentu - wiersze pozycji dziedziczą ją po tickerze
            cat = df[found["category"]].map(lambda v: v.strip().upper() if isinstance(v, str) else "")
            by_symbol = {s: c for s, c in zip(out["symbol"], cat) if s and c}
            cat = pd.Series([c or by_symbol.get(s, "") for s, c in zip(out["symbol"], cat)], index=df.index)
            is_stock = cat.map(lambda c: CATEGORY_CLASS.get(c, "STOCK") != "CFD")
        else:
            is_stock = True
        out["purchase_value"] = (out["_value"] - out["gross_pl"]).where(is_stock)
    info = _instrument_info(df, found, out, kind)
    # wiersze podsumowań ("Total", "Profit/loss", wiersz zbiorczy instrumentu) i puste
    ok = (out["symbol"] != "") & out["open_time"].notna() & out["volume"].notna() & (out["type"] != "")
    if kind == "closed":
        ok &= out["close_time"].notna()
    out = out[ok].copy()
    if not len(out):
        return _empty(out_cols), info
    return out[out_cols].sort_values("open_time").reset_index(drop=True), info


_SYM_IN_COMMENT = re.compile(r"\b([A-Z0-9]{1,12}\.[A-Z]{2}(?:_\d)?)\b")


def _build_cash(df: pd.DataFrame, label: str) -> pd.DataFrame:
    found, _ = _map(df, CASH_COLS)
    missing = [c for c in CASH_REQUIRED if c not in found]
    if missing:
        raise XTBFormatError(_missing_msg(label, "cash", missing, df))
    out = pd.DataFrame(index=df.index)
    out["id"] = df[found["id"]].map(_pos_id) if "id" in found else ""
    out["type_raw"] = _txt(df, found, "type")
    out["kind"] = out["type_raw"].map(cash_kind)
    out["time"] = pd.to_datetime(df[found["time"]].map(parse_datetime))
    out["comment"] = _txt(df, found, "comment")
    sym = _txt(df, found, "symbol").map(clean_symbol)
    from_comment = out["comment"].str.upper().str.extract(_SYM_IN_COMMENT, expand=False).fillna("").map(clean_symbol)
    out["symbol"] = np.where(sym != "", sym, from_comment)
    out["amount"] = _num(df, found, "amount", np.nan)
    out = out[out["time"].notna() & out["amount"].notna() & (out["type_raw"] != "")].copy()
    # TRANSFER / OTHER bez symbolu traktujemy wg znaku jak przepływ zewnętrzny tylko dla TRANSFER
    if not len(out):
        return _empty(CASH_OUT)
    return out[CASH_OUT].sort_values("time").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# API publiczne
# --------------------------------------------------------------------------- #

def _raw_tables(name: str, data: bytes) -> list[tuple[str, pd.DataFrame]]:
    lower = name.lower()
    if lower.endswith((".xlsx", ".xlsm", ".xls")):
        try:
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object)
        except Exception as exc:  # uszkodzony plik / zły format
            raise XTBFormatError(f"Nie udało się otworzyć pliku Excel „{name}”: {exc}") from exc
        return [(f"{name} › {sheet}", df) for sheet, df in sheets.items()]
    if lower.endswith((".csv", ".txt", ".tsv")):
        return [(name, _read_csv_raw(data))]
    raise XTBFormatError(f"Nieobsługiwany typ pliku: „{name}”. Wgraj eksport z xStation 5 jako .xlsx lub .csv.")


def parse_export(files) -> XTBExport:
    """Parsuje eksport XTB.

    `files`: ścieżka, albo lista krotek (nazwa, bajty) - jeden xlsx z trzema arkuszami
    lub kilka plików csv (po jednym na tabelę).
    """
    if isinstance(files, (str, bytes)) or hasattr(files, "__fspath__"):
        path = str(files)
        with open(path, "rb") as fh:
            files = [(path.replace("\\", "/").split("/")[-1], fh.read())]
    exp = XTBExport()
    parts: dict[str, list[pd.DataFrame]] = {"closed": [], "open": [], "cash": []}
    seen, name_date, errors = [], None, []
    infos: list[dict] = []
    file_currency = None
    account = None

    for fname, data in files:
        file_currency = _currency_from_filename(fname) or file_currency
        account = _account_from_filename(fname) or account
        for label, raw in _raw_tables(fname, data):
            if raw is None or raw.dropna(how="all").empty:
                continue
            hdr = _find_header(raw)
            sheet_name = label.split("›")[-1].strip()
            kind_hint = _kind_by_name(sheet_name)
            if hdr is None:
                if kind_hint:
                    errors.append(f"„{label}”: nie znaleziono wiersza nagłówków z nazwami kolumn "
                                  f"(oczekiwano m.in. Symbol, Type/Typ, Volume/Wolumen).")
                continue
            tbl = _table(raw, hdr)
            kind = _kind_by_columns(tbl) or kind_hint
            if kind is None:
                continue
            seen.append(f"{label} → {KIND_PL[kind]}")
            cur = _account_currency(raw, hdr)
            if cur:
                exp.currency = cur
            if kind == "open":
                name_date = _date_from_name(sheet_name) or name_date
            account = _account_number(raw, hdr) or account
            rep_date = _report_date(raw, hdr)
            if rep_date is not None:
                name_date = rep_date if name_date is None else max(name_date, rep_date)
            try:
                if kind == "cash":
                    parts[kind].append(_build_cash(tbl, label))
                else:
                    table, info = _build_positions(tbl, label, kind)
                    parts[kind].append(table)
                    infos.append(info)
            except XTBFormatError as exc:
                errors.append(str(exc))

    if errors:
        raise XTBFormatError("Problem z formatem pliku:\n- " + "\n- ".join(errors))
    if not any(parts.values()):
        raise XTBFormatError(
            "Nie rozpoznano żadnej tabeli z eksportu xStation 5. Oczekuję arkuszy: "
            "Closed Positions (zamknięte pozycje), Open Positions (otwarte pozycje) i "
            "Cash Operations (operacje gotówkowe), z nagłówkami takimi jak Symbol, Type, Volume, "
            "Open time, Open price, Close time, Amount. Sprawdź, czy wgrywasz raport z xStation 5 "
            "(Historia → Eksportuj)."
        )
    if not parts["cash"]:
        raise XTBFormatError(
            "Brakuje tabeli operacji gotówkowych (Cash Operations / Operacje gotówkowe). "
            "Bez niej nie da się odtworzyć wpłat, wypłat, dywidend i kosztów. "
            f"Rozpoznane tabele: {'; '.join(seen) or 'brak'}."
        )
    if not parts["closed"] and not parts["open"]:
        raise XTBFormatError(
            "Brakuje tabel z pozycjami (Closed Positions i/lub Open Positions). "
            f"Rozpoznane tabele: {'; '.join(seen) or 'brak'}."
        )

    def _cat(lst, cols):
        lst = [x for x in lst if len(x)]
        if not lst:
            return _empty(cols)
        df = pd.concat(lst, ignore_index=True)
        return df.drop_duplicates().reset_index(drop=True)

    exp.closed = _cat(parts["closed"], CLOSED_OUT)
    exp.open = _cat(parts["open"], OPEN_OUT)
    exp.cash = _cat(parts["cash"], CASH_OUT)
    if not parts["closed"]:
        exp.notes.append("Brak tabeli zamkniętych pozycji - analityka tradera i podatki będą niepełne.")
    if not parts["open"]:
        exp.notes.append("Brak tabeli otwartych pozycji - zakładam, że na koniec okresu portfel jest w gotówce.")
    unknown = exp.cash.loc[exp.cash["kind"] == "OTHER", "type_raw"].unique().tolist()
    if unknown:
        exp.notes.append("Nierozpoznane typy operacji gotówkowych (traktowane jako wynik, nie wpłata): "
                         + ", ".join(map(str, unknown[:8])))
    if file_currency and exp.currency == "PLN":
        exp.currency = file_currency
    exp.account = account or ""
    # podpowiedzi o instrumentach: nazwa, kategoria; waluta, gdy wartości zgadzają się 1:1 z ceną x ilość
    for info in infos:
        for sym, d in info.items():
            tgt = exp.instruments.setdefault(sym, {})
            for k in ("name", "category"):
                if d.get(k) and k not in tgt:
                    tgt[k] = d[k]
            tgt.setdefault("_ratios", []).extend(d.get("_ratios", []))
    for sym, d in exp.instruments.items():
        ratios = d.pop("_ratios", [])
        if ratios and abs(float(np.median(ratios)) - 1.0) < 0.004:
            d["currency"] = exp.currency
    if exp.currency != "PLN":
        exp.notes.append(f"Rachunek prowadzony w {exp.currency}. Walutę wyświetlania (PLN / USD / EUR) wybierasz na pasku u góry.")

    last = [t for t in (exp.cash["time"].max() if len(exp.cash) else None,
                        exp.closed["close_time"].max() if len(exp.closed) else None,
                        exp.open["open_time"].max() if len(exp.open) else None) if t is not None and not pd.isna(t)]
    last_event = max(last).normalize() if last else None
    # data z nazwy arkusza/pliku liczy się tylko, gdy jest tuż po ostatnim zdarzeniu (inaczej to np. numer rachunku
    # albo początek zakresu dat w nazwie pliku)
    if name_date is not None and last_event is not None and not (
            last_event <= name_date <= last_event + pd.Timedelta(days=10)):
        name_date = None
    exp.as_of = max([d for d in (name_date, last_event) if d is not None]) if (name_date or last_event) else None
    exp.notes = seen + exp.notes
    return exp


# --------------------------------------------------------------------------- #
# Kilka rachunków naraz
# --------------------------------------------------------------------------- #

_TRANSFER_ACCOUNTS = re.compile(r"from\s+TA:?\s*(\d+)\s+to:?\s*(\d+)", re.IGNORECASE)


def _peek_account(name: str, data: bytes) -> str | None:
    """Numer rachunku pliku: z nazwy albo z wiersza "Account number" w pierwszym arkuszu."""
    acc = _account_from_filename(name)
    if acc:
        return acc
    try:
        for _, raw in _raw_tables(name, data):
            hdr = _find_header(raw)
            if hdr is not None:
                acc = _account_number(raw, hdr)
                if acc:
                    return acc
    except XTBFormatError:
        return None
    return None


def parse_accounts(files) -> list[XTBExport]:
    """Dzieli pliki na rachunki (po numerze rachunku) i parsuje każdy osobno.

    Pliki bez numeru rachunku (np. stare eksporty CSV) trafiają razem do jednego rachunku. Gdy dla tego samego
    rachunku wgrano kilka plików xlsx, liczy się najnowszy raport - starszy zawierałby pozycje, które w nowszym
    są już zamknięte, i zostałyby policzone podwójnie.
    """
    if isinstance(files, (str, bytes)) or hasattr(files, "__fspath__"):
        return [parse_export(files)]
    groups: dict[str, list] = {}
    for name, data in files:
        groups.setdefault(_peek_account(name, data) or "", []).append((name, data))
    out = []
    for acc, group in groups.items():
        excel = [f for f in group if f[0].lower().endswith((".xlsx", ".xlsm", ".xls"))]
        if acc and len(excel) > 1 and len(excel) == len(group):
            parsed = [parse_export([f]) for f in excel]
            best = max(range(len(parsed)), key=lambda i: (parsed[i].as_of or pd.Timestamp.min, len(parsed[i].cash)))
            exp = parsed[best]
            exp.notes.append(f"Pominięto {len(excel) - 1} starszy eksport tego samego rachunku - liczy się najnowszy raport.")
            exp.files = [excel[best][0]]
        else:
            exp = parse_export(group)
            exp.files = [f[0] for f in group]
        exp.account = exp.account or acc or ("rachunek" if len(groups) == 1 else f"rachunek {len(out) + 1}")
        out.append(exp)
    return out


def merge_exports(exports: list[XTBExport], selected=None) -> XTBExport:
    """Scala wybrane rachunki w jeden portfel. Każdy wiersz dostaje kolumny `account` i `currency` (waluta
    rachunku, w której wyrażone są kwoty). Przelewy między wybranymi rachunkami przestają być wpłatą/wypłatą."""
    chosen = [e for e in exports if selected is None or e.account in set(selected)]
    if not chosen:
        raise XTBFormatError("Nie wybrano żadnego rachunku. Zaznacz przynajmniej jeden.")
    ids = {e.account for e in chosen}

    def tag(df, e):
        d = df.copy()
        d["account"] = e.account
        d["currency"] = e.currency
        return d

    def cat(frames, cols):
        frames = [f for f in frames if len(f)]
        if not frames:
            d = _empty(cols)
            d["account"] = pd.Series(dtype="object")
            d["currency"] = pd.Series(dtype="object")
            return d
        return pd.concat(frames, ignore_index=True)

    out = XTBExport(
        closed=cat([tag(e.closed, e) for e in chosen], CLOSED_OUT), open=cat([tag(e.open, e) for e in chosen], OPEN_OUT),
        cash=cat([tag(e.cash, e) for e in chosen], CASH_OUT),
        as_of=max((e.as_of for e in chosen if e.as_of is not None), default=None))
    currencies = sorted({e.currency for e in chosen})
    out.currency = currencies[0] if len(currencies) == 1 else "MIX"
    out.account = ", ".join(sorted(ids))
    if len(out.cash):
        out.cash = out.cash.sort_values("time").reset_index(drop=True)

        def internal(row):
            if row["kind"] != "TRANSFER":
                return row["kind"]
            m = _TRANSFER_ACCOUNTS.search(str(row["comment"]))
            return "INTERNAL" if m and m.group(1) in ids and m.group(2) in ids else row["kind"]

        out.cash["kind"] = out.cash.apply(internal, axis=1)
    for e in chosen:
        for sym, d in e.instruments.items():
            tgt = out.instruments.setdefault(sym, {})
            for k, v in d.items():
                tgt.setdefault(k, v)
        out.notes += [f"[{e.account}] {n}" for n in e.notes] if len(chosen) > 1 else list(e.notes)
    out.accounts = [{"id": e.account, "currency": e.currency, "closed": len(e.closed), "open": len(e.open),
                     "cash": len(e.cash), "as_of": e.as_of, "selected": e.account in ids,
                     "files": list(getattr(e, "files", []))} for e in exports]
    return out
