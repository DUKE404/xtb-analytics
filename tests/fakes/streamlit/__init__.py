"""Minimalna atrapa Streamlit do testu dymnego UI tam, gdzie prawdziwy Streamlit nie jest zainstalowany.

Udostępnia WYŁĄCZNIE te funkcje, których aplikacja używa - literówka w nazwie API da AttributeError.
Widżety zwracają wartości domyślne; `_overrides` pozwala testom je podmienić (np. "kliknąć" przycisk).
"""
from __future__ import annotations

import sys
import types

_overrides: dict = {}
_calls: list = []
_this = sys.modules[__name__]


class StopException(BaseException):
    pass


class RerunException(BaseException):
    pass


class _State(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v


session_state = _State()


class _Box:
    """Kontener (kolumna, zakładka, expander, sidebar): działa jak `with` i jak obiekt z metodami st.*"""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __getattr__(self, name):
        return getattr(_this, name)


sidebar = _Box()


def _log(kind, *a, **k):
    _calls.append((kind, a[0] if a else None))


def _val(label, key, default):
    if key is not None and key in _overrides:
        v = _overrides[key]
    elif label in _overrides:
        v = _overrides[label]
    else:
        v = default
    if key is not None:
        session_state[key] = v
    return v


# --- układ
def set_page_config(**k): _log("page_config")
def columns(spec, **k): return [_Box() for _ in range(spec if isinstance(spec, int) else len(spec))]
def tabs(names, **k): return [_Box() for _ in names]
def expander(label, expanded=False, **k): return _Box()
def container(**k): return _Box()
def spinner(text="", **k): return _Box()
def form(key, **k): return _Box()
def chat_message(name, **k): return _Box()
def empty(): return _Box()
def divider(**k): _log("divider")


# --- tekst
def _text(kind):
    def f(*a, **k):
        _log(kind, *a)
    return f


title = _text("title"); header = _text("header"); subheader = _text("subheader"); markdown = _text("markdown")
caption = _text("caption"); write = _text("write"); info = _text("info"); warning = _text("warning")
success = _text("success"); code = _text("code"); text = _text("text"); latex = _text("latex")


def error(*a, **k):
    _log("error", *a)


def exception(exc, **k):
    _log("exception", exc)


def metric(label, value, delta=None, delta_color="normal", help=None, **k):
    assert isinstance(label, str) and value is not None
    _log("metric", label)


# --- dane i wykresy
def dataframe(data=None, hide_index=None, height=None, column_config=None, use_container_width=None, **k):
    _log("dataframe")


def table(data=None, **k): _log("table")


def plotly_chart(fig, use_container_width=False, theme="streamlit", key=None, config=None, **k):
    assert hasattr(fig, "update_layout"), "plotly_chart wymaga obiektu Figure"
    _log("plotly_chart", key)


def data_editor(data, key=None, column_config=None, hide_index=None, num_rows="fixed", use_container_width=None,
                disabled=False, **k):
    return _val(None, key, data)


class _ColumnConfig:
    def __getattr__(self, name):
        if not name.endswith("Column"):
            raise AttributeError(name)
        return lambda *a, **k: {"type": name, **k}


column_config = _ColumnConfig()


# --- widżety
def button(label, key=None, type="secondary", help=None, disabled=False, on_click=None, **k):
    return bool(_val(label, key, False))


def form_submit_button(label="Submit", **k): return bool(_val(label, None, False))
def download_button(label, data=None, file_name=None, mime=None, key=None, **k):
    assert data is not None
    return False


def checkbox(label, value=False, key=None, help=None, **k): return _val(label, key, value)
def toggle(label, value=False, key=None, help=None, **k): return _val(label, key, value)


def radio(label, options, index=0, key=None, help=None, horizontal=False, format_func=str, **k):
    options = list(options)
    return _val(label, key, session_state.get(key, options[index]) if key else options[index])


def selectbox(label, options, index=0, key=None, help=None, format_func=str, **k):
    options = list(options)
    for o in options:
        format_func(o)
    return _val(label, key, options[index] if options and index is not None else None)


def multiselect(label, options, default=None, key=None, help=None, format_func=str, max_selections=None, **k):
    return _val(label, key, list(default or []))


def slider(label, min_value=None, max_value=None, value=None, step=None, key=None, help=None, format=None, **k):
    return _val(label, key, value if value is not None else min_value)


def select_slider(label, options, value=None, key=None, help=None, **k):
    options = list(options)
    return _val(label, key, value if value is not None else options[0])


def number_input(label, min_value=None, max_value=None, value=None, step=None, key=None, help=None, format=None, **k):
    v = _val(label, key, value if value is not None else (min_value or 0))
    if min_value is not None:
        assert v >= min_value, f"{label}: {v} < min {min_value}"
    if max_value is not None:
        assert v <= max_value, f"{label}: {v} > max {max_value}"
    return v


def text_input(label, value="", key=None, help=None, placeholder=None, type="default", **k): return _val(label, key, value)
def text_area(label, value="", key=None, help=None, height=None, placeholder=None, **k): return _val(label, key, value)
def date_input(label, value=None, key=None, help=None, **k): return _val(label, key, value)
def chat_input(placeholder="", key=None, **k): return _val(placeholder, key, None)


def file_uploader(label, type=None, accept_multiple_files=False, key=None, help=None, **k):
    return _val(label, key, [] if accept_multiple_files else None)


# --- sterowanie
def stop():
    raise StopException()


def rerun():
    raise RerunException()


def cache_data(func=None, **kwargs):
    def deco(f):
        f.clear = lambda: None
        return f
    return deco(func) if callable(func) else deco


cache_resource = cache_data


def _reset():
    _overrides.clear()
    _calls.clear()
    session_state.clear()
