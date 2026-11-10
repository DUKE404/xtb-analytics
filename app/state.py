"""Stan aplikacji: wczytanie eksportu, mapowanie, dane rynkowe i odtworzony portfel.

Wynik trzymamy w st.session_state pod kluczem zależnym od pliku, mapowania i źródła danych,
więc ciężka praca wykonuje się tylko wtedy, gdy coś się faktycznie zmieni.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pandas as pd
import streamlit as st

from core.portfolio import Portfolio
from data.loader import MarketBundle, build_portfolio, load_market
from data.mapping import build_mapping, load_overrides, save_overrides
from data.sample_generator import ensure_sample
from data.xtb_parser import XTBExport, XTBFormatError, parse_export

SOURCE_DEMO = "Dane przykładowe (demo, offline)"
SOURCE_UPLOAD = "Mój eksport z XTB"


@dataclass
class Ctx:
    export: XTBExport
    mapping: pd.DataFrame
    market: MarketBundle
    pf: Portfolio
    provider: object
    is_demo: bool
    rf: float
    bench: str
    key: str

    @property
    def meta(self) -> pd.DataFrame:
        return self.pf.meta


def _digest(*parts) -> str:
    h = hashlib.md5()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode())
    return h.hexdigest()


def _provider(is_demo: bool):
    if is_demo:
        from data.synthetic import SyntheticMarketData
        return SyntheticMarketData()
    from data.providers import LiveMarketData
    return LiveMarketData()


def source() -> str:
    return st.session_state.get("source", SOURCE_DEMO)


def set_uploaded(files: list[tuple[str, bytes]]) -> None:
    st.session_state["upload_files"] = files


def overrides_key(is_demo: bool) -> str:
    return "overrides_demo" if is_demo else "overrides_live"


def set_overrides(df: pd.DataFrame, is_demo: bool) -> None:
    st.session_state[overrides_key(is_demo)] = df
    if not is_demo:
        save_overrides(df)


def get_context(rf: float, bench: str) -> Ctx | None:
    """Zwraca gotowy kontekst analizy albo None (brak pliku / błąd - komunikat w session_state['load_error'])."""
    is_demo = source() == SOURCE_DEMO
    st.session_state["load_error"] = None
    if is_demo:
        with open(ensure_sample(), "rb") as fh:
            files = [("xtb_sample_export.xlsx", fh.read())]
    else:
        files = st.session_state.get("upload_files") or []
        if not files:
            return None

    ok = overrides_key(is_demo)
    if ok not in st.session_state:
        st.session_state[ok] = None if is_demo else load_overrides()
    overrides = st.session_state[ok]
    ov_sig = "" if overrides is None else overrides.to_csv(index=False)
    key = _digest(is_demo, *[n for n, _ in files], *[b for _, b in files], ov_sig)

    cached = st.session_state.get("_ctx")
    if cached is not None and cached.key == key:
        cached.rf, cached.bench = rf, bench
        return cached

    try:
        export = parse_export(files)
    except XTBFormatError as exc:
        st.session_state["load_error"] = str(exc)
        return None
    mapping = build_mapping(export.symbols, overrides)
    provider = _provider(is_demo)
    with st.spinner("Pobieram notowania i odtwarzam portfel dzień po dniu…"):
        market = load_market(export, mapping, provider)
        try:
            pf = build_portfolio(export, mapping, market)
        except Exception as exc:
            st.session_state["load_error"] = f"Nie udało się odtworzyć portfela: {exc}"
            return None
    ctx = Ctx(export=export, mapping=mapping, market=market, pf=pf, provider=provider, is_demo=is_demo,
              rf=rf, bench=bench, key=key)
    st.session_state["_ctx"] = ctx
    return ctx
