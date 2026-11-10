"""Zestawienie do PIT-38 - pomoc obliczeniowa, NIE porada podatkowa.

Zasady przyjęte w obliczeniach:
- akcje i ETF-y: metoda FIFO (pierwsze kupione - pierwsze sprzedane) osobno dla każdego instrumentu,
- przeliczenie walut: kurs średni NBP z ostatniego dnia roboczego poprzedzającego dzień transakcji,
- CFD (instrumenty pochodne): suma zysków jako przychód, suma strat i kosztów jako koszty, w PLN z raportu,
- dywidendy zagraniczne: 19% od kwoty brutto minus podatek pobrany u źródła (nie więcej niż 19% brutto),
- zaokrąglenia: podstawa i podatek do pełnych złotych.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TAX_RATE = 0.19
DISCLAIMER = ("To pomoc obliczeniowa, a nie porada podatkowa. Dokumentem dla urzędu jest PIT-8C wystawiony przez XTB; "
              "jeśli liczby się różnią, wyjaśnij to z doradcą podatkowym przed złożeniem zeznania.")


def nbp_rate_before(rates: pd.Series | None, when) -> tuple[float, pd.Timestamp | None]:
    """Kurs z ostatniego dnia publikacji NBP przed dniem `when` (D-1) i data tego kursu."""
    if rates is None:
        return 1.0, None
    s = rates.dropna()
    i = int(s.index.searchsorted(pd.Timestamp(when).normalize(), side="left")) - 1
    if i < 0:
        if not len(s):
            return 1.0, None
        return float(s.iloc[0]), s.index[0]       # transakcja sprzed początku danych: najbliższy dostępny kurs
    return float(s.iloc[i]), s.index[i]


def round_pln(x: float) -> float:
    """Zaokrąglenie do pełnych złotych (końcówki od 50 gr w górę)."""
    return float(np.floor(abs(x) + 0.5) * np.sign(x))


def _rate(fx: pd.DataFrame | None, currency: str, when) -> tuple[float, pd.Timestamp | None]:
    if currency == "PLN" or fx is None or currency not in fx.columns:
        return 1.0, None
    return nbp_rate_before(fx[currency], when)


def _is_cfd(meta: pd.DataFrame, symbol: str) -> bool:
    return symbol in meta.index and meta.at[symbol, "asset_class"] == "CFD"


# --------------------------------------------------------------------------- #
# Akcje i ETF-y: FIFO
# --------------------------------------------------------------------------- #

FIFO_COLS = ["year", "symbol", "country", "currency", "qty", "buy_date", "buy_price", "buy_rate", "buy_rate_date", "cost",
             "sell_date", "sell_price", "sell_rate", "sell_rate_date", "revenue", "income"]


def fifo_matches(closed: pd.DataFrame, open_: pd.DataFrame, meta: pd.DataFrame, fx: pd.DataFrame | None) -> pd.DataFrame:
    """Paruje sprzedaże z zakupami metodą FIFO (per instrument) i przelicza obie strony po kursie NBP D-1."""
    buys, sells = [], []
    for df, has_close in ((closed, True), (open_, False)):
        for t in df.itertuples(index=False):
            if _is_cfd(meta, t.symbol) or t.type != "BUY":
                continue
            buys.append((t.symbol, t.open_time, float(t.volume), float(t.open_price)))
            if has_close:
                sells.append((t.symbol, t.close_time, float(t.volume), float(t.close_price)))
    rows = []
    for sym in sorted({s[0] for s in sells}):
        cur = str(meta.at[sym, "currency"]) if sym in meta.index else "PLN"
        country = str(meta.at[sym, "country"]) if sym in meta.index and "country" in meta.columns else "Nieznany"
        queue = sorted([[b[1], b[2], b[3]] for b in buys if b[0] == sym], key=lambda x: x[0])
        for _, when, qty, price in sorted([s for s in sells if s[0] == sym], key=lambda x: x[1]):
            left = qty
            s_rate, s_date = _rate(fx, cur, when)
            while left > 1e-9 and queue:
                lot = queue[0]
                take = min(left, lot[1])
                b_rate, b_date = _rate(fx, cur, lot[0])
                cost, revenue = take * lot[2] * b_rate, take * price * s_rate
                rows.append({"year": int(pd.Timestamp(when).year), "symbol": sym, "country": country, "currency": cur,
                             "qty": take, "buy_date": pd.Timestamp(lot[0]).normalize(), "buy_price": lot[2],
                             "buy_rate": b_rate, "buy_rate_date": b_date, "cost": cost,
                             "sell_date": pd.Timestamp(when).normalize(), "sell_price": price, "sell_rate": s_rate,
                             "sell_rate_date": s_date, "revenue": revenue, "income": revenue - cost})
                lot[1] -= take
                left -= take
                if lot[1] <= 1e-9:
                    queue.pop(0)
    return pd.DataFrame(rows, columns=FIFO_COLS)


def report_amounts(closed: pd.DataFrame, meta: pd.DataFrame, year: int) -> dict:
    """Wariant porównawczy: przychód i koszt akcji/ETF wprost z kwot PLN w raporcie XTB (kurs brokera)."""
    c = closed[(closed["close_time"].dt.year == year) & ~closed["symbol"].map(lambda s: _is_cfd(meta, s))]
    revenue = float(c["sale_value"].fillna(0).sum())
    cost = float(c["purchase_value"].fillna(0).sum())
    return {"revenue": revenue, "cost": cost, "income": revenue - cost}


# --------------------------------------------------------------------------- #
# CFD i dywidendy
# --------------------------------------------------------------------------- #

def derivatives(closed: pd.DataFrame, meta: pd.DataFrame, year: int) -> dict:
    """CFD zamknięte w danym roku: zyski = przychód; straty, swapy i prowizje = koszty (PLN z raportu)."""
    c = closed[(closed["close_time"].dt.year == year) & closed["symbol"].map(lambda s: _is_cfd(meta, s))]
    gross = c["gross_pl"].fillna(0.0)
    charges = float(-(c["commission"].fillna(0).clip(upper=0) + c["swap"].fillna(0).clip(upper=0)
                      + c["rollover"].fillna(0).clip(upper=0)).sum())
    credits = float((c["commission"].fillna(0).clip(lower=0) + c["swap"].fillna(0).clip(lower=0)
                     + c["rollover"].fillna(0).clip(lower=0)).sum())
    revenue = float(gross[gross > 0].sum()) + credits
    cost = float(-gross[gross < 0].sum()) + charges
    return {"revenue": revenue, "cost": cost, "income": revenue - cost, "n": int(len(c))}


_DPS = re.compile(r"\b([A-Z]{3})\s+(\d+(?:[.,]\d+)?)\s*/\s*SHR", re.IGNORECASE)
DIV_COLS = ["year", "date", "symbol", "country", "currency", "gross_report", "wht_report", "gross", "wht", "method",
            "rate", "rate_date", "foreign"]


def dividends(cash: pd.DataFrame, meta: pd.DataFrame, fx: pd.DataFrame | None = None,
              qty: pd.DataFrame | None = None) -> pd.DataFrame:
    """Dywidendy i podatek u źródła, sparowane po instrumencie i dniu.

    Jeśli komentarz operacji zawiera kwotę na akcję w walucie (np. 'USD 0.2500/ SHR') i znamy stan posiadania
    z dnia poprzedniego, kwota brutto jest liczona od nowa po kursie NBP D-1; w innym razie brana jest kwota
    PLN z raportu. Podatek u źródła zachowuje proporcję z raportu.
    """
    if not len(cash):
        return pd.DataFrame(columns=DIV_COLS)
    d = cash[cash["kind"] == "DIVIDEND"]
    w = cash[cash["kind"] == "WHT"]
    wht_by = w.assign(day=w["time"].dt.normalize()).groupby(["symbol", "day"])["amount"].sum() if len(w) else {}
    rows = []
    for (sym, day), grp in d.assign(day=d["time"].dt.normalize()).groupby(["symbol", "day"]):
        gross_rep = float(grp["amount"].sum())
        wht_rep = float(-wht_by.get((sym, day), 0.0)) if len(w) else 0.0
        cur = str(meta.at[sym, "currency"]) if sym in meta.index else "PLN"
        country = str(meta.at[sym, "country"]) if sym in meta.index and "country" in meta.columns else "Nieznany"
        gross, method, rate, rate_date = gross_rep, "kwota z raportu", 1.0, None
        m = _DPS.search(" ".join(grp["comment"].astype(str)))
        if m and m.group(1).upper() != "PLN" and qty is not None and sym in qty.columns and fx is not None \
                and m.group(1).upper() in fx.columns:
            prev = qty[sym].loc[:day - pd.Timedelta(days=1)]
            held = float(prev.iloc[-1]) if len(prev) else 0.0
            if held > 0:
                rate, rate_date = nbp_rate_before(fx[m.group(1).upper()], day)
                candidate = held * float(m.group(2).replace(",", ".")) * rate
                # zabezpieczenie: gdy stan z eksportu nie pasuje do wypłaty (różnica > 10%), zostajemy przy raporcie
                if gross_rep > 0 and abs(candidate / gross_rep - 1.0) <= 0.10:
                    gross, method = candidate, "kurs NBP D-1"
        wht = wht_rep * (gross / gross_rep) if gross_rep > 0 else wht_rep
        rows.append({"year": int(day.year), "date": day, "symbol": sym, "country": country, "currency": cur,
                     "gross_report": gross_rep, "wht_report": wht_rep, "gross": gross, "wht": wht, "method": method,
                     "rate": rate, "rate_date": rate_date, "foreign": country != "Polska"})
    return pd.DataFrame(rows, columns=DIV_COLS)


def dividend_tax(div: pd.DataFrame, year: int) -> dict:
    """Podatek od dywidend zagranicznych: 19% brutto minus podatek u źródła (maks. 19% brutto), per kraj."""
    d = div[(div["year"] == year) & div["foreign"]] if len(div) else div
    by = pd.DataFrame(columns=["gross", "wht", "tax", "credit", "due"])
    if len(d):
        by = d.groupby("country")[["gross", "wht"]].sum()
        by["tax"] = by["gross"] * TAX_RATE
        by["credit"] = np.minimum(by["wht"], by["tax"])
        by["due"] = by["tax"] - by["credit"]
    dom = div[(div["year"] == year) & ~div["foreign"]] if len(div) else div
    tax = round_pln(float(by["tax"].sum())) if len(by) else 0.0
    credit = round_pln(float(by["credit"].sum())) if len(by) else 0.0
    return {"by_country": by, "gross": float(by["gross"].sum()) if len(by) else 0.0,
            "wht": float(by["wht"].sum()) if len(by) else 0.0, "tax": tax, "credit": credit,
            "due": max(tax - credit, 0.0),
            "domestic_gross": float(dom["gross"].sum()) if len(dom) else 0.0,
            "domestic_tax_withheld": float(dom["wht"].sum()) if len(dom) else 0.0}


# --------------------------------------------------------------------------- #
# Zestawienie roczne
# --------------------------------------------------------------------------- #

@dataclass
class TaxReport:
    year: int
    method: str                       # 'nbp' albo 'report'
    securities: dict
    derivatives: dict
    total: dict
    dividends: dict
    fifo: pd.DataFrame
    dividend_rows: pd.DataFrame
    foreign_income: pd.DataFrame      # dochód z akcji/ETF per kraj (do PIT/ZG)
    comparison: dict                  # akcje/ETF wg kwot z raportu XTB
    commissions: float
    interest: dict
    notes: list[str] = field(default_factory=list)


def available_years(closed: pd.DataFrame, cash: pd.DataFrame) -> list[int]:
    years = set()
    if len(closed):
        years |= set(closed["close_time"].dt.year.unique().tolist())
    if len(cash):
        years |= set(cash.loc[cash["kind"].isin(["DIVIDEND", "WHT"]), "time"].dt.year.unique().tolist())
    return sorted(int(y) for y in years)


def build_report(year: int, closed: pd.DataFrame, open_: pd.DataFrame, cash: pd.DataFrame, meta: pd.DataFrame,
                 fx: pd.DataFrame | None, qty: pd.DataFrame | None = None, method: str = "nbp") -> TaxReport:
    fifo_all = fifo_matches(closed, open_, meta, fx)
    fifo = fifo_all[fifo_all["year"] == year].reset_index(drop=True)
    sec_closed = closed[(closed["close_time"].dt.year == year) & ~closed["symbol"].map(lambda s: _is_cfd(meta, s))]
    commissions = float(-sec_closed["commission"].fillna(0).clip(upper=0).sum()) if len(sec_closed) else 0.0
    comparison = report_amounts(closed, meta, year)
    if method == "report":
        sec = {"revenue": comparison["revenue"], "cost": comparison["cost"] + commissions}
    else:
        sec = {"revenue": float(fifo["revenue"].sum()), "cost": float(fifo["cost"].sum()) + commissions}
    sec["income"] = sec["revenue"] - sec["cost"]
    der = derivatives(closed, meta, year)
    revenue, cost = sec["revenue"] + der["revenue"], sec["cost"] + der["cost"]
    income = revenue - cost
    base = round_pln(income) if income > 0 else 0.0
    total = {"revenue": revenue, "cost": cost, "income": income, "loss": -income if income < 0 else 0.0,
             "base": base, "tax": round_pln(base * TAX_RATE)}
    div_rows = dividends(cash, meta, fx if method == "nbp" else None, qty if method == "nbp" else None)
    div = dividend_tax(div_rows, year)

    foreign = fifo[fifo["country"] != "Polska"]
    foreign_income = foreign.groupby("country")[["revenue", "cost", "income"]].sum() if len(foreign) else \
        pd.DataFrame(columns=["revenue", "cost", "income"])
    ints = cash[cash["time"].dt.year == year] if len(cash) else cash
    interest = {"gross": float(ints.loc[ints["kind"] == "INTEREST", "amount"].sum()) if len(ints) else 0.0,
                "tax": float(-ints.loc[ints["kind"] == "INTEREST_TAX", "amount"].sum()) if len(ints) else 0.0}

    notes = []
    unmatched = _unmatched_sales(closed, open_, meta)
    if unmatched:
        notes.append("Sprzedaże bez pasującego zakupu w eksporcie (niepełna historia?): " + ", ".join(unmatched))
    if total["loss"] > 0:
        notes.append("Rok zamknął się stratą. Stratę można odliczać w kolejnych 5 latach - tego zestawienie nie liczy.")
    if len(div_rows) and method == "nbp" and (
            div_rows.loc[(div_rows["year"] == year) & div_rows["foreign"], "method"] == "kwota z raportu").any():
        notes.append("Część dywidend zagranicznych przyjęto w kwocie PLN z raportu XTB (brak danych do przeliczenia po kursie NBP).")
    return TaxReport(year=year, method=method, securities=sec, derivatives=der, total=total, dividends=div, fifo=fifo,
                     dividend_rows=div_rows[div_rows["year"] == year].reset_index(drop=True) if len(div_rows) else div_rows,
                     foreign_income=foreign_income, comparison=comparison, commissions=commissions, interest=interest,
                     notes=notes)


def _unmatched_sales(closed, open_, meta) -> list[str]:
    """W eksporcie XTB każda zamknięta pozycja niesie własny zakup, więc brak pary oznacza uszkodzone dane."""
    bad = closed[(closed["type"] != "BUY") & ~closed["symbol"].map(lambda s: _is_cfd(meta, s))]
    return sorted(bad["symbol"].unique().tolist())


# --------------------------------------------------------------------------- #
# Eksport
# --------------------------------------------------------------------------- #

def _pl(x, digits=2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return ""
    return f"{x:.{digits}f}".replace(".", ",")


def _d(x) -> str:
    return "" if x is None or pd.isna(x) else pd.Timestamp(x).strftime("%Y-%m-%d")


def fifo_csv(rep: TaxReport) -> str:
    """CSV dla polskiego Excela: średnik jako separator, przecinek dziesiętny, BOM dodawany przy wysyłce."""
    lines = ["Rok;Symbol;Kraj;Waluta;Ilość;Data zakupu;Cena zakupu;Kurs NBP zakupu;Data kursu zakupu;Koszt PLN;"
             "Data sprzedaży;Cena sprzedaży;Kurs NBP sprzedaży;Data kursu sprzedaży;Przychód PLN;Dochód PLN"]
    for r in rep.fifo.itertuples(index=False):
        lines.append(";".join([str(r.year), r.symbol, r.country, r.currency, _pl(r.qty, 4), _d(r.buy_date), _pl(r.buy_price, 4),
                               _pl(r.buy_rate, 4), _d(r.buy_rate_date), _pl(r.cost), _d(r.sell_date), _pl(r.sell_price, 4),
                               _pl(r.sell_rate, 4), _d(r.sell_rate_date), _pl(r.revenue), _pl(r.income)]))
    lines.append(f";;;;;;;;RAZEM;{_pl(float(rep.fifo['cost'].sum()))};;;;;{_pl(float(rep.fifo['revenue'].sum()))};"
                 f"{_pl(float(rep.fifo['income'].sum()))}")
    return "\n".join(lines) + "\n"


def dividends_csv(rep: TaxReport) -> str:
    lines = ["Rok;Data;Symbol;Kraj;Waluta;Brutto PLN;Podatek u źródła PLN;Metoda;Kurs NBP;Data kursu;Zagraniczna"]
    for r in rep.dividend_rows.itertuples(index=False):
        lines.append(";".join([str(r.year), _d(r.date), r.symbol, r.country, r.currency, _pl(r.gross), _pl(r.wht), r.method,
                               _pl(r.rate, 4) if r.method != "kwota z raportu" else "", _d(r.rate_date),
                               "tak" if r.foreign else "nie"]))
    return "\n".join(lines) + "\n"


def summary_rows(rep: TaxReport) -> list[tuple[str, str]]:
    t, s, d, dv = rep.total, rep.securities, rep.derivatives, rep.dividends
    money = lambda x: _pl(x) + " zł"  # noqa: E731
    rows = [
        ("Akcje i ETF-y: przychód", money(s["revenue"])), ("Akcje i ETF-y: koszty uzyskania", money(s["cost"])),
        ("Akcje i ETF-y: dochód / strata", money(s["income"])),
        ("Instrumenty pochodne (CFD): przychód", money(d["revenue"])), ("Instrumenty pochodne (CFD): koszty", money(d["cost"])),
        ("Instrumenty pochodne (CFD): dochód / strata", money(d["income"])),
        ("Razem przychód", money(t["revenue"])), ("Razem koszty", money(t["cost"])), ("Razem dochód / strata", money(t["income"])),
        ("Podstawa opodatkowania (pełne złote)", money(t["base"])), ("Podatek 19%", money(t["tax"])),
        ("Dywidendy zagraniczne brutto", money(dv["gross"])), ("Podatek pobrany u źródła za granicą", money(dv["wht"])),
        ("Podatek 19% od dywidend zagranicznych", money(dv["tax"])), ("Odliczenie podatku zagranicznego", money(dv["credit"])),
        ("Dywidendy zagraniczne: do dopłaty w Polsce", money(dv["due"])),
    ]
    return rows


def summary_csv(rep: TaxReport) -> str:
    head = f"Zestawienie do PIT-38 za rok {rep.year};{'kursy NBP D-1, FIFO' if rep.method == 'nbp' else 'kwoty PLN z raportu XTB'}"
    lines = [head, "Pozycja;Kwota"] + [f"{k};{v}" for k, v in summary_rows(rep)]
    lines += ["", "Dochód z akcji i ETF-ów zagranicznych wg kraju (PIT/ZG);Przychód;Koszty;Dochód"]
    for country, r in rep.foreign_income.iterrows():
        lines.append(f"{country};{_pl(r['revenue'])};{_pl(r['cost'])};{_pl(r['income'])}")
    lines += ["", "Dywidendy zagraniczne wg kraju;Brutto;Podatek u źródła;Podatek 19%;Odliczenie;Do dopłaty"]
    for country, r in rep.dividends["by_country"].iterrows():
        lines.append(f"{country};{_pl(r['gross'])};{_pl(r['wht'])};{_pl(r['tax'])};{_pl(r['credit'])};{_pl(r['due'])}")
    lines += ["", DISCLAIMER]
    return "\n".join(lines) + "\n"


def report_pdf(rep: TaxReport, font_dir: str | None = None) -> bytes:
    """PDF z zestawieniem (reportlab). Krój DejaVu z `font_dir` zapewnia polskie znaki."""
    import os

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold = "Helvetica", "Helvetica-Bold"
    if font_dir and os.path.exists(os.path.join(font_dir, "DejaVuSans.ttf")):
        if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(font_dir, "DejaVuSans.ttf")))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(font_dir, "DejaVuSans-Bold.ttf")))
        regular, bold = "DejaVuSans", "DejaVuSans-Bold"

    def txt(s: str) -> str:
        if regular != "Helvetica":
            return s
        import unicodedata                      # bez kroju z polskimi znakami: usuwamy ogonki zamiast pokazywać kwadraty
        s = s.replace("ł", "l").replace("Ł", "L")
        return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

    h1 = ParagraphStyle("h1", fontName=bold, fontSize=15, leading=19, spaceAfter=4)
    h2 = ParagraphStyle("h2", fontName=bold, fontSize=11, leading=14, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", fontName=regular, fontSize=8.5, leading=11.5)
    warn = ParagraphStyle("warn", parent=body, backColor=colors.HexColor("#fff4d6"), borderPadding=6, spaceBefore=6, spaceAfter=8)

    def table(data, widths=None, numeric_from=1):
        t = Table([[txt(str(c)) for c in row] for row in data], colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), bold, 8), ("FONT", (0, 1), (-1, -1), regular, 8),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black), ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
            ("ALIGN", (numeric_from, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5)]))
        return t

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=txt(f"Zestawienie do PIT-38 za {rep.year}"), author="XTB Analytics")
    method = "FIFO, kursy średnie NBP z dnia roboczego poprzedzającego transakcję" if rep.method == "nbp" else "kwoty PLN z raportu XTB"
    story = [Paragraph(txt(f"Zestawienie do PIT-38 za rok {rep.year}"), h1),
             Paragraph(txt(f"Metoda: {method}."), body), Paragraph(txt(DISCLAIMER), warn),
             Paragraph(txt("Podsumowanie"), h2), table([["Pozycja", "Kwota"]] + [list(r) for r in summary_rows(rep)], [120 * mm, 50 * mm])]
    if len(rep.foreign_income):
        story += [Paragraph(txt("Dochód z akcji i ETF-ów zagranicznych wg kraju (do załącznika PIT/ZG)"), h2),
                  table([["Kraj", "Przychód", "Koszty", "Dochód"]] + [[c, _pl(r["revenue"]), _pl(r["cost"]), _pl(r["income"])]
                                                                      for c, r in rep.foreign_income.iterrows()])]
    by = rep.dividends["by_country"]
    if len(by):
        story += [Paragraph(txt("Dywidendy zagraniczne wg kraju"), h2),
                  table([["Kraj", "Brutto", "Podatek u źródła", "Podatek 19%", "Odliczenie", "Do dopłaty"]] +
                        [[c, _pl(r["gross"]), _pl(r["wht"]), _pl(r["tax"]), _pl(r["credit"]), _pl(r["due"])] for c, r in by.iterrows()])]
    if len(rep.fifo):
        story += [Paragraph(txt("Sprzedaże akcji i ETF-ów (FIFO)"), h2)]
        rows = [["Symbol", "Ilość", "Zakup", "Cena", "Kurs", "Koszt PLN", "Sprzedaż", "Cena", "Kurs", "Przychód PLN", "Dochód PLN"]]
        for r in rep.fifo.itertuples(index=False):
            rows.append([r.symbol, _pl(r.qty, 4).rstrip("0").rstrip(","), _d(r.buy_date), _pl(r.buy_price), _pl(r.buy_rate, 4),
                         _pl(r.cost), _d(r.sell_date), _pl(r.sell_price), _pl(r.sell_rate, 4), _pl(r.revenue), _pl(r.income)])
        story.append(table(rows))
    for n in rep.notes:
        story.append(Paragraph(txt("Uwaga: " + n), body))
    story += [Spacer(1, 8), Paragraph(txt("Wygenerowano w aplikacji XTB Analytics. Prowizje od akcji i ETF-ów "
                                          f"({_pl(rep.commissions)} zł) doliczono do kosztów. Odsetki od wolnych środków: "
                                          f"{_pl(rep.interest['gross'])} zł, podatek pobrany przez brokera: {_pl(rep.interest['tax'])} zł."), body)]
    doc.build(story)
    return buf.getvalue()
