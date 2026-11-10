"""Test dymny UI: uruchamia całą aplikację na danych przykładowych (tryb demo, bez sieci).

- Gdy Streamlit jest zainstalowany (np. `make test` u użytkownika): prawdziwy przebieg przez AppTest.
- Gdy go nie ma: przebieg na atrapach z tests/fakes, które sprawdzają wywołania i kształt danych.
"""
import importlib.util
import os
import runpy
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, "app", "main.py")
HAS_STREAMLIT = importlib.util.find_spec("streamlit") is not None and importlib.util.find_spec("plotly") is not None


@pytest.mark.skipif(not HAS_STREAMLIT, reason="Streamlit/plotly nie są zainstalowane")
def test_app_runs_with_real_streamlit(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = AppTest.from_file(MAIN, default_timeout=300)
    at.run()
    assert not at.exception, [str(e.value) for e in at.exception]
    assert not at.error, [str(e.value) for e in at.error]
    assert len(at.metric) >= 6


def _run_with_fakes(monkeypatch, overrides=None, env=None):
    fakes = os.path.join(ROOT, "tests", "fakes")
    monkeypatch.syspath_prepend(fakes)
    for name in [m for m in sys.modules if m == "streamlit" or m.startswith(("streamlit.", "plotly", "app."))
                 or m == "app"]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.chdir(ROOT)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    import streamlit as st

    st._reset()
    st._overrides.update(overrides or {})
    try:
        runpy.run_path(MAIN, run_name="__main__")
    except st.RerunException:
        pass
    return st


@pytest.mark.skipif(HAS_STREAMLIT, reason="jest prawdziwy Streamlit - używamy AppTest")
def test_app_runs_with_fakes(monkeypatch):
    st = _run_with_fakes(monkeypatch)
    errors = [c for c in st._calls if c[0] in ("error", "exception")]
    assert not errors, errors
    assert sum(1 for c in st._calls if c[0] == "metric") >= 6
    assert sum(1 for c in st._calls if c[0] == "plotly_chart") >= 2


@pytest.mark.skipif(HAS_STREAMLIT, reason="jest prawdziwy Streamlit - używamy AppTest")
def test_app_upload_mode_without_file(monkeypatch):
    st = _run_with_fakes(monkeypatch, overrides={"source": "Mój eksport z XTB"})
    assert not [c for c in st._calls if c[0] in ("error", "exception")]
    assert any(c[0] == "info" and "Import" in str(c[1]) for c in st._calls)
