"""Mapowanie symboli XTB (np. PKN.PL, AAPL.US, US500) na tickery yfinance / Stooq
oraz metadane: klasa aktywów, waluta, mnożnik kontraktu CFD, sektor, kraj.

Tabela jest w pełni edytowalna w UI; poprawki użytkownika zapisują się w
`.cache/mapping_overrides.csv` i mają pierwszeństwo przed regułami automatycznymi.
"""
from __future__ import annotations

import os
import re

import pandas as pd

MAPPING_COLUMNS = ["symbol", "name", "asset_class", "yf_ticker", "stooq_ticker", "currency",
                   "price_factor", "multiplier", "margin_rate", "spread_bps", "sector", "country"]

# sufiks giełdy w symbolu XTB -> (sufiks Yahoo, sufiks Stooq, waluta, kraj)
SUFFIX_RULES = {
    "PL": (".WA", "", "PLN", "Polska"),
    "US": ("", ".us", "USD", "USA"),
    "DE": (".DE", ".de", "EUR", "Niemcy"),
    "UK": (".L", ".uk", "GBP", "Wielka Brytania"),
    "FR": (".PA", ".fr", "EUR", "Francja"),
    "NL": (".AS", ".nl", "EUR", "Holandia"),
    "ES": (".MC", ".es", "EUR", "Hiszpania"),
    "IT": (".MI", ".it", "EUR", "Włochy"),
    "BE": (".BR", ".be", "EUR", "Belgia"),
    "PT": (".LS", ".pt", "EUR", "Portugalia"),
    "FI": (".HE", ".fi", "EUR", "Finlandia"),
    "CH": (".SW", ".ch", "CHF", "Szwajcaria"),
    "SE": (".ST", ".se", "SEK", "Szwecja"),
    "DK": (".CO", ".dk", "DKK", "Dania"),
    "NO": (".OL", ".no", "NOK", "Norwegia"),
    "CZ": (".PR", ".cz", "CZK", "Czechy"),
}

# Szacunkowy spread (w punktach bazowych wartości transakcji, w jedną stronę = połowa spreadu x2 za round-trip)
DEFAULT_SPREAD_BPS = {"PL": 15.0, "US": 4.0, "ETF": 8.0, "CFD_INDEX": 2.5, "CFD_COMMODITY": 4.0,
                      "CFD_FX": 1.0, "OTHER": 10.0}

S = "STOCK"
E = "ETF"
C = "CFD"

