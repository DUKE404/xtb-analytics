"""Analityka tradera: skuteczność, wzorce zachowań, wielkość pozycji, rozbicie kosztów.

Wynik netto transakcji = wynik brutto + prowizja + swap + rollover (koszty są w raporcie XTB ujemne).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

WEEKDAYS_PL = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]


def _fx_at(fx: pd.DataFrame | None, currency: str, when) -> float:
    if fx is None or currency == "PLN" or currency not in fx.columns:
        return 1.0
    s = fx[currency].dropna()
    if not len(s):
        return 1.0
    i = s.index.searchsorted(pd.Timestamp(when).normalize(), side="right") - 1
    return float(s.iloc[max(i, 0)])


def _f(x) -> float:
    """Liczba albo 0.0 dla braków (None / NaN)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return v if np.isfinite(v) else 0.0


def trades_table(closed: pd.DataFrame, meta: pd.DataFrame, fx: pd.DataFrame | None = None) -> pd.DataFrame:
    """Jedna zamknięta pozycja = jedna transakcja. Dodaje wynik netto, czas trzymania i wielkość w PLN."""
    cols = ["symbol", "asset_class", "side", "volume", "open_time", "close_time", "open_price", "close_price",
            "hold_days", "gross_pl", "commission", "swap", "costs", "net_pl", "size_pln", "close_value_pln",
            "return_pct", "win"]
    if not len(closed):
        return pd.DataFrame(columns=cols)
    rows = []
    for t in closed.itertuples(index=False):
        m = meta.loc[t.symbol] if t.symbol in meta.index else None
        cls = str(m["asset_class"]) if m is not None else "STOCK"
        cur = str(m["currency"]) if m is not None else "PLN"
        mult = float(m["multiplier"]) if m is not None and cls == "CFD" else 1.0
        gross = _f(t.gross_pl)
        if cls == "CFD" or _f(t.purchase_value) <= 0:
            size = t.volume * mult * t.open_price * _fx_at(fx, cur, t.open_time)
            close_value = t.volume * mult * t.close_price * _fx_at(fx, cur, t.close_time)
        else:
            size = _f(t.purchase_value)
            close_value = _f(t.sale_value) or size + gross
        commission = _f(t.commission)
        swap = _f(t.swap) + _f(t.rollover)
        costs = commission + swap
        net = gross + costs
        rows.append({"symbol": t.symbol, "asset_class": cls, "side": t.type, "volume": t.volume,
                     "open_time": t.open_time, "close_time": t.close_time, "open_price": t.open_price,
                     "close_price": t.close_price,
                     "hold_days": (t.close_time - t.open_time).total_seconds() / 86400.0,
                     "gross_pl": gross, "commission": commission, "swap": swap, "costs": costs, "net_pl": net, "size_pln": size, "close_value_pln": close_value,
                     "return_pct": net / size if size else np.nan, "win": net > 0})
    return pd.DataFrame(rows, columns=cols).sort_values("close_time").reset_index(drop=True)


def _streak(flags: np.ndarray, value: bool) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f == value else 0
        best = max(best, cur)
    return best


