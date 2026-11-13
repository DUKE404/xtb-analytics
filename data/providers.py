"""Dostawcy danych rynkowych na żywo: yfinance (z fallbackiem na Stooq), kursy NBP (tabela A)
oraz czynniki Fama-French z biblioteki Kennetha Frencha. Wszystko przechodzi przez lokalny cache.
"""
from __future__ import annotations

import io
import json
import os
import zipfile

import numpy as np
import pandas as pd

from data.cache import Cache

NBP_URL = "https://api.nbp.pl/api/exchangerates/rates/a/{code}/{start}/{end}/?format=json"
STOOQ_URL = "https://stooq.pl/q/d/l/?s={ticker}&d1={d1}&d2={d2}&i=d"
FF5_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
MOM_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"
HEADERS = {"User-Agent": "Mozilla/5.0 (xtb-analytics; lokalna aplikacja analityczna)"}

# benchmark -> (kandydaci Yahoo, ticker Stooq, waluta)
BENCHMARKS = {
    "WIG20": ("WIG20.WA|^WIG20", "wig20", "PLN"),
    "WIG": ("WIG.WA|^WIG", "wig", "PLN"),
    "SP500": ("^GSPC", "^spx", "USD"),
    "NDX": ("^NDX", "^ndx", "USD"),
    "DAX": ("^GDAXI", "^dax", "EUR"),
    "MSCIW": ("URTH", "urth.us", "USD"),
    "GOLD": ("GC=F", "xauusd", "USD"),
    "OIL": ("BZ=F", "cb.f", "USD"),
}
BENCHMARK_NAMES = {"WIG20": "WIG20", "WIG": "WIG", "SP500": "S&P 500", "MSCIW": "MSCI World ETF", "NDX": "Nasdaq 100",
                   "DAX": "DAX", "GOLD": "Złoto", "OIL": "Ropa Brent"}


def benchmark_label(key: str, base: str = "PLN", currency: str | None = None) -> str:
    """Nazwa benchmarku z dopiskiem waluty, gdy został przeliczony (np. "S&P 500 (w PLN)")."""
    name = BENCHMARK_NAMES.get(key, key)
    return name if currency in (None, base) else f"{name} (w {base})"


BENCHMARK_LABELS = {"WIG20": "WIG20", "WIG": "WIG", "SP500": "S&P 500 (w PLN)", "MSCIW": "MSCI World ETF (w PLN)",
                    "NDX": "Nasdaq 100 (w PLN)", "DAX": "DAX (w PLN)", "GOLD": "Złoto (w PLN)", "OIL": "Ropa Brent (w PLN)"}


