"""Ekran Podatki: zestawienie do PIT-38 (pomoc obliczeniowa, nie porada podatkowa)."""
from __future__ import annotations

import pandas as pd

from core import tax
from server import blocks as B

METHODS = [("nbp", "Kursy NBP (D-1)"), ("report", "Kwoty z raportu XTB")]


def build(ctx, year: int, method: str) -> tax.TaxReport:
    # podatki nie zależą od widoku: zawsze wszystkie instrumenty (także wyłączone CFD), cała historia, złote
    return ctx.memo(("tax", year, method), lambda: tax.build_report(
        year, ctx.export_pln.closed, ctx.export_pln.open, ctx.export_pln.cash, ctx.meta_full, ctx.market.fx_pln,
        (ctx.pf_full or ctx.pf).qty, method))


def years(ctx) -> list[int]:
    return tax.available_years(ctx.export_pln.closed, ctx.export_pln.cash)


def _scope_notes(ctx) -> list:
    """Uwagi o zakresie: odznaczone rachunki i rachunki w walucie obcej."""
    out = []
    accounts = ctx.accounts
    off = [a["id"] for a in accounts if not a["selected"]]
    if off:
        out.append(B.note("Zestawienie obejmuje tylko zaznaczone rachunki. Pominięte: " + ", ".join(off) +
                          ". Do rozliczenia rocznego zaznacz wszystkie na pasku u góry.", "warn"))
    foreign = sorted({a["currency"] for a in accounts if a["selected"] and a["currency"] != "PLN"})
    if foreign:
        out.append(B.note(f"Rachunek w {', '.join(foreign)}: prowizje, opłaty, wyniki CFD i dywidendy bez kwoty na akcję "
                          "przeliczam na złote po kursie NBP z dnia operacji. Sprzedaże akcji i ETF-ów idą zwykłą drogą: "
                          "cena w walucie instrumentu razy kurs NBP z dnia poprzedzającego transakcję."))
    if ctx.base != "PLN":
        out.append(B.note(f"Ta zakładka jest zawsze w złotych, niezależnie od waluty wyświetlania ({ctx.base})."))
    if ctx.exclude_cfd or ctx.since_date is not None:
        out.append(B.note("Podatki liczę zawsze z całej historii i ze wszystkich instrumentów, także gdy w widoku "
                          "portfela pominięto CFD albo skrócono okres analizy."))
    return out