def summary(trades: pd.DataFrame) -> dict:
    n = len(trades)
    out = {"n": n, "hit_rate": np.nan, "avg_win": np.nan, "avg_loss": np.nan, "payoff": np.nan, "profit_factor": np.nan,
           "expectancy": np.nan, "total": 0.0, "best": np.nan, "worst": np.nan, "hold_win": np.nan, "hold_loss": np.nan,
           "hold_median": np.nan, "max_losing_streak": 0, "max_winning_streak": 0, "gross_profit": 0.0, "gross_loss": 0.0}
    if not n:
        return out
    pl = trades["net_pl"]
    wins, losses = pl[pl > 0], pl[pl <= 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    out.update(
        hit_rate=len(wins) / n, total=float(pl.sum()), expectancy=float(pl.mean()),
        avg_win=float(wins.mean()) if len(wins) else np.nan, avg_loss=float(losses.mean()) if len(losses) else np.nan,
        gross_profit=gp, gross_loss=gl, profit_factor=gp / gl if gl > 0 else np.nan,
        best=float(pl.max()), worst=float(pl.min()),
        hold_win=float(trades.loc[pl > 0, "hold_days"].mean()) if len(wins) else np.nan,
        hold_loss=float(trades.loc[pl <= 0, "hold_days"].mean()) if len(losses) else np.nan,
        hold_median=float(trades["hold_days"].median()),
        max_losing_streak=_streak((pl > 0).values, False), max_winning_streak=_streak((pl > 0).values, True))
    if len(wins) and len(losses) and losses.mean() != 0:
        out["payoff"] = float(wins.mean() / abs(losses.mean()))
    return out


def _group(trades: pd.DataFrame, key) -> pd.DataFrame:
    g = trades.groupby(key)
    return pd.DataFrame({"n": g.size(), "net": g["net_pl"].sum(), "hit_rate": g["win"].mean(), "avg": g["net_pl"].mean()})


def by_weekday(trades: pd.DataFrame, when: str = "open_time") -> pd.DataFrame:
    """Wyniki wg dnia tygodnia otwarcia (lub zamknięcia) pozycji."""
    if not len(trades):
        return pd.DataFrame(columns=["n", "net", "hit_rate", "avg"])
    out = _group(trades, trades[when].dt.dayofweek)
    out.index = [WEEKDAYS_PL[i] for i in out.index]
    return out


def by_hour(trades: pd.DataFrame, when: str = "open_time") -> pd.DataFrame:
    if not len(trades):
        return pd.DataFrame(columns=["n", "net", "hit_rate", "avg"])
    return _group(trades, trades[when].dt.hour).sort_index()


def by_symbol(trades: pd.DataFrame) -> pd.DataFrame:
    if not len(trades):
        return pd.DataFrame(columns=["n", "net", "hit_rate", "avg"])
    return _group(trades, "symbol").sort_values("net", ascending=False)


# --------------------------------------------------------------------------- #
# Wzorce zachowań
# --------------------------------------------------------------------------- #

def size_after_loss(trades: pd.DataFrame, min_trades: int = 8) -> dict:
    """Czy po stratnej transakcji kolejna pozycja jest większa niż po zyskownej?

    Wielkość jest normalizowana medianą wielkości na danym instrumencie, żeby nie mieszać US500 z WIG20.
    'Poprzednia' transakcja = ostatnia zamknięta przed otwarciem bieżącej.
    """
    out = {"detected": False, "ratio": np.nan, "after_loss": np.nan, "after_win": np.nan, "p_value": np.nan,
           "n_after_loss": 0, "n_after_win": 0}
    if len(trades) < min_trades:
        return out
    t = trades.sort_values("close_time").reset_index(drop=True)
    rel = t["size_pln"] / t.groupby("symbol")["size_pln"].transform("median")
    closes = t["close_time"].values
    after_loss, after_win = [], []
    for i in range(len(t)):
        j = int(np.searchsorted(closes, t.at[i, "open_time"].to_datetime64(), side="right")) - 1
        if j < 0 or j == i or not np.isfinite(rel.iloc[i]):
            continue
        (after_win if t.at[j, "net_pl"] > 0 else after_loss).append(float(rel.iloc[i]))
    out.update(n_after_loss=len(after_loss), n_after_win=len(after_win))
    if len(after_loss) < 4 or len(after_win) < 4:
        return out
    a, b = float(np.mean(after_loss)), float(np.mean(after_win))
    p = float(stats.mannwhitneyu(after_loss, after_win, alternative="greater").pvalue)
    out.update(after_loss=a, after_win=b, ratio=a / b if b else np.nan, p_value=p,
               detected=bool(b and a / b >= 1.2 and p < 0.05))
    return out


def disposition_effect(trades: pd.DataFrame, min_trades: int = 8) -> dict:
    """Czy stratne pozycje są trzymane wyraźnie dłużej niż zyskowne (efekt dyspozycji)?"""
    out = {"detected": False, "hold_win": np.nan, "hold_loss": np.nan, "ratio": np.nan, "p_value": np.nan}
    if len(trades) < min_trades:
        return out
    w = trades.loc[trades["net_pl"] > 0, "hold_days"]
    l = trades.loc[trades["net_pl"] <= 0, "hold_days"]
    if len(w) < 4 or len(l) < 4:
        return out
    hw, hl = float(w.median()), float(l.median())
    p = float(stats.mannwhitneyu(l, w, alternative="greater").pvalue)
    out.update(hold_win=hw, hold_loss=hl, ratio=hl / hw if hw > 0 else np.nan, p_value=p,
               detected=bool(hw > 0 and hl / hw >= 1.5 and p < 0.05))
    return out


def early_exit(trades: pd.DataFrame, prices: pd.DataFrame, lookahead: int = 20, min_trades: int = 6) -> dict:
    """Czy zyski są realizowane zbyt wcześnie? Sprawdza, co cena zrobiła przez `lookahead` sesji po zamknięciu
    zyskownej pozycji, liczone w kierunku tej pozycji (plus = dalszy ruch na korzyść)."""
    out = {"detected": False, "n": 0, "share_continued": np.nan, "avg_move": np.nan, "median_move": np.nan,
           "p_value": np.nan, "left_on_table": np.nan}
    wins = trades[trades["net_pl"] > 0]
    moves, left = [], 0.0
    for t in wins.itertuples(index=False):
        if t.symbol not in prices.columns:
            continue
        px = prices[t.symbol].dropna()
        i = px.index.searchsorted(pd.Timestamp(t.close_time).normalize(), side="right") - 1
        if i < 0 or i + lookahead >= len(px):
            continue
        base, later = float(px.iloc[i]), float(px.iloc[i + lookahead])
        if base <= 0:
            continue
        move = (later / base - 1.0) * (-1.0 if t.side == "SELL" else 1.0)
        moves.append(move)
        left += move * t.close_value_pln
    out["n"] = len(moves)
    if len(moves) < min_trades:
        return out
    arr = np.array(moves)
    p = float(stats.ttest_1samp(arr, 0.0, alternative="greater").pvalue) if arr.std() > 0 else np.nan
    out.update(share_continued=float((arr > 0).mean()), avg_move=float(arr.mean()), median_move=float(np.median(arr)),
               p_value=p, left_on_table=float(left),
               detected=bool(arr.mean() > 0 and (arr > 0).mean() >= 0.55 and p == p and p < 0.10))
    return out


# --------------------------------------------------------------------------- #
# Wielkość pozycji (kalkulatory)
# --------------------------------------------------------------------------- #

def kelly_fraction(hit_rate: float, payoff: float) -> float:
    """Wzór Kelly'ego dla zakładu o skuteczności p i stosunku zysk/strata b: f = p - (1 - p) / b."""
    if not (0 < hit_rate < 1) or not payoff or payoff <= 0:
        return np.nan
    return float(hit_rate - (1.0 - hit_rate) / payoff)


def half_kelly(hit_rate: float, payoff: float) -> float:
    f = kelly_fraction(hit_rate, payoff)
    return float(max(f, 0.0) / 2.0) if f == f else np.nan


def volatility_position_size(equity: float, risk_fraction: float, daily_vol: float, stop_multiple: float = 2.0,
                             horizon_days: float = 1.0) -> float:
    """Wartość pozycji, przy której ruch o `stop_multiple` odchyleń standardowych w horyzoncie `horizon_days`
    kosztuje `risk_fraction` kapitału: wielkość = kapitał x ryzyko / (k x sigma_dzienna x sqrt(h))."""
    stop = stop_multiple * daily_vol * np.sqrt(max(horizon_days, 1e-9))
    return float(equity * risk_fraction / stop) if stop > 0 else np.nan


# --------------------------------------------------------------------------- #
# Koszty
# --------------------------------------------------------------------------- #

def cost_breakdown(trades: pd.DataFrame, open_positions: pd.DataFrame, cash: pd.DataFrame, meta: pd.DataFrame,
                   fx: pd.DataFrame | None = None, fx_fee: float = 0.005) -> dict:
    """Prowizje, swapy, spready (szacunek) i przewalutowania (szacunek) w PLN.

    - prowizje i swapy: z operacji gotówkowych, a gdy ich tam nie ma - z kolumn pozycji,
    - spread: połowa spreadu z tabeli mapowań (spread_bps) od wartości każdej nogi transakcji,
    - przewalutowanie: `fx_fee` (domyślnie 0,5%) od wartości kupna i sprzedaży akcji/ETF w walucie obcej.
    """
    def cash_sum(kinds):
        return float(cash.loc[cash["kind"].isin(kinds), "amount"].sum()) if len(cash) else 0.0

    commissions = cash_sum(["COMMISSION"])
    swaps = cash_sum(["SWAP", "ROLLOVER"])
    fees = cash_sum(["FEE"])
    explicit_fx = cash_sum(["FX_CONVERSION"])
    # raport bez osobnych operacji kosztowych: bierzemy kolumny Commission / Swap z pozycji
    if commissions == 0.0:
        commissions = float(trades["commission"].sum()) if len(trades) else 0.0
        commissions += _f(open_positions["commission"].sum()) if len(open_positions) else 0.0
    if swaps == 0.0:
        swaps = float(trades["swap"].sum()) if len(trades) else 0.0
        swaps += _f(open_positions["swap"].sum()) if len(open_positions) else 0.0

    def bps(sym):
        if sym in meta.index and "spread_bps" in meta.columns and meta.at[sym, "spread_bps"] == meta.at[sym, "spread_bps"]:
            return float(meta.at[sym, "spread_bps"])
        return 10.0

    def foreign_stock(sym):
        return sym in meta.index and meta.at[sym, "asset_class"] != "CFD" and meta.at[sym, "currency"] != "PLN"

    spread, fx_cost, turnover = 0.0, 0.0, 0.0
    by_class = {}
    for t in trades.itertuples(index=False):
        legs = t.size_pln + t.close_value_pln
        turnover += legs
        s = legs * bps(t.symbol) / 1e4 / 2.0
        f = legs * fx_fee if foreign_stock(t.symbol) else 0.0
        spread += s
        fx_cost += f
        c = by_class.setdefault(t.asset_class, {"spread": 0.0, "fx": 0.0, "turnover": 0.0})
        c["spread"] += s; c["fx"] += f; c["turnover"] += legs
    for p in open_positions.itertuples(index=False):
        m = meta.loc[p.symbol] if p.symbol in meta.index else None
        cls = str(m["asset_class"]) if m is not None else "STOCK"
        if cls == "CFD" or _f(p.purchase_value) <= 0:
            mult = float(m["multiplier"]) if m is not None else 1.0
            leg = p.volume * mult * p.open_price * _fx_at(fx, str(m["currency"]) if m is not None else "PLN", p.open_time)
        else:
            leg = _f(p.purchase_value)
        turnover += leg
        s = leg * bps(p.symbol) / 1e4 / 2.0
        f = leg * fx_fee if foreign_stock(p.symbol) else 0.0
        spread += s
        fx_cost += f
        c = by_class.setdefault(cls, {"spread": 0.0, "fx": 0.0, "turnover": 0.0})
        c["spread"] += s; c["fx"] += f; c["turnover"] += leg

    items = {
        "Prowizje": -commissions, "Swapy": -swaps, "Spready (szacunek)": spread,
        "Przewalutowania (szacunek)": fx_cost - explicit_fx, "Pozostałe opłaty": -fees,
    }
    total = float(sum(items.values()))
    return {"items": items, "total": total, "turnover": turnover, "by_class": by_class,
            "taxes_withheld": -cash_sum(["WHT", "INTEREST_TAX"])}


def cost_ratios(total_cost: float, net_result: float, avg_equity: float, years: float) -> dict:
    """Koszty jako % wyniku przed kosztami i jako % średniego kapitału rocznie."""
    gross = net_result + total_cost
    return {"gross_result": gross,
            "share_of_gross": total_cost / gross if gross > 0 else np.nan,
            "per_year_of_equity": total_cost / avg_equity / years if avg_equity > 0 and years > 0 else np.nan}