class DataUnavailable(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Parsery odpowiedzi (czyste funkcje - testowane na próbkach formatów)
# --------------------------------------------------------------------------- #

def parse_nbp_json(payload) -> pd.Series:
    """Odpowiedź API NBP (tabela A, jedna waluta) -> Series kursów średnich indeksowana datą."""
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    rates = payload.get("rates", [])
    if not rates:
        return pd.Series(dtype=float)
    s = pd.Series({pd.Timestamp(r["effectiveDate"]): float(r["mid"]) for r in rates}).sort_index()
    s.index.name = "date"
    return s


def parse_stooq_csv(text: str) -> pd.DataFrame:
    """CSV ze Stooq (nagłówki PL lub EN) -> DataFrame[close, volume]."""
    head = text.strip()[:200].lower()
    if not head or "brak danych" in head or "no data" in head:
        raise DataUnavailable("Stooq: brak danych dla tickera")
    if "limit" in head or "<html" in head or "apikey" in head:
        raise DataUnavailable("Stooq: przekroczony limit pobrań lub wymagany klucz")
    df = pd.read_csv(io.StringIO(text))
    cols = {c.lower().strip(): c for c in df.columns}
    date_c = cols.get("date") or cols.get("data")
    close_c = cols.get("close") or cols.get("zamkniecie")
    vol_c = cols.get("volume") or cols.get("wolumen")
    if not date_c or not close_c:
        raise DataUnavailable("Stooq: nieoczekiwany format odpowiedzi")
    out = pd.DataFrame({"close": pd.to_numeric(df[close_c], errors="coerce").values,
                        "volume": pd.to_numeric(df[vol_c], errors="coerce").values if vol_c else np.nan},
                       index=pd.to_datetime(df[date_c]))
    out.index.name = "date"
    return out.dropna(subset=["close"]).sort_index()


def parse_french_csv(text: str) -> pd.DataFrame:
    """Plik CSV z biblioteki K. Frencha (dzienny) -> DataFrame w ułamkach dziesiętnych."""
    lines = text.splitlines()
    hdr = next((i for i, ln in enumerate(lines) if ln.strip().startswith(",")), None)
    if hdr is None:
        raise DataUnavailable("French: nie znaleziono nagłówka w pliku")
    names = [c.strip() for c in lines[hdr].split(",")][1:]
    rows = []
    for ln in lines[hdr + 1:]:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) < len(names) + 1 or not (parts[0].isdigit() and len(parts[0]) == 8):
            if rows:
                break
            continue
        rows.append([parts[0]] + parts[1:len(names) + 1])
    if not rows:
        raise DataUnavailable("French: brak wierszy z danymi")
    df = pd.DataFrame(rows, columns=["date"] + names)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.set_index("date").apply(pd.to_numeric, errors="coerce")
    df = df.where(df > -99)  # -99.99 = brak danych
    return df / 100.0


# --------------------------------------------------------------------------- #
# Pobieranie
# --------------------------------------------------------------------------- #

def _http_get(url: str, timeout: float = 20.0):
    import requests

    r = requests.get(url, headers=HEADERS, timeout=timeout)
    return r