def screen(ctx, p) -> dict:
    ys = years(ctx)
    if not ys:
        return {"title": "PIT-38", "blocks": [B.note(tax.DISCLAIMER, "warn"),
                                              B.note("W eksporcie nie ma zamkniętych pozycji ani dywidend - nie ma czego rozliczać.")]}
    default = ys[-2] if len(ys) > 1 and ys[-1] == ctx.pf.as_of.year else ys[-1]     # domyślnie ostatni zamknięty rok
    year = p.int("year", default)
    year = year if year in ys else default
    method = p.str("method", "nbp", {k for k, _ in METHODS})
    rep = build(ctx, year, method)
    t, s, d, dv = rep.total, rep.securities, rep.derivatives, rep.dividends
    if t["income"] > 0:
        lead = f"Za {year} r. dochód ze sprzedaży to {B.f_zl(t['base'])}, a podatek 19% to {B.f_zl(t['tax'])}."
    else:
        lead = f"Za {year} r. sprzedaże zamknęły się stratą {B.f_zl(t['loss'])}. Podatek od nich wynosi 0 zł."
    sub = (f"Od dywidend zagranicznych do dopłaty w Polsce: {B.f_zl(dv['due'])}." if dv["gross"] > 0
           else "W tym roku nie było dywidend zagranicznych.")
    blocks = [
        B.statement(lead, sub),
        B.note(tax.DISCLAIMER, "warn"),
        *_scope_notes(ctx),
        B.controls([
            B.control("year", "segmented" if len(ys) <= 4 else "select", "Rok podatkowy", year, [(y, str(y)) for y in ys]),
            B.control("method", "segmented", "Przeliczenie walut", method, METHODS,
                      tip="Kursy NBP: średni kurs z dnia roboczego poprzedzającego każdą transakcję, zakupy dobierane metodą "
                          "FIFO. Kwoty z raportu: wartości w PLN tak, jak przeliczył je XTB - do porównania."),
        ]),
        B.metrics([
            B.metric("Akcje i ETF-y: przychód", s["revenue"], "zl", digits=2, tip="Przychód"),
            B.metric("Akcje i ETF-y: koszty", s["cost"], "zl", digits=2, tip="Koszty uzyskania",
                     note=f"w tym prowizje {B.f_zl(rep.commissions, 2)}" if rep.commissions else None),
            B.metric("Akcje i ETF-y: dochód", s["income"], "zl", digits=2, tip="Dochód", sign=True),
            B.metric("CFD: przychód", d["revenue"], "zl", digits=2,
                     tip="Suma zysków z zamkniętych pozycji CFD (instrumenty pochodne), w PLN z raportu XTB."),
            B.metric("CFD: koszty", d["cost"], "zl", digits=2, tip="Suma strat z zamkniętych pozycji CFD plus swapy i prowizje."),
            B.metric("CFD: dochód", d["income"], "zl", digits=2, sign=True, tip="Dochód",
                     note=f"{d['n']} zamkniętych pozycji"),
        ], "Sprzedaż papierów i instrumenty pochodne"),
        B.metrics([
            B.metric("Razem przychód", t["revenue"], "zl", digits=2, tip="Przychód"),
            B.metric("Razem koszty", t["cost"], "zl", digits=2, tip="Koszty uzyskania"),
            B.metric("Dochód / strata", t["income"], "zl", digits=2, tip="Dochód", sign=True),
            B.metric("Podstawa opodatkowania", t["base"], "zl", tip="Dochód zaokrąglony do pełnych złotych. Przy stracie 0."),
            B.metric("Podatek 19%", t["tax"], "zl", tip="19% podstawy, zaokrąglone do pełnych złotych."),
        ], "Do PIT-38"),
        B.metrics([
            B.metric("Dywidendy zagraniczne brutto", dv["gross"], "zl", digits=2,
                     tip="Suma dywidend ze spółek zagranicznych przed potrąceniem podatku."),
            B.metric("Podatek pobrany u źródła", dv["wht"], "zl", digits=2,
                     tip="Podatek potrącony za granicą (np. 15% w USA przy złożonym formularzu W-8BEN)."),
            B.metric("Podatek 19% w Polsce", dv["tax"], "zl", tip="19% kwoty brutto dywidend zagranicznych."),
            B.metric("Odliczenie podatku zagranicznego", dv["credit"], "zl",
                     tip="Podatek zapłacony za granicą, ale nie więcej niż 19% brutto dla danego kraju."),
            B.metric("Do dopłaty w Polsce", dv["due"], "zl", tip="Podatek 19% minus odliczenie. Jeśli broker pobrał już pełne "
                                                              "19%, wyjdzie 0."),
            B.metric("Dywidendy polskie brutto", dv["domestic_gross"], "zl", digits=2,
                     tip="Podatek od dywidend polskich spółek pobiera płatnik - nie wykazujesz ich w PIT-38.",
                     note=f"podatek pobrany: {B.f_zl(dv['domestic_tax_withheld'], 2)}"),
        ], "Dywidendy"),
    ]
    if rep.notes:
        blocks.append(B.bullets(rep.notes, "Uwagi"))
    if len(rep.foreign_income):
        fi = rep.foreign_income.reset_index()
        blocks.append(B.table(fi, [("country", "Kraj", "text"), ("income", "Dochód", "zl", 2), ("revenue", "Przychód", "zl", 2),
                                   ("cost", "Koszty", "zl", 2)], title="Akcje i ETF-y zagraniczne wg kraju (PIT/ZG)",
                              caption="Kraj pochodzi z tabeli tickerów. Dla ETF-ów sprawdź kraj rejestracji funduszu."))
    by = dv["by_country"]
    if len(by):
        blocks.append(B.table(by.reset_index(), [("country", "Kraj", "text"), ("gross", "Brutto", "zl", 2),
                                                 ("wht", "U źródła", "zl", 2), ("tax", "19%", "zl", 2),
                                                 ("credit", "Odliczenie", "zl", 2), ("due", "Dopłata", "zl", 2)],
                              title="Dywidendy zagraniczne wg kraju"))
    if len(rep.fifo):
        f = rep.fifo.assign(buy_date=rep.fifo["buy_date"].dt.strftime("%Y-%m-%d"),
                            sell_date=rep.fifo["sell_date"].dt.strftime("%Y-%m-%d"))
        blocks.append(B.table(f, [("symbol", "Symbol", "text"), ("income", "Dochód", "zl", 2), ("qty", "Ilość", "num", 4),
                                  ("buy_date", "Zakup", "text"), ("buy_rate", "Kurs zakupu", "num", 4), ("cost", "Koszt", "zl", 2),
                                  ("sell_date", "Sprzedaż", "text"), ("sell_rate", "Kurs sprzedaży", "num", 4),
                                  ("revenue", "Przychód", "zl", 2)],
                              title="Sprzedaże dobrane metodą FIFO", limit=8,
                              caption="Kurs to średni kurs NBP z dnia roboczego poprzedzającego transakcję (dla PLN 1,0000)."))
    blocks += [
        B.download("Pobierz zestawienie (PDF)", f"tax/{year}/{method}/zestawienie.pdf", f"PIT-38_{year}_zestawienie.pdf"),
        B.download("Pobierz transakcje FIFO (CSV)", f"tax/{year}/{method}/transakcje.csv", f"PIT-38_{year}_transakcje.csv"),
        B.download("Pobierz dywidendy (CSV)", f"tax/{year}/{method}/dywidendy.csv", f"PIT-38_{year}_dywidendy.csv"),
        B.download("Pobierz podsumowanie (CSV)", f"tax/{year}/{method}/podsumowanie.csv", f"PIT-38_{year}_podsumowanie.csv"),
        B.bullets([
            "Akcje i ETF-y rozliczam metodą FIFO osobno dla każdego instrumentu na każdym rachunku, a waluty przeliczam po średnim kursie NBP "
            "z dnia roboczego poprzedzającego transakcję.",
            "Za datę transakcji przyjmuję datę z raportu XTB, a nie datę rozliczenia w depozycie.",
            "Dla CFD przyjmuję kwoty w PLN z raportu: zyski jako przychód, straty, swapy i prowizje jako koszty.",
            "Zestawienie nie uwzględnia strat z lat ubiegłych, innych rachunków maklerskich ani pozycji przeniesionych od innego brokera.",
        ], "Jak to policzono"),
    ]
    return {"title": "PIT-38", "blocks": blocks}
