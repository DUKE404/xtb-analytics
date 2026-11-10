"""Zakładka Import: wgranie eksportu, kontrola jakości danych i edytowalna tabela mapowań tickerów."""
from __future__ import annotations

import streamlit as st

from app import state, ui
from data.mapping import MAPPING_COLUMNS, normalize_mapping, unmapped

COLUMN_HELP = {
    "symbol": "Symbol z XTB (nie zmieniaj).",
    "name": "Nazwa instrumentu.",
    "asset_class": "STOCK = akcje, ETF, CFD = kontrakt na różnice kursowe.",
    "yf_ticker": "Ticker w Yahoo Finance (np. PKN.WA, AAPL, ^GSPC). Kilku kandydatów oddziel znakiem |.",
    "stooq_ticker": "Ticker w Stooq (np. pkn, aapl.us, ^spx) - źródło zapasowe.",
    "currency": "Waluta notowań (dla CFD: waluta, w której liczony jest wynik).",
    "price_factor": "Mnożnik ceny ze źródła, np. 0.01 gdy źródło podaje pensy zamiast funtów.",
    "multiplier": "Tylko CFD: wartość 1 punktu na 1 lot (np. US500 = 50).",
    "margin_rate": "Tylko CFD: wymagany depozyt jako ułamek nominału (0.05 = 5%).",
    "spread_bps": "Szacowany spread w punktach bazowych (1 pb = 0,01%) - do rozbicia kosztów.",
    "sector": "Sektor - używany w ekspozycjach i atrybucji.",
    "country": "Kraj - używany w ekspozycjach i atrybucji.",
}


