"""Detekcja reżimów rynkowych: ukryty model Markowa z rozkładami normalnymi (własna implementacja w numpy)
albo proste progi zmienności kroczącej. Plus wyniki portfela w każdym reżimie."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252
LABELS = {2: ["spokojny", "nerwowy"], 3: ["spokojny", "przejściowy", "nerwowy"]}


def _log_gauss(x: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    return -0.5 * np.log(2 * np.pi) - np.log(sd)[None, :] - 0.5 * ((x[:, None] - mu[None, :]) / sd[None, :]) ** 2


def gaussian_hmm(x, n_states: int = 2, n_iter: int = 200, tol: float = 1e-6) -> dict:
    """Dopasowuje HMM algorytmem Bauma-Welcha (z normalizacją) i dekoduje ścieżkę stanów algorytmem Viterbiego.

    Stany są posortowane rosnąco po zmienności: 0 = najspokojniejszy.
    """
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n, k = len(x), n_states
    if n < 30 * k:
        raise ValueError("Za mało obserwacji do dopasowania modelu reżimów.")
    # start: podział po kroczącej zmienności (stabilniejszy niż losowy)
    roll = pd.Series(np.abs(x)).rolling(10, min_periods=1).mean().values
    cuts = np.quantile(roll, np.linspace(0, 1, k + 1)[1:-1])
    init = np.digitize(roll, cuts)
    mu = np.array([x[init == j].mean() if (init == j).any() else x.mean() for j in range(k)])
    sd = np.array([x[init == j].std() if (init == j).sum() > 1 else x.std() for j in range(k)])
    floor = max(x.std() * 0.05, 1e-8)
    sd = np.maximum(sd, floor)
    A = np.full((k, k), 0.03 / max(k - 1, 1))
    np.fill_diagonal(A, 0.97)
    pi = np.full(k, 1.0 / k)
    prev = -np.inf
    for _ in range(n_iter):
        logb = _log_gauss(x, mu, sd)
        b = np.exp(logb - logb.max(axis=1, keepdims=True))
        # krok E: przód-tył ze skalowaniem
        alpha = np.empty((n, k))
        scale = np.empty(n)
        a = pi * b[0]
        scale[0] = a.sum()
        alpha[0] = a / scale[0]
        for t in range(1, n):
            a = (alpha[t - 1] @ A) * b[t]
            scale[t] = a.sum()
            alpha[t] = a / scale[t]
        beta = np.empty((n, k))
        beta[-1] = 1.0
        for t in range(n - 2, -1, -1):
            beta[t] = (A @ (b[t + 1] * beta[t + 1])) / scale[t + 1]
        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True)
        xi = (alpha[:-1, :, None] * A[None, :, :] * (b[1:] * beta[1:])[:, None, :]) / scale[1:, None, None]
        loglik = float(np.log(scale).sum() + logb.max(axis=1).sum())
        # krok M
        pi = gamma[0] / gamma[0].sum()
        A = xi.sum(axis=0)
        A /= A.sum(axis=1, keepdims=True)
        w = gamma.sum(axis=0)
        mu = (gamma * x[:, None]).sum(axis=0) / w
        sd = np.maximum(np.sqrt((gamma * (x[:, None] - mu[None, :]) ** 2).sum(axis=0) / w), floor)
        if abs(loglik - prev) < tol * max(1.0, abs(loglik)):
            break
        prev = loglik
    # Viterbi
    logb = _log_gauss(x, mu, sd)
    logA = np.log(np.maximum(A, 1e-300))
    delta = np.log(np.maximum(pi, 1e-300)) + logb[0]
    back = np.zeros((n, k), dtype=int)
    for t in range(1, n):
        cand = delta[:, None] + logA
        back[t] = cand.argmax(axis=0)
        delta = cand.max(axis=0) + logb[t]
    states = np.empty(n, dtype=int)
    states[-1] = int(delta.argmax())
    for t in range(n - 1, 0, -1):
        states[t - 1] = back[t, states[t]]
    order = np.argsort(sd)
    rank = np.empty(k, dtype=int)
    rank[order] = np.arange(k)
    return {"states": rank[states], "means": mu[order], "stds": sd[order], "transition": A[np.ix_(order, order)],
            "posterior": gamma[:, order], "loglik": loglik}


def hmm_regimes(returns: pd.Series, n_states: int = 2) -> pd.Series:
    r = returns.dropna()
    res = gaussian_hmm(r.values, n_states)
    return pd.Series(res["states"], index=r.index, name="regime")


def volatility_regimes(returns: pd.Series, n_states: int = 2, window: int = 21) -> pd.Series:
    """Reżimy wg progów zmienności kroczącej (mediana przy 2 stanach, tercyle przy 3)."""
    r = returns.dropna()
    vol = r.rolling(window, min_periods=max(5, window // 2)).std().bfill()
    cuts = np.quantile(vol.dropna(), [0.67] if n_states == 2 else [0.5, 0.85])
    return pd.Series(np.digitize(vol.values, cuts), index=r.index, name="regime")


def regime_stats(returns: pd.Series, states: pd.Series, n_states: int = 2) -> pd.DataFrame:
    """Wyniki serii zwrotów w każdym reżimie: udział dni, zwrot i zmienność w skali roku, Sharpe (rf = 0)."""
    df = pd.concat([returns.rename("r"), states.rename("s")], axis=1).dropna()
    labels = LABELS.get(n_states, [str(i) for i in range(n_states)])
    rows = []
    for s in range(n_states):
        x = df.loc[df["s"] == s, "r"]
        if not len(x):
            rows.append({"regime": labels[s], "days": 0, "share": 0.0, "ann_return": np.nan, "volatility": np.nan,
                         "sharpe": np.nan, "worst_day": np.nan, "total": np.nan})
            continue
        vol = float(x.std(ddof=1) * np.sqrt(TRADING_DAYS)) if len(x) > 1 else np.nan
        ann = float(x.mean() * TRADING_DAYS)
        rows.append({"regime": labels[s], "days": int(len(x)), "share": len(x) / len(df), "ann_return": ann,
                     "volatility": vol, "sharpe": ann / vol if vol and vol > 0 else np.nan,
                     "worst_day": float(x.min()), "total": float((1 + x).prod() - 1)})
    return pd.DataFrame(rows).set_index("regime")


def spans(states: pd.Series, state: int) -> list[tuple[int, int]]:
    """Ciągłe odcinki (indeks początku, indeks końca) w których obowiązuje dany stan - do cieniowania wykresu."""
    v = (states.values == state).astype(int)
    out, start = [], None
    for i, f in enumerate(v):
        if f and start is None:
            start = i
        if not f and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(v) - 1))
    return out