def fetch_yfinance(ticker: str, start, end) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise DataUnavailable("biblioteka yfinance nie jest zainstalowana") from exc
    try:
        h = yf.Ticker(ticker).history(start=pd.Timestamp(start).strftime("%Y-%m-%d"),
                                      end=(pd.Timestamp(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                                      auto_adjust=False, actions=False)
    except Exception as exc:
        raise DataUnavailable(f"yfinance: {exc}") from exc
    if h is None or len(h) == 0 or "Close" not in h.columns:
        raise DataUnavailable(f"yfinance: brak danych dla {ticker}")
    idx = pd.DatetimeIndex(h.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out = pd.DataFrame({"close": h["Close"].values,
                        "volume": h["Volume"].values if "Volume" in h.columns else np.nan}, index=idx.normalize())
    out.index.name = "date"
    out = out.dropna(subset=["close"])
    out = out[~out.index.duplicated(keep="last")]
    if out.empty:
        raise DataUnavailable(f"yfinance: brak danych dla {ticker}")
    return out


def fetch_stooq(ticker: str, start, end) -> pd.DataFrame:
    url = STOOQ_URL.format(ticker=ticker, d1=pd.Timestamp(start).strftime("%Y%m%d"),
                           d2=pd.Timestamp(end).strftime("%Y%m%d"))
    try:
        r = _http_get(url)
    except Exception as exc:
        raise DataUnavailable(f"Stooq: {exc}") from exc
    if r.status_code != 200:
        raise DataUnavailable(f"Stooq: HTTP {r.status_code}")
    return parse_stooq_csv(r.text)


def fetch_nbp(code: str, start, end) -> pd.Series:
    """Kursy średnie NBP (tabela A). API przyjmuje maks. 367 dni na zapytanie - dzielimy zakres."""
    start, end = pd.Timestamp(start).normalize(), min(pd.Timestamp(end).normalize(), pd.Timestamp.today().normalize())
    parts, cur = [], start
    while cur <= end:
        stop = min(cur + pd.Timedelta(days=360), end)
        url = NBP_URL.format(code=code.lower(), start=cur.strftime("%Y-%m-%d"), end=stop.strftime("%Y-%m-%d"))
        try:
            r = _http_get(url)
        except Exception as exc:
            raise DataUnavailable(f"NBP: {exc}") from exc
        if r.status_code == 200:
            parts.append(parse_nbp_json(r.json()))
        elif r.status_code != 404:  # 404 = brak notowań w zakresie (np. sam weekend)
            raise DataUnavailable(f"NBP: HTTP {r.status_code} dla {code}")
        cur = stop + pd.Timedelta(days=1)
    parts = [p for p in parts if len(p)]
    if not parts:
        raise DataUnavailable(f"NBP: brak kursów {code} w zakresie {start.date()} - {end.date()}")
    return pd.concat(parts).sort_index()


def fetch_french_factors() -> pd.DataFrame:
    def _zip_csv(url):
        r = _http_get(url, timeout=40)
        if r.status_code != 200:
            raise DataUnavailable(f"French: HTTP {r.status_code}")
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            return z.read(z.namelist()[0]).decode("latin-1")

    ff = parse_french_csv(_zip_csv(FF5_URL))
    mom = parse_french_csv(_zip_csv(MOM_URL))
    mom.columns = ["MOM"]
    out = ff.join(mom, how="inner")
    return out[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM", "RF"]].dropna()


# --------------------------------------------------------------------------- #
# Dostawca "na żywo"
# --------------------------------------------------------------------------- #

def _stmt_rows(df, names) -> list[dict]:
    """Wiersz rocznego sprawozdania -> [{"year": 2023, "value": ...}] rosnąco. `names`: możliwe nazwy pozycji."""
    if df is None or not hasattr(df, "index") or not len(df):
        return []
    for name in names:
        if name in df.index:
            row = df.loc[name]
            out = []
            for when, v in row.items():
                try:
                    v, year = float(v), pd.Timestamp(when).year
                except (TypeError, ValueError):
                    continue
                if v == v:
                    out.append({"year": int(year), "value": v})
            return sorted(out, key=lambda r: r["year"])
    return []


def _estimate_rows(df) -> list[dict]:
    """Tabela prognoz analityków (yfinance: indeks 0q, +1q, 0y, +1y) -> lista słowników."""
    if df is None or not hasattr(df, "iterrows") or not len(df):
        return []
    out = []
    for period, r in df.iterrows():
        def num(*keys):
            for k in keys:
                if k in r.index:
                    try:
                        v = float(r[k])
                    except (TypeError, ValueError):
                        continue
                    if v == v:
                        return v
            return None
        out.append({"period": str(period), "avg": num("avg"), "low": num("low"), "high": num("high"),
                    "growth": num("growth"), "analysts": num("numberOfAnalysts"),
                    "year_ago": num("yearAgoRevenue", "yearAgoEps")})
    return out


def fetch_fundamentals(ticker: str) -> dict:
    """Dane o spółce z Yahoo Finance: wycena, sprawozdania roczne, prognozy i ceny docelowe analityków.

    Każdy fragment jest pobierany osobno - brak jednego (np. prognoz dla małej spółki) nie psuje reszty.
    """
    try:
        import yfinance as yf
    except ImportError as exc:  # pragma: no cover
        raise DataUnavailable("biblioteka yfinance nie jest zainstalowana") from exc
    t = yf.Ticker(ticker)

    def safe(fn, default=None):
        try:
            v = fn()
            return default if v is None else v
        except Exception:
            return default

    info = safe(lambda: dict(t.info), {}) or {}
    if not info.get("marketCap") and not info.get("currentPrice") and not info.get("regularMarketPrice"):
        raise DataUnavailable(f"yfinance: brak danych o spółce {ticker}")
    g = lambda *keys: next((info[k] for k in keys if info.get(k) is not None), None)
    targets = safe(lambda: dict(t.analyst_price_targets), {}) or {}
    income, cash = safe(lambda: t.income_stmt), safe(lambda: t.cashflow)
    return {
        "ticker": ticker, "name": g("longName", "shortName"), "sector": g("sector"), "industry": g("industry"),
        "currency": g("currency"), "financial_currency": g("financialCurrency", "currency"),
        "price": g("currentPrice", "regularMarketPrice", "previousClose"),
        "market_cap": g("marketCap"), "enterprise_value": g("enterpriseValue"), "shares": g("sharesOutstanding"),
        "total_cash": g("totalCash"), "total_debt": g("totalDebt"),
        "pe": g("trailingPE"), "forward_pe": g("forwardPE"), "ps": g("priceToSalesTrailing12Months"),
        "ev_ebitda": g("enterpriseToEbitda"), "beta": g("beta"), "dividend_yield": g("dividendYield"),
        "revenue_ttm": g("totalRevenue"), "fcf_ttm": g("freeCashflow"), "ocf_ttm": g("operatingCashflow"),
        "revenue_growth": g("revenueGrowth"), "profit_margin": g("profitMargins"), "operating_margin": g("operatingMargins"),
        "targets": {"low": targets.get("low", g("targetLowPrice")), "mean": targets.get("mean", g("targetMeanPrice")),
                    "median": targets.get("median", g("targetMedianPrice")), "high": targets.get("high", g("targetHighPrice")),
                    "analysts": g("numberOfAnalystOpinions")},
        "recommendation": g("recommendationKey"), "recommendation_mean": g("recommendationMean"),
        "revenue": _stmt_rows(income, ["Total Revenue", "Operating Revenue"]),
        "net_income": _stmt_rows(income, ["Net Income", "Net Income Common Stockholders"]),
        "fcf": _stmt_rows(cash, ["Free Cash Flow"]),
        "ocf": _stmt_rows(cash, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]),
        "capex": _stmt_rows(cash, ["Capital Expenditure"]),
        "revenue_estimates": _estimate_rows(safe(lambda: t.revenue_estimate)),
        "eps_estimates": _estimate_rows(safe(lambda: t.earnings_estimate)),
        "source": "Yahoo Finance", "fetched": pd.Timestamp.now().strftime("%Y-%m-%d"),
    }


def _ts(v):
    """Data z yfinance (date, datetime, Timestamp albo sekundy epoki) -> 'RRRR-MM-DD' lub None."""
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if v != v or v <= 0:
                return None
            return pd.Timestamp(int(v), unit="s").strftime("%Y-%m-%d")
        t = pd.Timestamp(v)
        return None if pd.isna(t) else t.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return None


def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v else None


def parse_events(ticker: str, cal, info: dict | None, history=None) -> dict:
    """Terminy i oczekiwania z yfinance: Ticker.calendar (słownik), Ticker.info, Ticker.earnings_history."""
    cal = cal if isinstance(cal, dict) else {}
    info = info or {}
    dates = cal.get("Earnings Date") or []
    dates = [dates] if not isinstance(dates, (list, tuple)) else list(dates)
    dates = [x for x in (_ts(d) for d in dates) if x]
    if not dates:
        dates = [x for x in (_ts(info.get(k)) for k in ("earningsTimestampStart", "earningsTimestamp")) if x][:1]
    fy = _ts(info.get("nextFiscalYearEnd")) or _ts(info.get("lastFiscalYearEnd"))
    last = {}
    if history is not None and hasattr(history, "iterrows") and len(history):
        h = history.copy()
        try:
            h.index = pd.to_datetime(h.index)
            h = h.sort_index()
        except (TypeError, ValueError):
            pass
        row = h.iloc[-1]
        last = {"last_quarter_end": _ts(h.index[-1]), "last_eps_actual": _f(row.get("epsActual")),
                "last_eps_estimate": _f(row.get("epsEstimate")), "last_surprise": _f(row.get("surprisePercent"))}
    return {
        "ticker": ticker, "earnings": sorted(set(dates)),
        "estimated": bool(info.get("isEarningsDateEstimate", len(dates) > 1)),
        "eps_avg": _f(cal.get("Earnings Average")), "eps_low": _f(cal.get("Earnings Low")), "eps_high": _f(cal.get("Earnings High")),
        "rev_avg": _f(cal.get("Revenue Average")), "rev_low": _f(cal.get("Revenue Low")), "rev_high": _f(cal.get("Revenue High")),
        "ex_dividend": _ts(cal.get("Ex-Dividend Date")) or _ts(info.get("exDividendDate")),
        "dividend_date": _ts(cal.get("Dividend Date")) or _ts(info.get("dividendDate")),
        "dividend_rate": _f(info.get("dividendRate")),
        "fy_end_month": int(fy[5:7]) if fy else None, "currency": info.get("currency"),
        **{k: None for k in ("last_quarter_end", "last_eps_actual", "last_eps_estimate", "last_surprise")}, **last,
        "source": "Yahoo Finance", "fetched": pd.Timestamp.now().strftime("%Y-%m-%d"),
    }


def parse_quote(info: dict | None) -> dict | None:
    """Bieżące notowanie z Ticker.info: cena, poprzednie zamknięcie, czas notowania (ISO, UTC), waluta."""
    info = info or {}
    price = next((_f(info.get(k)) for k in ("currentPrice", "regularMarketPrice") if _f(info.get(k))), None)
    prev = next((_f(info.get(k)) for k in ("regularMarketPreviousClose", "previousClose") if _f(info.get(k))), None)
    if not price or not prev:
        return None
    when = info.get("regularMarketTime")
    try:
        when = pd.Timestamp(int(when), unit="s").strftime("%Y-%m-%dT%H:%M:%SZ") if when else None
    except (TypeError, ValueError, OverflowError):
        when = None
    return {"price": price, "prev_close": prev, "time": when, "currency": info.get("currency"),
            "state": info.get("marketState"), "source": "Yahoo Finance"}


def parse_news(items, limit: int = 6) -> list[dict]:
    """Nagłówki z Ticker.news. Obsługuje nowy kształt ({"content": {...}}) i dawny (płaski słownik).

    Zostają tylko: tytuł, wydawca, czas i odnośnik http(s) - treści artykułów nie pobieramy.
    """
    out = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        c = it.get("content") if isinstance(it.get("content"), dict) else it
        title = str(c.get("title") or "").strip()
        if not title:
            continue
        url = ""
        for key in ("canonicalUrl", "clickThroughUrl"):
            u = c.get(key)
            if isinstance(u, dict) and u.get("url"):
                url = str(u["url"])
                break
        url = url or str(c.get("link") or "")
        if not url.lower().startswith(("https://", "http://")):
            url = ""
        prov = c.get("provider")
        source = (prov.get("displayName") if isinstance(prov, dict) else None) or c.get("publisher") or ""
        when = c.get("pubDate") or c.get("displayTime")
        try:
            if when:
                t = pd.Timestamp(when)
                t = t.tz_convert("UTC") if t.tzinfo else t
            elif c.get("providerPublishTime"):
                t = pd.Timestamp(int(c["providerPublishTime"]), unit="s")
            else:
                t = None
            when = t.strftime("%Y-%m-%dT%H:%M:%SZ") if t is not None and not pd.isna(t) else None
        except (TypeError, ValueError, OverflowError):
            when = None
        out.append({"title": title[:220], "source": str(source)[:60], "time": when, "url": url})
    out.sort(key=lambda r: r["time"] or "", reverse=True)
    return out[:limit]


class LiveMarketData:
    name = "na żywo (yfinance → Stooq, NBP)"
    is_synthetic = False
    max_workers = int(os.environ.get("FETCH_WORKERS", "4") or 1)   # ile instrumentów pobierać naraz

    def __init__(self, cache: Cache | None = None):
        self.cache = cache or Cache()
        self.warnings: list[str] = []
        self.sources: dict[str, str] = {}

    def _prices(self, key, yf_candidates: str, stooq_ticker: str, start, end, factor: float = 1.0):
        def fetcher(s, e):
            errors = []
            for t in [x.strip() for x in str(yf_candidates or "").split("|") if x.strip()]:
                try:
                    return fetch_yfinance(t, s, e), f"yfinance:{t}"
                except DataUnavailable as exc:
                    errors.append(str(exc))
            if stooq_ticker:
                try:
                    return fetch_stooq(stooq_ticker, s, e), f"stooq:{stooq_ticker}"
                except DataUnavailable as exc:
                    errors.append(str(exc))
            raise DataUnavailable("; ".join(errors) or "brak tickera w mapowaniu")

        df, source, warn = self.cache.cached_fetch("px", key, start, end, fetcher)
        if warn:
            self.warnings.append(warn)
        self.sources[key] = source
        if factor != 1.0:
            df = df.assign(close=df["close"] * factor)
        return df

    BRIEF_TTL_HOURS = {"quote": 10 / 60, "events": 12.0, "news": 0.5}

    def brief(self, row, today=None, parts=("quote", "events", "news")) -> dict:
        """Krótka karta spółki: bieżące notowanie, terminy raportów i dywidend, nagłówki wiadomości.

        Każda część ma własny czas życia w cache (notowanie 10 min, wiadomości 30 min, terminy 12 h).
        Gdy pobranie się nie uda, wraca ostatnia zapisana wersja; brakująca część ma wartość None.
        """
        ticker = next((x.strip() for x in str(row.get("yf_ticker") or "").split("|") if x.strip()), "")
        if not ticker:
            raise DataUnavailable("brak tickera Yahoo w tabeli mapowań")
        out = {p: None for p in ("quote", "events", "news")}
        missing = []
        for part in parts:
            hit = self.cache.get_kv(f"{part}:{ticker}", max_age_hours=self.BRIEF_TTL_HOURS[part])
            if hit:
                out[part] = json.loads(hit)
            else:
                missing.append(part)
        if missing:
            try:
                import yfinance as yf
                t = yf.Ticker(ticker)
            except Exception:                      # brak biblioteki albo sieci: zostaje to, co jest w cache
                t = None

            def safe(fn):
                try:
                    return fn()
                except Exception:
                    return None

            info = safe(lambda: dict(t.info)) if t is not None and {"quote", "events"} & set(missing) else None
            fresh = {}
            if "quote" in missing:
                fresh["quote"] = parse_quote(info)
            if "events" in missing and t is not None:
                cal = safe(lambda: t.calendar)
                fresh["events"] = parse_events(ticker, cal, info) if (cal or info) else None
            if "news" in missing and t is not None:
                items = safe(lambda: t.news)
                fresh["news"] = parse_news(items) if items is not None else None
            for part in missing:
                if fresh.get(part) is not None:
                    out[part] = fresh[part]
                    self.cache.set_kv(f"{part}:{ticker}", json.dumps(fresh[part]))
                else:
                    old = self.cache.get_kv(f"{part}:{ticker}")       # stara wersja lepsza niż nic
                    out[part] = json.loads(old) if old else None
        out["ticker"] = ticker
        return out

    def fundamentals(self, row) -> dict:
        """Dane o spółce (cache 24 h)."""
        ticker = next((x.strip() for x in str(row.get("yf_ticker") or "").split("|") if x.strip()), "")
        if not ticker:
            raise DataUnavailable("brak tickera Yahoo w tabeli mapowań")
        key = f"fund:{ticker}"
        hit = self.cache.get_kv(key, max_age_hours=24.0)
        if hit:
            return json.loads(hit)
        try:
            data = fetch_fundamentals(ticker)
        except DataUnavailable:
            old = self.cache.get_kv(key)
            if old:
                return json.loads(old)
            raise
        self.cache.set_kv(key, json.dumps(data, default=lambda o: None))
        return data

    def price_history(self, row, start, end) -> pd.DataFrame:
        key = f"{row['symbol']}|{row.get('yf_ticker', '')}|{row.get('stooq_ticker', '')}"
        return self._prices(key, row.get("yf_ticker", ""), row.get("stooq_ticker", ""), start, end,
                            float(row.get("price_factor", 1.0) or 1.0))

    def fx_rates(self, currency: str, start, end) -> pd.Series:
        cur = currency.upper()
        idx = pd.bdate_range(start, end)
        if cur == "PLN":
            return pd.Series(1.0, index=idx, name="PLN")
        scale = 1.0
        if cur == "GBX":
            cur, scale = "GBP", 0.01

        def fetcher(s, e):
            return fetch_nbp(cur, s, e).to_frame("close"), "NBP tabela A"

        # kilka dni zapasu wstecz, żeby zawsze mieć kurs z dnia poprzedzającego pierwszą transakcję
        df, _, warn = self.cache.cached_fetch("fx", cur, pd.Timestamp(start) - pd.Timedelta(days=10), end, fetcher)
        if warn:
            self.warnings.append(warn)
        return (df["close"] * scale).rename(currency.upper())

    def benchmark(self, key: str, start, end) -> tuple[pd.Series, str]:
        yf_c, stq, cur = BENCHMARKS[key]
        df = self._prices(f"bench:{key}", yf_c, stq, start, end)
        return df["close"].rename(key), cur

    def markets(self, today=None) -> dict:
        """Dane do zakładki „Rynki” (rentowności USA, notowania, kalendarz makro) - patrz data/markets.py."""
        from data.markets import live_markets
        return live_markets(self.cache, today, workers=self.max_workers)

    def ff_factors(self, start, end) -> pd.DataFrame:
        raw = self.cache.get_kv("ff_factors", max_age_hours=24 * 7)
        if raw is None:
            try:
                df = fetch_french_factors()
                self.cache.set_kv("ff_factors", df.to_json(orient="split", date_format="iso"))
            except Exception as exc:
                raw = self.cache.get_kv("ff_factors")
                if raw is None:
                    raise DataUnavailable(f"Nie udało się pobrać czynników Fama-French: {exc}") from exc
                self.warnings.append("Czynniki Fama-French: używam starszej kopii z cache.")
                df = pd.read_json(io.StringIO(raw), orient="split")
        else:
            df = pd.read_json(io.StringIO(raw), orient="split")
        df.index = pd.to_datetime(df.index).tz_localize(None) if getattr(df.index, "tz", None) else pd.to_datetime(df.index)
        return df.loc[pd.Timestamp(start):pd.Timestamp(end)]


# Tłumaczenie sektorów GICS z Yahoo na nazwy używane w aplikacji
YAHOO_SECTORS = {
    "Technology": "Technologia", "Financial Services": "Finanse", "Healthcare": "Ochrona zdrowia",
    "Consumer Cyclical": "Dobra cykliczne", "Consumer Defensive": "Dobra podstawowe", "Energy": "Energia",
    "Basic Materials": "Surowce", "Industrials": "Przemysł", "Utilities": "Użyteczność publiczna",
    "Communication Services": "Telekomunikacja", "Real Estate": "Nieruchomości",
}


def enrich_from_yahoo(mapping: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Uzupełnia sektor / nazwę dla wierszy z sektorem 'Nieznany' (best effort, wymaga sieci)."""
    notes, out = [], mapping.copy()
    try:
        import yfinance as yf
    except ImportError:
        return out, ["yfinance nie jest zainstalowane - nie mogę uzupełnić sektorów."]
    for i, row in out.iterrows():
        if row["sector"] != "Nieznany" or not row["yf_ticker"]:
            continue
        try:
            info = yf.Ticker(row["yf_ticker"].split("|")[0]).info or {}
        except Exception as exc:
            notes.append(f"{row['symbol']}: {exc}")
            continue
        sector = info.get("sector")
        if info.get("quoteType") == "ETF":
            out.at[i, "asset_class"] = "ETF"
            out.at[i, "sector"] = "ETF"
        elif sector:
            out.at[i, "sector"] = YAHOO_SECTORS.get(sector, sector)
        if info.get("shortName") and row["name"] == row["symbol"]:
            out.at[i, "name"] = info["shortName"]
    return out, notes