def render(ctx) -> None:
    st.subheader("Import danych z xStation 5")
    is_demo = state.source() == state.SOURCE_DEMO

    if is_demo:
        st.info("Pracujesz na **danych przykładowych** (ok. 2 lata historii: spółki z GPW i USA, ETF-y, CFD na indeksy, "
                "dywidendy, wpłaty). Notowania w trybie demo są syntetyczne i działają bez internetu. "
                "Aby przeanalizować własny portfel, wybierz w panelu bocznym „Mój eksport z XTB”.")
    else:
        up = st.file_uploader("Wgraj eksport z xStation 5 (jeden plik .xlsx albo kilka plików .csv)",
                              type=["xlsx", "xls", "csv"], accept_multiple_files=True,
                              help="Plik zostaje na Twoim komputerze - aplikacja działa lokalnie.")
        if up:
            files = [(f.name, f.getvalue()) for f in up]
            if files != st.session_state.get("upload_files"):
                state.set_uploaded(files)
                st.rerun()
        with st.expander("Jak wyeksportować pliki z xStation 5?", expanded=ctx is None):
            st.markdown(
                "1. Zaloguj się do **xStation 5** (wersja przeglądarkowa lub desktopowa).\n"
                "2. W dolnym panelu otwórz zakładkę **Historia**.\n"
                "3. Kliknij ikonę **Eksportuj** (strzałka w dół / „Export button”) po prawej stronie panelu.\n"
                "4. Wybierz **cały dostępny zakres dat** (od otwarcia rachunku) i zaznacz wszystkie raporty: "
                "*Zamknięte pozycje*, *Otwarte pozycje*, *Operacje gotówkowe*.\n"
                "5. Pobierz plik **.xlsx** i wgraj go powyżej. Jeśli masz osobne pliki CSV, wgraj je wszystkie naraz.\n\n"
                "Im pełniejsza historia operacji gotówkowych, tym dokładniejsze stopy zwrotu i podatki."
            )

    err = st.session_state.get("load_error")
    if err:
        st.error(err)
    if ctx is None:
        return

    exp, pf = ctx.export, ctx.pf
    summary = exp.summary()
    ui.metric_row([
        ("Zamknięte pozycje", ui.num(summary["zamknięte pozycje"], 0), None),
        ("Otwarte pozycje", ui.num(summary["otwarte pozycje"], 0), None),
        ("Operacje gotówkowe", ui.num(summary["operacje gotówkowe"], 0), None),
        ("Instrumenty", ui.num(summary["instrumenty"], 0), None),
        ("Stan na", summary["stan na"] or "–", None),
    ])
    with st.expander("Co rozpoznano w pliku", expanded=False):
        for n in exp.notes:
            st.markdown(f"- {n}")
    ui.warn_list(pf.warnings)

    # --- kontrola spójności: gotówka i wycena
    c1, c2, c3 = st.columns(3)
    ui.metric(c1, "Gotówka wg operacji", ui.pln(pf.cash.iloc[-1]), "Wartość portfela")
    ui.metric(c2, "Wartość pozycji", ui.pln(pf.value.iloc[-1].sum()), "Wartość portfela")
    ui.metric(c3, "Wartość portfela", ui.pln(pf.equity.iloc[-1]), "Wartość portfela")
    ui.explain("Porównaj „Wartość portfela” z polem Equity w xStation z dnia eksportu. Rozbieżność zwykle oznacza "
               "niepełny zakres dat w eksporcie albo błędny ticker w tabeli poniżej.")

    # --- mapowanie tickerów
    st.markdown("#### Mapowanie tickerów")
    bad = unmapped(ctx.mapping)
    if bad:
        st.warning("Brak tickera dla: " + ", ".join(bad) + ". Uzupełnij kolumny yf_ticker lub stooq_ticker.")
    st.caption("Symbole XTB są automatycznie tłumaczone na tickery Yahoo Finance i Stooq. Jeśli wycena którejś pozycji "
               "się nie zgadza albo brakuje sektora, popraw wiersz i kliknij „Zapisz i przelicz”.")
    cfg = {
        "symbol": st.column_config.TextColumn("symbol", disabled=True, help=COLUMN_HELP["symbol"]),
        "asset_class": st.column_config.SelectboxColumn("asset_class", options=["STOCK", "ETF", "CFD"],
                                                        help=COLUMN_HELP["asset_class"]),
        "price_factor": st.column_config.NumberColumn("price_factor", format="%.4f", help=COLUMN_HELP["price_factor"]),
        "multiplier": st.column_config.NumberColumn("multiplier", help=COLUMN_HELP["multiplier"]),
        "margin_rate": st.column_config.NumberColumn("margin_rate", format="%.4f", help=COLUMN_HELP["margin_rate"]),
        "spread_bps": st.column_config.NumberColumn("spread_bps", format="%.1f", help=COLUMN_HELP["spread_bps"]),
    }
    for c in ("name", "yf_ticker", "stooq_ticker", "currency", "sector", "country"):
        cfg[c] = st.column_config.TextColumn(c, help=COLUMN_HELP[c])
    edited = ui.editor(ctx.mapping[MAPPING_COLUMNS], key=f"mapping_editor_{ctx.key[:8]}", column_config=cfg,
                       hide_index=True, num_rows="fixed")
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Zapisz i przelicz", type="primary"):
        state.set_overrides(normalize_mapping(edited), ctx.is_demo)
        st.rerun()
    if b2.button("Przywróć automatyczne"):
        state.set_overrides(edited.iloc[0:0], ctx.is_demo)
        st.rerun()
    if not ctx.is_demo:
        src = getattr(ctx.provider, "sources", {})
        if src:
            with st.expander("Skąd pobrano notowania"):
                for k, v in sorted(src.items()):
                    st.markdown(f"- `{k.split('|')[0]}` → {v}")

    # --- podgląd danych
    st.markdown("#### Podgląd wczytanych danych")
    t1, t2, t3 = st.tabs(["Otwarte pozycje", "Zamknięte pozycje", "Operacje gotówkowe"])
    with t1:
        ui.table(exp.open, height=320)
    with t2:
        ui.table(exp.closed, height=320)
    with t3:
        by_kind = exp.cash.groupby("kind")["amount"].agg(["count", "sum"]).rename(
            columns={"count": "liczba", "sum": "suma PLN"}).sort_values("suma PLN")
        ui.table(by_kind.reset_index().rename(columns={"kind": "rodzaj"}))
        ui.table(exp.cash, height=320)
