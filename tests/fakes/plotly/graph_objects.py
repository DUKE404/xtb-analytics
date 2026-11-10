"""Atrapa plotly.graph_objects: sprawdza kształt danych i nazwy używanych typów śladów."""
from __future__ import annotations

import numpy as np

_ALLOWED = {"Scatter", "Bar", "Heatmap", "Histogram", "Box", "Waterfall", "Table", "Violin"}


class _Trace:
    kind = "Trace"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        x, y = kwargs.get("x"), kwargs.get("y")
        if x is not None and y is not None and self.kind in ("Scatter", "Bar"):
            assert len(x) == len(y), f"{self.kind}: x i y mają różne długości ({len(x)} vs {len(y)})"
        z = kwargs.get("z")
        if z is not None and self.kind == "Heatmap":
            z = np.asarray(z, dtype=float)
            assert z.ndim == 2, "Heatmap: z musi być macierzą"
            if kwargs.get("x") is not None:
                assert z.shape[1] == len(kwargs["x"]), "Heatmap: liczba kolumn z != len(x)"
            if kwargs.get("y") is not None:
                assert z.shape[0] == len(kwargs["y"]), "Heatmap: liczba wierszy z != len(y)"


def __getattr__(name):
    if name in _ALLOWED:
        return type(name, (_Trace,), {"kind": name})
    raise AttributeError(name)


class Figure:
    def __init__(self, data=None, layout=None, **k):
        self.data = [] if data is None else (list(data) if isinstance(data, (list, tuple)) else [data])
        self.layout = {}

    def add_trace(self, trace, row=None, col=None, **k):
        assert isinstance(trace, _Trace)
        self.data.append(trace)
        return self

    def _noop(self, *a, **k):
        return self

    update_layout = update_xaxes = update_yaxes = update_traces = _noop
    add_hline = add_vline = add_hrect = add_vrect = add_annotation = add_shape = _noop
