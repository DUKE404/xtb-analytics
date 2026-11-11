"""Buduje paczkę zagregowanych metryk wysyłaną do modelu (memo ryzyka, czat).

Świadomie NIE zawiera: imienia i nazwiska, numeru rachunku, listy transakcji ani operacji gotówkowych.
Zawiera wartości zbiorcze portfela i nazwy instrumentów z największym wpływem - bez tego memo byłoby puste.
Użytkownik może podejrzeć dokładnie tę paczkę w aplikacji, zanim cokolwiek wyśle.
"""
from __future__ import annotations

import math

import pandas as pd

from core import attribution as attr
from core import cfd, performance as perf, risk, stress, trader


def _r(x, digits: int = 4):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, digits) if math.isfinite(f) else None


def _names(ctx, index) -> list[str]:
    n = ctx.pf.meta["name"]
    return [str(n.get(i, i)) for i in index]


def build(ctx, rf: float, bench: str) -> dict:
    pf = ctx.pf
    equity = float(pf.equity.iloc[-1])
    b = ctx.bench_series(bench)
    s = perf.summary(pf.returns, b, rf, pf.flows, equity)
    out = {
        "stan_na": pf.as_of.strftime("%Y-%m-%d"), "historia_od": pf.dates[0].strftime("%Y-%m-%d"), "waluta": ctx.base,
        "tryb_demo": bool(ctx.is_demo),
        "portfel": {"wartosc_pln": _r(equity, 0), "wplaty_netto_pln": _r(pf.invested_capital(), 0),
                    "wynik_calkowity_pln": _r(pf.total_pnl(), 0), "udzial_gotowki": _r(pf.cash.iloc[-1] / equity if equity else None),
                    "liczba_pozycji": int((pf.qty.iloc[-1].abs() > 1e-12).sum())},
        "wyniki": {"benchmark": ctx.bench_label(bench), "stopa_zwrotu_twr": _r(s["total_return"]), "cagr": _r(s["cagr"]),
                   "xirr": _r(s["xirr"]), "zmiennosc_roczna": _r(s["volatility"]), "sharpe": _r(s["sharpe"], 2),
                   "sortino": _r(s["sortino"], 2), "calmar": _r(s["calmar"], 2), "max_drawdown": _r(s["max_drawdown"]),
                   "biezace_obsuniecie": _r(s["current_drawdown"]), "beta": _r(s["beta"], 2), "alfa_roczna": _r(s["alpha"]),
                   "tracking_error": _r(s["tracking_error"]), "information_ratio": _r(s["information_ratio"], 2)},
    }

    # ostatnie 5 sesji
    week = pf.returns.tail(5)
    a = attr.decompose(pf, ctx.export.cash, start=week.index[0])
    a = a[a["pnl"].abs() > 0.5]
    out["tydzien"] = {
        "zwrot_portfela": _r(perf.total_return(week)), "zwrot_benchmarku": _r(perf.total_return(b.reindex(week.index))),
        "wynik_pln": _r(float(pf.pnl.loc[week.index].sum().sum()), 0),
        "najlepsze": [{"nazwa": n, "wynik_pln": _r(v, 0)} for n, v in zip(a["name"].head(3), a["pnl"].head(3)) if v > 0],
        "najgorsze": [{"nazwa": n, "wynik_pln": _r(v, 0)} for n, v in zip(a["name"].tail(3)[::-1], a["pnl"].tail(3)[::-1]) if v < 0],
    }

    r, exp, _ = ctx.risk_inputs()
    if len(exp) and len(r) >= 30:
        pnl = risk.pnl_series(r, exp)
        cov = risk.covariance(r)
        rc = risk.risk_contributions(exp, cov).head(5)
        conc = risk.concentration(exp)
        v95, v99 = risk.var_historical(pnl, 0.95), risk.var_historical(pnl, 0.99)
        out["ryzyko"] = {
            "var95_1d_pln": _r(v95, 0), "var95_1d_udzial_kapitalu": _r(v95 / equity), "cvar95_1d_pln": _r(risk.cvar_historical(pnl, 0.95), 0),
            "var99_1d_pln": _r(v99, 0), "var95_10d_pln": _r(risk.var_historical(pnl, 0.95, 10), 0),
            "efektywna_liczba_zakladow": _r(risk.effective_number_of_bets(exp, cov), 1),
            "srednia_korelacja_90d": _r(risk.average_correlation(risk.correlation_matrix(r, 90)), 2),
            "top5_udzial": _r(conc["top5"]), "hhi": _r(conc["hhi"], 3),
            "najwieksze_udzialy_w_ryzyku": [{"nazwa": n, "udzial_w_ryzyku": _r(x), "udzial_w_ekspozycji": _r(abs(w))}
                                            for n, x, w in zip(_names(ctx, rc.index), rc["risk_share"], rc["weight"])],
        }
        cash = float(pf.cash.iloc[-1])
        full = pf.current_exposure()

        def shares(col):
            e = risk.exposure_by(full, pf.meta, col, equity, cash, "Gotówka")["net_pct"].sort_values(ascending=False).head(6)
            return {str(k): _r(v) for k, v in e.items()}

        cur = risk.currency_exposure(pf.value.iloc[-1], pf.meta, ctx.cash_by_currency(), equity, ctx.base)["share"]
        out["ekspozycje"] = {"sektory": shares("sector"), "kraje": shares("country"),
                             "waluty": {str(k): _r(v) for k, v in cur.items()}}
        sc = stress.run_scenarios(full, pf.meta, equity, {k: v["shocks"] for k, v in stress.HISTORICAL.items()})
        out["stress_testy_udzial_kapitalu"] = {str(k): _r(v) for k, v in sc["pct"].items()}

    lev = cfd.leverage_summary(pf, ctx.export.open)
    sw = cfd.swap_costs(pf, ctx.export.cash, ctx.export.closed, ctx.export.open)
    out["cfd"] = {"efektywna_dzwignia": _r(lev["gross_leverage"], 2), "nominal_pln": _r(lev["cfd_nominal_gross"], 0),
                  "poziom_depozytu": _r(lev["margin_level"], 2), "odleglosc_do_stop_outu": _r(lev["distance_to_stop_out"]),
                  "roczny_koszt_swapow": _r(sw["annual_rate"])}

    t = ctx.memo("trades", lambda: trader.trades_table(ctx.export.closed, pf.meta, ctx.market.fx))
    if len(t) >= 8:
        ts = trader.summary(t)
        cfd_t = t[t["asset_class"] == "CFD"]
        costs = trader.cost_breakdown(t, ctx.export.open, ctx.export.cash, pf.meta, ctx.market.fx,
                                      account_currencies=ctx.account_currencies)
        ratios = trader.cost_ratios(costs["total"], pf.total_pnl(), float(pf.equity.mean()), max(len(pf.dates) / 252.0, 1e-9))
        out["trader"] = {
            "liczba_transakcji": int(ts["n"]), "hit_rate": _r(ts["hit_rate"]), "payoff_ratio": _r(ts["payoff"], 2),
            "profit_factor": _r(ts["profit_factor"], 2), "expectancy_pln": _r(ts["expectancy"], 0),
            "wzorce_cfd": {"wieksze_pozycje_po_stracie": bool(trader.size_after_loss(cfd_t)["detected"]),
                           "straty_trzymane_dluzej_niz_zyski": bool(trader.disposition_effect(cfd_t[cfd_t["hold_days"] >= 1])["detected"])},
        }
        out["koszty"] = {"razem_pln": _r(costs["total"], 0), "udzial_w_zysku_przed_kosztami": _r(ratios["share_of_gross"]),
                         "rocznie_do_kapitalu": _r(ratios["per_year_of_equity"])}
    return out if ctx.base == "PLN" else _rename_currency(out, ctx.base.lower())


def _rename_currency(obj, code: str):
    """Klucze z końcówką _pln dostają końcówkę waluty wyświetlania (np. wartosc_usd), żeby model nie mylił walut."""
    if isinstance(obj, dict):
        return {(k[:-4] + "_" + code if isinstance(k, str) and k.endswith("_pln") else k): _rename_currency(v, code)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rename_currency(v, code) for v in obj]
    return obj