# symbol -> (nazwa, klasa, sektor, kraj[, nadpisania])
KNOWN: dict[str, tuple] = {
    # --- GPW
    "PKN.PL": ("Orlen", S, "Energia", "Polska"),
    "PKO.PL": ("PKO BP", S, "Finanse", "Polska"),
    "PEO.PL": ("Bank Pekao", S, "Finanse", "Polska"),
    "PZU.PL": ("PZU", S, "Finanse", "Polska"),
    "SPL.PL": ("Santander Bank Polska", S, "Finanse", "Polska"),
    "MBK.PL": ("mBank", S, "Finanse", "Polska"),
    "ALR.PL": ("Alior Bank", S, "Finanse", "Polska"),
    "XTB.PL": ("XTB", S, "Finanse", "Polska"),
    "KGH.PL": ("KGHM", S, "Surowce", "Polska"),
    "JSW.PL": ("JSW", S, "Surowce", "Polska"),
    "CDR.PL": ("CD Projekt", S, "Technologia", "Polska"),
    "DNP.PL": ("Dino Polska", S, "Dobra podstawowe", "Polska"),
    "ALE.PL": ("Allegro", S, "Dobra cykliczne", "Polska"),
    "LPP.PL": ("LPP", S, "Dobra cykliczne", "Polska"),
    "CCC.PL": ("CCC", S, "Dobra cykliczne", "Polska"),
    "PGE.PL": ("PGE", S, "Użyteczność publiczna", "Polska"),
    "TPE.PL": ("Tauron", S, "Użyteczność publiczna", "Polska"),
    "OPL.PL": ("Orange Polska", S, "Telekomunikacja", "Polska"),
    "CPS.PL": ("Cyfrowy Polsat", S, "Telekomunikacja", "Polska"),
    "KTY.PL": ("Kęty", S, "Przemysł", "Polska"),
    "BDX.PL": ("Budimex", S, "Przemysł", "Polska"),
    "ZAB.PL": ("Żabka", S, "Dobra podstawowe", "Polska"),
    # --- USA
    "AAPL.US": ("Apple", S, "Technologia", "USA"),
    "MSFT.US": ("Microsoft", S, "Technologia", "USA"),
    "NVDA.US": ("NVIDIA", S, "Technologia", "USA"),
    "AMD.US": ("AMD", S, "Technologia", "USA"),
    "GOOGL.US": ("Alphabet", S, "Telekomunikacja", "USA"),
    "META.US": ("Meta Platforms", S, "Telekomunikacja", "USA"),
    "AMZN.US": ("Amazon", S, "Dobra cykliczne", "USA"),
    "TSLA.US": ("Tesla", S, "Dobra cykliczne", "USA"),
    "KO.US": ("Coca-Cola", S, "Dobra podstawowe", "USA"),
    "PG.US": ("Procter & Gamble", S, "Dobra podstawowe", "USA"),
    "JNJ.US": ("Johnson & Johnson", S, "Ochrona zdrowia", "USA"),
    "PFE.US": ("Pfizer", S, "Ochrona zdrowia", "USA"),
    "XOM.US": ("Exxon Mobil", S, "Energia", "USA"),
    "JPM.US": ("JPMorgan Chase", S, "Finanse", "USA"),
    "V.US": ("Visa", S, "Finanse", "USA"),
    "BRKB.US": ("Berkshire Hathaway B", S, "Finanse", "USA", {"yf_ticker": "BRK-B", "stooq_ticker": "brk-b.us"}),
    # --- ETF
    "CSPX.UK": ("iShares Core S&P 500 UCITS (Acc)", E, "ETF akcji USA", "USA",
                {"yf_ticker": "CSPX.L", "stooq_ticker": "cspx.uk", "currency": "USD", "price_factor": 1.0}),
    "IWDA.UK": ("iShares Core MSCI World UCITS (Acc)", E, "ETF akcji globalnych", "Świat",
                {"yf_ticker": "IWDA.L", "stooq_ticker": "iwda.uk", "currency": "USD", "price_factor": 1.0}),
    "VUSA.UK": ("Vanguard S&P 500 UCITS (Dist)", E, "ETF akcji USA", "USA", {"price_factor": 1.0}),
    "EUNL.DE": ("iShares Core MSCI World UCITS (Acc)", E, "ETF akcji globalnych", "Świat"),
    "VWCE.DE": ("Vanguard FTSE All-World UCITS (Acc)", E, "ETF akcji globalnych", "Świat"),
    "SXR8.DE": ("iShares Core S&P 500 UCITS (Acc)", E, "ETF akcji USA", "USA"),
    "IS3N.DE": ("iShares Core MSCI EM IMI UCITS (Acc)", E, "ETF rynków wschodzących", "Rynki wschodzące"),
    "4GLD.DE": ("Xetra-Gold", E, "Złoto", "Świat"),
    "ETFBW20TR.PL": ("Beta ETF WIG20TR", E, "ETF akcji polskich", "Polska"),
    "ETFBM40TR.PL": ("Beta ETF mWIG40TR", E, "ETF akcji polskich", "Polska"),
    "ETFBS80TR.PL": ("Beta ETF sWIG80TR", E, "ETF akcji polskich", "Polska"),
}

