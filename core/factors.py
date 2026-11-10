"""Ekspozycja na czynniki Fama-French 5 + momentum: regresja OLS i opis wyniku prostym językiem."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "MOM"]
FACTOR_NAMES = {
    "Mkt-RF": "Rynek (Mkt-RF)", "SMB": "Wielkość (SMB)", "HML": "Wartość (HML)",
    "RMW": "Rentowność (RMW)", "CMA": "Inwestycje (CMA)", "MOM": "Momentum (MOM)",
}
PERIODS_PER_YEAR = {"D": 252, "W": 52}


def ols(y, X, names=None) -> dict:
    """Zwykła regresja najmniejszych kwadratów z wyrazem wolnym. Zwraca współczynniki, błędy, t, p, R²."""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)
    n, k = X.shape
    A = np.column_stack([np.ones(n), X])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ coef
    dof = n - k - 1
    if dof <= 0:
        raise ValueError("Za mało obserwacji do regresji.")
    s2 = float(resid @ resid) / dof
    cov = s2 * np.linalg.pinv(A.T @ A)
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, coef / se, np.nan)
    p = 2.0 * stats.t.sf(np.abs(t), dof)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else np.nan
    adj = 1.0 - (1.0 - r2) * (n - 1) / dof if np.isfinite(r2) else np.nan
    names = ["const"] + list(names if names is not None else [f"x{i + 1}" for i in range(k)])
    return {"names": names, "coef": coef, "se": se, "t": t, "p": p, "r2": r2, "adj_r2": adj,
            "n": n, "dof": dof, "resid_std": float(np.sqrt(s2))}


def to_usd_returns(returns_pln: pd.Series, usdpln: pd.Series) -> pd.Series:
    """Zwroty widziane z PLN -> zwroty w USD (czynniki Frencha są w dolarach)."""
    fx = usdpln.sort_index()
    fx = fx.reindex(fx.index.union(returns_pln.index)).ffill().reindex(returns_pln.index)
    fx_ret = fx.pct_change().fillna(0.0)
    return (1.0 + returns_pln) / (1.0 + fx_ret) - 1.0


def _compound(df: pd.DataFrame | pd.Series, freq: str):
    return (1.0 + df).resample(freq).prod() - 1.0


def factor_regression(returns_pln: pd.Series, usdpln: pd.Series, factors: pd.DataFrame, freq: str = "W") -> dict:
    """Regresja nadwyżkowych zwrotów portfela (w USD) na czynniki FF5 + momentum.

    freq='W' (domyślnie) agreguje do tygodni - łagodzi to niesynchroniczność sesji w Polsce i USA.
    """
    have = [f for f in FACTORS if f in factors.columns]
    if "RF" not in factors.columns or "Mkt-RF" not in have:
        raise ValueError("Brak kolumn Mkt-RF / RF w danych czynnikowych.")
    r_usd = to_usd_returns(returns_pln, usdpln)
    f = factors[have + ["RF"]].dropna()
    common = r_usd.index.intersection(f.index)
    if len(common) < 40:
        raise ValueError("Za krótki wspólny okres portfela i danych czynnikowych (potrzeba min. 40 sesji).")
    r_usd, f = r_usd.loc[common], f.loc[common]
    if freq == "W":
        r_usd = _compound(r_usd, "W-FRI")
        f = _compound(f, "W-FRI")
        keep = r_usd.index.intersection(f.index)
        r_usd, f = r_usd.loc[keep], f.loc[keep]
    y = r_usd - f["RF"]
    res = ols(y.values, f[have].values, have)
    ppy = PERIODS_PER_YEAR[freq]
    table = pd.DataFrame({"beta": res["coef"][1:], "se": res["se"][1:], "t": res["t"][1:], "p": res["p"][1:]},
                         index=have)
    table["significant"] = table["t"].abs() >= 2.0
    table["label"] = [FACTOR_NAMES.get(i, i) for i in table.index]
    return {
        "table": table, "alpha_annual": float(res["coef"][0] * ppy), "alpha_t": float(res["t"][0]),
        "alpha_p": float(res["p"][0]), "r2": res["r2"], "adj_r2": res["adj_r2"], "n": res["n"], "freq": freq,
        "start": common.min(), "end": common.max(),
    }


_TEXT = {
    "SMB": ("przechył w stronę mniejszych spółek", "przewagę dużych spółek (blue chipów)"),
    "HML": ("przechył w stronę spółek tanich względem wartości księgowej (value)",
            "przechył w stronę drogich spółek wzrostowych (growth)"),
    "RMW": ("przewagę spółek o wysokiej, stabilnej rentowności (quality)",
            "przewagę spółek o słabej rentowności lub jeszcze nierentownych"),
    "CMA": ("przewagę spółek inwestujących ostrożnie", "przewagę spółek agresywnie inwestujących i rosnących"),
    "MOM": ("skłonność do trzymania spółek, które ostatnio rosły (momentum)",
            "skłonność do kupowania spółek, które ostatnio spadały (podejście kontrariańskie)"),
}


def interpret(result: dict) -> list[str]:
    """Wynik regresji zrozumiałym językiem (lista zdań po polsku)."""
    t = result["table"]
    out = []
    b = float(t.at["Mkt-RF", "beta"])
    if b > 1.1:
        tone = "portfel jest bardziej agresywny niż szeroki rynek akcji"
    elif b < 0.9:
        tone = "portfel jest spokojniejszy niż szeroki rynek akcji (część kapitału w gotówce lub defensywnych spółkach)"
    else:
        tone = "portfel zachowuje się podobnie do szerokiego rynku akcji"
    out.append(f"**Rynek:** beta {b:.2f}".replace(".", ",") +
               f" - gdy rynek akcji rusza się o 1%, portfel rusza się średnio o {abs(b):.2f}%".replace(".", ",") + f"; {tone}.")
    weak = []
    for f in ("SMB", "HML", "RMW", "CMA", "MOM"):
        if f not in t.index:
            continue
        beta, sig = float(t.at[f, "beta"]), bool(t.at[f, "significant"])
        if not sig:
            weak.append(FACTOR_NAMES[f].split(" (")[0].lower())
            continue
        pos, neg = _TEXT[f]
        strength = "wyraźny" if abs(beta) >= 0.3 else "umiarkowany"
        desc = pos if beta > 0 else neg
        out.append(f"**{FACTOR_NAMES[f]}:** {beta:+.2f}".replace(".", ",") + f" - {strength} sygnał: dane pokazują {desc}.")
    if weak:
        out.append("**Bez wyraźnej ekspozycji** (statystycznie nieodróżnialne od zera): " + ", ".join(weak) + ".")
    a, at = result["alpha_annual"], result["alpha_t"]
    if abs(at) >= 2:
        out.append(f"**Alfa:** {a * 100:+.1f}% rocznie".replace(".", ",") + " po uwzględnieniu czynników - statystycznie istotna, "
                   "czyli wynik nie tłumaczy się samą ekspozycją na znane czynniki.")
    else:
        out.append(f"**Alfa:** {a * 100:+.1f}% rocznie".replace(".", ",") + ", ale nieistotna statystycznie - nie da się odróżnić "
                   "umiejętności od szczęścia na tak krótkiej historii.")
    r2 = result["r2"]
    out.append(f"**Dopasowanie:** czynniki tłumaczą {r2 * 100:.0f}% wahań portfela (R²); reszta to ryzyko specyficzne "
               f"Twoich spółek. Regresja na {result['n']} obserwacjach "
               f"({'tygodniowych' if result['freq'] == 'W' else 'dziennych'}).")
    return out
