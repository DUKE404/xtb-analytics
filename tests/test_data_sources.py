import json

import numpy as np
import pandas as pd
import pytest

from data.cache import Cache
from data.providers import DataUnavailable, LiveMarketData, parse_french_csv, parse_nbp_json, parse_stooq_csv
from data.synthetic import SyntheticMarket

NBP_SAMPLE = {"table": "A", "currency": "dolar amerykański", "code": "USD", "rates": [
    {"no": "001/A/NBP/2025", "effectiveDate": "2025-01-02", "mid": 4.1219},
    {"no": "002/A/NBP/2025", "effectiveDate": "2025-01-03", "mid": 4.1512}]}

STOOQ_PL = "Data,Otwarcie,Najwyzszy,Najnizszy,Zamkniecie,Wolumen\n2025-01-02,61.5,62.9,61.2,62.5,1500000\n2025-01-03,62.5,63,62,62.8,1200000\n"
STOOQ_EN = "Date,Open,High,Low,Close,Volume\n2025-01-02,5903.26,5935.09,5829.53,5868.55,0\n"

FRENCH = """This file was created by CMPT_ME_BEME_OP_INV_RETS_DAILY using the 202509 CRSP database.
The 1-month TBill rate data until 202405 are from Ibbotson Associates.

,Mkt-RF,SMB,HML,RMW,CMA,RF
19630701,   -0.67,    0.00,   -0.34,   -0.01,    0.16,   0.012
19630702,    0.79,   -0.26,    0.26,   -0.07,   -0.20,   0.012
19630703,    0.63,   -0.17,   -0.09,    0.18,   -0.34,   -99.99

Copyright 2025 Eugene F. Fama and Kenneth R. French
"""


def test_parse_nbp():
    s = parse_nbp_json(json.dumps(NBP_SAMPLE))
    assert s.loc["2025-01-03"] == 4.1512 and len(s) == 2
    assert parse_nbp_json({"rates": []}).empty


def test_parse_stooq_pl_and_en():
    df = parse_stooq_csv(STOOQ_PL)
    assert df["close"].tolist() == [62.5, 62.8] and df["volume"].iloc[0] == 1.5e6
    assert parse_stooq_csv(STOOQ_EN)["close"].iloc[0] == 5868.55


@pytest.mark.parametrize("text", ["Brak danych", "No data", "Przekroczony dzienny limit wywolan", "<html>captcha</html>", ""])
def test_parse_stooq_errors(text):
    with pytest.raises(DataUnavailable):
        parse_stooq_csv(text)


def test_parse_french():
    df = parse_french_csv(FRENCH)
    assert list(df.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]
    assert df.loc["1963-07-01", "Mkt-RF"] == pytest.approx(-0.0067)
    assert df.loc["1963-07-02", "RF"] == pytest.approx(0.00012)
    assert np.isnan(df.loc["1963-07-03", "RF"]) and len(df) == 3


def test_cache_fetches_once_and_survives_failure():
    cache = Cache(":memory:")
    calls = []
    idx = pd.bdate_range("2025-01-01", "2025-01-31")

    def fetcher(s, e):
        calls.append((s, e))
        return pd.DataFrame({"close": np.arange(len(idx), dtype=float), "volume": 1.0}, index=idx), "test"

    a, src, warn = cache.cached_fetch("px", "X", "2025-01-01", "2025-01-31", fetcher)
    b, _, _ = cache.cached_fetch("px", "X", "2025-01-10", "2025-01-20", fetcher)
    assert len(calls) == 1 and src == "test" and warn is None
    assert len(a) == len(idx) and b.index.min() == pd.Timestamp("2025-01-10")

    def broken(s, e):
        raise DataUnavailable("brak sieci")

    # szerszy zakres wymaga pobrania; przy błędzie dostajemy cache i ostrzeżenie
    c, _, warn = cache.cached_fetch("px", "X", "2024-12-01", "2025-01-31", broken)
    assert len(c) == len(idx) and "brak sieci" in warn
    with pytest.raises(DataUnavailable):
        cache.cached_fetch("px", "NOWY", "2025-01-01", "2025-01-31", broken)


def test_cache_kv(tmp_path):
    cache = Cache(str(tmp_path / "c.sqlite"))
    cache.set_kv("a", "1")
    assert cache.get_kv("a") == "1" and cache.get_kv("a", max_age_hours=1) == "1" and cache.get_kv("b") is None


def test_live_provider_uses_fallback_and_factor(monkeypatch):
    import data.providers as prov

    idx = pd.bdate_range("2025-01-01", "2025-01-10")

    def no_yahoo(t, s, e):
        raise DataUnavailable("yfinance: brak")

    monkeypatch.setattr(prov, "fetch_yfinance", no_yahoo)
    monkeypatch.setattr(prov, "fetch_stooq", lambda t, s, e: pd.DataFrame({"close": 250.0, "volume": 10.0}, index=idx))
    monkeypatch.setattr(prov, "fetch_nbp", lambda c, s, e: pd.Series(5.0, index=idx))
    live = LiveMarketData(Cache(":memory:"))
    row = {"symbol": "BARC.UK", "yf_ticker": "BARC.L", "stooq_ticker": "barc.uk", "price_factor": 0.01}
    df = live.price_history(row, "2025-01-01", "2025-01-10")
    assert df["close"].iloc[0] == pytest.approx(2.5)               # pensy -> funty
    assert live.sources[next(iter(live.sources))].startswith("stooq")
    assert live.fx_rates("PLN", "2025-01-01", "2025-01-10").eq(1.0).all()
    assert live.fx_rates("GBP", "2025-01-01", "2025-01-10").iloc[-1] == 5.0
    s, cur = live.benchmark("SP500", "2025-01-01", "2025-01-10")
    assert cur == "USD" and len(s) == len(idx)


def test_live_ff_factors_cached(monkeypatch):
    import data.providers as prov

    idx = pd.bdate_range("2025-01-01", "2025-01-10")
    ff = pd.DataFrame(0.001, index=idx, columns=["Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM", "RF"])
    calls = []
    monkeypatch.setattr(prov, "fetch_french_factors", lambda: calls.append(1) or ff)
    live = LiveMarketData(Cache(":memory:"))
    a = live.ff_factors("2025-01-01", "2025-01-10")
    b = live.ff_factors("2025-01-03", "2025-01-10")
    assert len(calls) == 1 and len(a) == len(idx) and b.index.min() == pd.Timestamp("2025-01-03")
    assert a["HML"].iloc[0] == pytest.approx(0.001)


def test_synthetic_market_is_deterministic():
    a, b = SyntheticMarket(), SyntheticMarket()
    assert a.asset("AAPL.US")["close"].equals(b.asset("AAPL.US")["close"])
    assert a.fx["USD"].between(2.5, 6.5).all() and set(a.regime.unique()) == {0, 1}
    s, cur = a.benchmark("WIG20")
    assert cur == "PLN" and s.equals(a.asset("W20")["close"].rename("WIG20"))
    assert a.asset("NIEZNANY.US")["close"].gt(0).all()
