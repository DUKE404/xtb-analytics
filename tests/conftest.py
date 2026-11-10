import os
import sys

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def sample_data():
    from data.sample_generator import generate
    return generate()


@pytest.fixture(scope="session")
def sample_path(sample_data, tmp_path_factory):
    from data.sample_generator import write_xlsx
    return write_xlsx(sample_data, str(tmp_path_factory.mktemp("xtb") / "sample.xlsx"))


@pytest.fixture(scope="session")
def export(sample_path):
    from data.xtb_parser import parse_export
    return parse_export(sample_path)


@pytest.fixture(scope="session")
def mapping(export):
    from data.mapping import build_mapping
    return build_mapping(export.symbols)


@pytest.fixture(scope="session")
def market(export, mapping):
    from data.loader import load_market
    from data.synthetic import SyntheticMarketData
    return load_market(export, mapping, SyntheticMarketData())


@pytest.fixture(scope="session")
def portfolio(export, mapping, market):
    from data.loader import build_portfolio
    return build_portfolio(export, mapping, market)


def make_frames(positions_closed=(), positions_open=(), cash_ops=()):
    """Pomocnik do małych, ręcznie policzalnych przykładów."""
    from data.xtb_parser import CASH_OUT, CLOSED_OUT, OPEN_OUT, cash_kind

    def pos(rows, cols):
        df = pd.DataFrame(list(rows))
        for c in cols:
            if c not in df.columns:
                df[c] = "" if c in ("position", "comment") else (pd.NaT if c.endswith("_time") else 0.0)
        for c in ("open_time", "close_time"):
            if c in df.columns:
                df[c] = pd.to_datetime(df[c])
        return df[cols]

    closed = pos(positions_closed, CLOSED_OUT)
    open_ = pos(positions_open, OPEN_OUT)
    cash = pd.DataFrame(list(cash_ops))
    if len(cash):
        cash["time"] = pd.to_datetime(cash["time"])
        cash["type_raw"] = cash["type"]
        cash["kind"] = cash["type"].map(cash_kind)
        for c in ("id", "comment", "symbol"):
            if c not in cash.columns:
                cash[c] = ""
        cash = cash[CASH_OUT]
    else:
        cash = pd.DataFrame(columns=CASH_OUT)
    return closed, open_, cash