# CFD: symbol -> (nazwa, yf, stooq, waluta kwotowania, mnożnik lota, depozyt, sektor, kraj, klucz spreadu)
CFD_KNOWN: dict[str, tuple] = {
    "US500": ("CFD S&P 500", "^GSPC", "^spx", "USD", 50, 0.05, "Indeks akcji", "USA", "CFD_INDEX"),
    "US100": ("CFD Nasdaq 100", "^NDX", "^ndx", "USD", 20, 0.05, "Indeks akcji", "USA", "CFD_INDEX"),
    "US30": ("CFD Dow Jones", "^DJI", "^dji", "USD", 5, 0.05, "Indeks akcji", "USA", "CFD_INDEX"),
    "US2000": ("CFD Russell 2000", "^RUT", "", "USD", 50, 0.10, "Indeks akcji", "USA", "CFD_INDEX"),
    "DE40": ("CFD DAX", "^GDAXI", "^dax", "EUR", 25, 0.05, "Indeks akcji", "Niemcy", "CFD_INDEX"),
    "EU50": ("CFD Euro Stoxx 50", "^STOXX50E", "", "EUR", 10, 0.05, "Indeks akcji", "Strefa euro", "CFD_INDEX"),
    "UK100": ("CFD FTSE 100", "^FTSE", "^ukx", "GBP", 10, 0.05, "Indeks akcji", "Wielka Brytania", "CFD_INDEX"),
    "FRA40": ("CFD CAC 40", "^FCHI", "^cac", "EUR", 10, 0.05, "Indeks akcji", "Francja", "CFD_INDEX"),
    "JP225": ("CFD Nikkei 225", "^N225", "^nkx", "JPY", 500, 0.05, "Indeks akcji", "Japonia", "CFD_INDEX"),
    "W20": ("CFD WIG20", "WIG20.WA|^WIG20", "wig20", "PLN", 20, 0.10, "Indeks akcji", "Polska", "CFD_INDEX"),
    "GOLD": ("CFD złoto", "GC=F", "xauusd", "USD", 100, 0.05, "Surowce", "Świat", "CFD_COMMODITY"),
    "SILVER": ("CFD srebro", "SI=F", "xagusd", "USD", 5000, 0.10, "Surowce", "Świat", "CFD_COMMODITY"),
    "OIL": ("CFD ropa Brent", "BZ=F", "cb.f", "USD", 1000, 0.10, "Surowce", "Świat", "CFD_COMMODITY"),
    "OIL.WTI": ("CFD ropa WTI", "CL=F", "cl.f", "USD", 1000, 0.10, "Surowce", "Świat", "CFD_COMMODITY"),
    "NATGAS": ("CFD gaz ziemny", "NG=F", "ng.f", "USD", 30000, 0.10, "Surowce", "Świat", "CFD_COMMODITY"),
    "EURUSD": ("CFD EUR/USD", "EURUSD=X", "eurusd", "USD", 100000, 0.0333, "Waluty", "Świat", "CFD_FX"),
    "GBPUSD": ("CFD GBP/USD", "GBPUSD=X", "gbpusd", "USD", 100000, 0.0333, "Waluty", "Świat", "CFD_FX"),
    "USDJPY": ("CFD USD/JPY", "USDJPY=X", "usdjpy", "JPY", 100000, 0.0333, "Waluty", "Świat", "CFD_FX"),
    "USDPLN": ("CFD USD/PLN", "USDPLN=X", "usdpln", "PLN", 100000, 0.05, "Waluty", "Świat", "CFD_FX"),
    "EURPLN": ("CFD EUR/PLN", "EURPLN=X", "eurpln", "PLN", 100000, 0.05, "Waluty", "Świat", "CFD_FX"),
    "BITCOIN": ("CFD Bitcoin", "BTC-USD", "btcusd", "USD", 1, 0.50, "Kryptowaluty", "Świat", "CFD_COMMODITY"),
    "ETHEREUM": ("CFD Ethereum", "ETH-USD", "ethusd", "USD", 1, 0.50, "Kryptowaluty", "Świat", "CFD_COMMODITY"),
}

DEFAULT_OVERRIDES_PATH = os.path.join(".cache", "mapping_overrides.csv")


