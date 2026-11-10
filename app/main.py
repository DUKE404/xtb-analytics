"""XTB Analytics - lokalna aplikacja do analityki portfela. Uruchom: `make run` lub `./start.sh`."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)  # ścieżki względne (.cache, data/sample) liczone od katalogu projektu

import streamlit as st  # noqa: E402

from app import state  # noqa: E402
from app.tabs import REGISTRY  # noqa: E402
from data.providers import BENCHMARK_LABELS  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:  # python-dotenv jest opcjonalny
    pass

st.set_page_config(page_title="XTB Analytics", page_icon="📊", layout="wide")

BENCH_CHOICES = ["MSCIW", "SP500", "WIG", "WIG20"]

with st.sidebar:
    st.markdown("### 📊 XTB Analytics")
    st.radio("Źródło danych", [state.SOURCE_DEMO, state.SOURCE_UPLOAD], key="source",
             help="Tryb demo działa bez internetu na syntetycznych notowaniach. Własny plik wgrasz w zakładce Import.")
    bench = st.selectbox("Benchmark do bety i alfy", BENCH_CHOICES, index=0,
                         format_func=lambda k: BENCHMARK_LABELS.get(k, k),
                         help="Indeks, do którego porównujemy portfel przy liczeniu bety, alfy i tracking error.")
    rf_pct = st.number_input("Stopa wolna od ryzyka (% rocznie)", min_value=0.0, max_value=15.0, value=4.0, step=0.25,
                             help="Oprocentowanie bezpiecznej lokaty / obligacji skarbowych. Używane w Sharpe, Sortino i alfie.")
    st.caption("Aplikacja wyłącznie analityczna. Nie łączy się z kontem XTB i nie daje rekomendacji inwestycyjnych.")

ctx = state.get_context(rf=rf_pct / 100.0, bench=bench)

has_ai = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
entries = [e for e in REGISTRY if e["name"] != "AI" or has_ai]

if ctx is not None:
    pf = ctx.pf
    st.caption(f"Stan na {pf.as_of.date().isoformat()} · historia od {pf.dates[0].date().isoformat()} · "
               f"notowania: {ctx.market.source}" + (" · **TRYB DEMO: dane syntetyczne, nie są to prawdziwe ceny**" if ctx.is_demo else ""))

tabs = st.tabs([e["name"] for e in entries])
for tab, entry in zip(tabs, entries):
    with tab:
        if entry["name"] != "Import" and ctx is None:
            st.info("Najpierw wgraj eksport z xStation 5 w zakładce **Import** albo przełącz źródło na dane przykładowe.")
            continue
        try:
            entry["render"](ctx)
        except Exception as exc:  # jedna zakładka nie może wyłożyć całej aplikacji
            if exc.__class__.__name__ in ("StopException", "RerunException", "RerunData"):
                raise
            st.error(f"Zakładka „{entry['name']}” napotkała błąd: {exc}")
            st.exception(exc)