def auto_map(symbol: str) -> dict:
    """Reguły automatyczne dla jednego symbolu XTB."""
    sym = symbol.strip().upper()
    row = {"symbol": sym, "name": sym, "asset_class": S, "yf_ticker": "", "stooq_ticker": "",
           "currency": "PLN", "price_factor": 1.0, "multiplier": 1.0, "margin_rate": 1.0,
           "spread_bps": DEFAULT_SPREAD_BPS["OTHER"], "sector": "Nieznany", "country": "Nieznany"}

    stock_cfd = sym.endswith("_4")
    base = re.sub(r"_\d$", "", sym)

    if base in CFD_KNOWN:
        name, yf, stq, cur, mult, margin, sector, country, spread_key = CFD_KNOWN[base]
        row.update(name=name, asset_class=C, yf_ticker=yf, stooq_ticker=stq, currency=cur,
                   multiplier=float(mult), margin_rate=margin, sector=sector, country=country,
                   spread_bps=DEFAULT_SPREAD_BPS[spread_key])
        return row

    m = re.fullmatch(r"([A-Z0-9\-]+)\.([A-Z]{2})", base)
    if m and m.group(2) in SUFFIX_RULES:
        root, suf = m.group(1), m.group(2)
        yf_suf, stq_suf, cur, country = SUFFIX_RULES[suf]
        row.update(yf_ticker=f"{root}{yf_suf}", stooq_ticker=f"{root.lower()}{stq_suf}",
                   currency=cur, country=country,
                   spread_bps=DEFAULT_SPREAD_BPS.get(suf, DEFAULT_SPREAD_BPS["OTHER"]))
        if suf == "UK":
            row["price_factor"] = 0.01  # Yahoo/Stooq podają akcje z LSE w pensach (GBX)
    elif "." not in base:
        # nieznany instrument bez sufiksu giełdy - najpewniej CFD; wymaga ręcznego uzupełnienia
        row.update(asset_class=C, yf_ticker=base, stooq_ticker=base.lower(), currency="USD",
                   margin_rate=0.10, sector="CFD (do uzupełnienia)")

    if base in KNOWN:
        name, cls, sector, country, *extra = KNOWN[base]
        row.update(name=name, asset_class=cls, sector=sector, country=country)
        if cls == E:
            row["spread_bps"] = DEFAULT_SPREAD_BPS["ETF"]
        if extra:
            row.update(extra[0])

    if stock_cfd:
        row.update(asset_class=C, margin_rate=0.20, name=f"CFD {row['name']}")
    return row


def load_overrides(path: str = DEFAULT_OVERRIDES_PATH) -> pd.DataFrame | None:
    if path and os.path.exists(path):
        try:
            return pd.read_csv(path, dtype={"symbol": str})
        except Exception:
            return None
    return None


def save_overrides(mapping: pd.DataFrame, path: str = DEFAULT_OVERRIDES_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    mapping[MAPPING_COLUMNS].to_csv(path, index=False)


def normalize_mapping(df: pd.DataFrame) -> pd.DataFrame:
    """Porządkuje typy po edycji w UI (liczby, wielkie litery, braki)."""
    out = df.copy()
    for c in MAPPING_COLUMNS:
        if c not in out.columns:
            out[c] = None
    out = out[MAPPING_COLUMNS]
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out = out[out["symbol"].ne("") & out["symbol"].ne("NAN") & out["symbol"].ne("NONE")]
    for c in ("name", "yf_ticker", "stooq_ticker", "sector", "country"):
        out[c] = out[c].fillna("").astype(str).str.strip()
    out["asset_class"] = out["asset_class"].fillna(S).astype(str).str.strip().str.upper()
    out.loc[~out["asset_class"].isin([S, E, C]), "asset_class"] = S
    out["currency"] = out["currency"].fillna("PLN").astype(str).str.strip().str.upper()
    for c, default in (("price_factor", 1.0), ("multiplier", 1.0), ("margin_rate", 1.0), ("spread_bps", 10.0)):
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(default).astype(float)
    out.loc[out["sector"] == "", "sector"] = "Nieznany"
    out.loc[out["country"] == "", "country"] = "Nieznany"
    return out.drop_duplicates("symbol", keep="last").reset_index(drop=True)


def build_mapping(symbols, overrides: pd.DataFrame | None = None) -> pd.DataFrame:
    """Tabela mapowań dla listy symboli XTB; `overrides` (np. poprawki z UI) nadpisują reguły."""
    rows = {s: auto_map(s) for s in sorted({str(s).strip().upper() for s in symbols if str(s).strip()})}
    if overrides is not None and len(overrides):
        for rec in overrides.to_dict("records"):
            sym = str(rec.get("symbol", "")).strip().upper()
            if sym not in rows:
                continue
            for k, v in rec.items():
                if k == "symbol" or k not in MAPPING_COLUMNS or v is None:
                    continue
                if (isinstance(v, float) and pd.isna(v)) or str(v).strip() == "":
                    continue  # puste pole w nadpisaniu = zostaw wartość automatyczną
                rows[sym][k] = v
    df = pd.DataFrame(list(rows.values()), columns=MAPPING_COLUMNS)
    return normalize_mapping(df).sort_values("symbol").reset_index(drop=True)


def unmapped(mapping: pd.DataFrame) -> list[str]:
    """Symbole bez tickera w żadnym źródle albo bez sektora/kraju."""
    m = mapping
    bad = m[(m["yf_ticker"] == "") & (m["stooq_ticker"] == "")]
    return bad["symbol"].tolist()
