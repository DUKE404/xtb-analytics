"""Test interfejsu mobilnego w prawdziwej przeglądarce (Chrome bez okna, ekran telefonu 390x844).

Przechodzi przez wszystkie ekrany w trybie demo, sprawdza brak błędów JavaScript i podstawowe interakcje.
Pomijany, gdy nie ma Playwrighta lub przeglądarki. Zrzuty ekranu: ustaw UI_SHOTS=katalog.
"""
import os
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")

VIEWPORT = {"width": 390, "height": 844}


@pytest.fixture(scope="module")
def base_url():
    from werkzeug.serving import make_server

    from server.app import create_app
    from server.sessions import SessionStore
    from tests.test_server import synthetic_provider

    app = create_app(SessionStore(), provider_factory=synthetic_provider)
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as p:
        b = None
        for kwargs in ({"channel": "chrome"}, {"executable_path": "/usr/bin/google-chrome"}, {}):
            try:
                b = p.chromium.launch(args=["--no-sandbox"], **kwargs)
                break
            except Exception:
                continue
        if b is None:
            pytest.skip("brak przeglądarki Chromium/Chrome dla Playwrighta")
        yield b
        b.close()


@pytest.fixture()
def page(browser, base_url):
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2, is_mobile=True, has_touch=True, locale="pl-PL",
                              service_workers="block")
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: pg.errors.append(f"console: {m.text}") if m.type == "error" else None)
    pg.goto(base_url + "/")
    wait_ready(pg)
    yield pg
    ctx.close()


def wait_ready(pg):
    pg.wait_for_selector("#tabs:not([hidden])", timeout=60000)
    pg.wait_for_selector("main:not(.is-loading) .screen:not(.skeleton)", timeout=60000)
    pg.wait_for_timeout(150)   # wykresy rysują się w następnej klatce


def shot(pg, name, full=True):
    out = os.environ.get("UI_SHOTS")
    if out:
        os.makedirs(out, exist_ok=True)
        pg.screenshot(path=os.path.join(out, f"{name}.png"), full_page=full)


def nav_from_health(pg, base_url):
    return pg.request.get(base_url + "/api/health").json()["nav"]


def test_overview_loads_with_statement_and_six_numbers(page):
    assert page.inner_text("#bar-title") == "Przegląd"
    assert "19 dni na 20" in page.inner_text(".statement")
    assert page.locator(".ledger").first.locator("li").count() == 6
    assert page.locator(".chart svg").count() >= 2
    assert "dane przykładowe" in page.inner_text("#bar-meta")
    assert page.locator("#tabs button:visible").count() <= 5
    shot(page, "01-overview")
    assert not page.errors, page.errors


def test_metric_row_expands_explanation(page):
    row = page.locator("button.row").first
    tip = page.locator(".tip").first
    assert row.get_attribute("aria-expanded") == "false"
    row.tap()
    assert row.get_attribute("aria-expanded") == "true"
    assert "open" in tip.get_attribute("class") and len(tip.inner_text()) > 40
    row.tap()
    assert row.get_attribute("aria-expanded") == "false"


def test_every_screen_renders_without_errors(page, base_url):
    nav = nav_from_health(page, base_url)
    visited = 0
    for section in nav["tabs"] + nav["more"]:
        for scr in section.get("screens") or [{"id": section["id"]}]:
            page.goto(f"{base_url}/#/{section['id']}/{scr['id']}")
            wait_ready(page)
            assert page.locator(".note.error").count() == 0, f"{scr['id']}: {page.inner_text('.note.error')}"
            assert page.locator("text=Nieznany blok").count() == 0 and page.locator("text=Nieznany typ wykresu").count() == 0
            assert len(page.inner_text("main")) > 150, f"ekran {scr['id']} jest pusty"
            # nic nie może wystawać poza ekran w poziomie (poza przewijanymi tabelami)
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            assert overflow <= 1, f"ekran {scr['id']} przewija się w poziomie o {overflow}px"
            shot(page, f"{visited + 2:02d}-{scr['id']}")
            visited += 1
    assert visited >= 12
    assert not page.errors, page.errors


def test_period_control_refetches_and_keeps_choice(page, base_url):
    page.goto(base_url + "/#/perf/perf-summary")
    wait_ready(page)
    before = page.locator(".ledger .value").first.inner_text()
    page.locator(".segmented button", has_text="12 miesięcy").tap()
    page.wait_for_function("document.querySelector('.segmented button[aria-pressed=\"true\"]').textContent === '12 miesięcy'")
    wait_ready(page)
    assert page.locator(".ledger .value").first.inner_text() != before
    assert not page.errors, page.errors


def test_line_chart_scrub_and_heatmap_tap(page, base_url):
    svg = page.locator(".chart svg").first
    box = svg.bounding_box()
    last = page.locator(".readout").first.inner_text()
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.5)
    page.wait_for_timeout(50)
    assert page.locator(".readout").first.inner_text() != last        # odczyt pokazuje inną datę
    page.goto(base_url + "/#/perf/perf-periods")
    wait_ready(page)
    cell = page.locator(".chart svg rect[rx='3']").nth(14)
    cell.dispatch_event("pointerdown")
    assert "%" in page.locator(".readout").first.inner_text()
    assert not page.errors, page.errors


def test_more_menu_import_and_ticker_edit(page, base_url):
    page.locator("#tabs button", has_text="Więcej").tap()
    page.wait_for_selector(".menu")
    assert page.locator(".menu button").count() >= 2
    page.locator(".menu button", has_text="Dane i import").tap()
    page.wait_for_selector(".drop")
    assert "dane przykładowe" in page.inner_text(".statement").lower()
    page.locator("#subnav button", has_text="Tickery").tap()
    page.wait_for_selector(".map-row")
    page.locator(".map-row", has_text="PKN.PL").tap()
    page.wait_for_selector(".sheet")
    sector = page.locator(".sheet input").nth(4)
    assert sector.input_value() == "Energia"
    sector.fill("Paliwa")
    page.locator(".sheet .btn", has_text="Zapisz i przelicz").tap()
    page.wait_for_selector(".sheet", state="detached")
    page.wait_for_function("document.querySelector('.map-row .nm') && [...document.querySelectorAll('.map-row')].some(r => r.textContent.includes('Paliwa'))", timeout=60000)
    shot(page, "90-mapping-edited")
    assert not page.errors, page.errors


def test_upload_of_real_file_from_phone(page, base_url, sample_path):
    page.goto(base_url + "/#/import/import")
    page.wait_for_selector(".drop input")
    page.set_input_files(".drop input", sample_path)
    page.wait_for_function("document.querySelector('.statement') && document.querySelector('.statement').textContent.includes('własny eksport')", timeout=90000)
    assert "dane przykładowe" not in page.inner_text("#bar-meta")
    # po odświeżeniu strony plik wraca z pamięci telefonu (IndexedDB), bez ponownego wgrywania
    page.goto(base_url + "/#/overview")
    page.reload()
    wait_ready(page)
    assert "dane przykładowe" not in page.inner_text("#bar-meta")
    assert "19 dni na 20" in page.inner_text(".statement")
    assert not page.errors, page.errors


def _write(tmp_path, pair):
    path = tmp_path / pair[0]
    path.write_bytes(pair[1])
    return str(path)


def test_two_accounts_selection_and_display_currency(page, base_url, tmp_path):
    """Rachunek PLN i USD w prawdziwym układzie eksportu: dogrywanie, odznaczanie rachunku, przełączanie waluty."""
    from tests import real_layout as R

    page.goto(base_url + "/#/import/import")
    page.wait_for_selector(".drop input")
    page.set_input_files(".drop input", _write(tmp_path, R.usd_account()))
    page.wait_for_function("document.querySelector('.statement')?.textContent.includes('własny eksport')", timeout=90000)
    assert page.inner_text("#bar-meta .view-btn") == "PLN"
    # drugi rachunek dogrywa się do pierwszego, nie zastępuje go
    page.set_input_files(".drop input", _write(tmp_path, R.pln_account()))
    page.wait_for_function("document.querySelector('#bar-meta .view-btn')?.textContent.includes('2 z 2')", timeout=90000)
    assert page.locator(".ledger .link-btn").count() == 2
    shot(page, "95-two-accounts-import")

    page.goto(base_url + "/#/overview")
    wait_ready(page)
    assert "zł" in page.inner_text(".statement")
    page.locator("#bar-meta .view-btn").tap()
    page.wait_for_selector(".sheet")
    assert page.locator(".sheet .toggle input").count() == 2
    shot(page, "96-view-sheet", full=False)
    page.locator(".sheet .segmented button", has_text="USD").tap()
    page.locator(".sheet .toggle input").first.uncheck()               # odznacz rachunek PLN
    page.locator(".sheet .btn", has_text="Zastosuj").tap()
    page.wait_for_selector(".sheet", state="detached", timeout=90000)
    wait_ready(page)
    assert "1 z 2 rachunków · USD" in page.inner_text("#bar-meta .view-btn")
    text = page.inner_text("main")
    assert "$" in text and "zł" not in text
    shot(page, "97-overview-usd")

    # wybór przeżywa odświeżenie strony (pliki i ustawienia są w telefonie)
    page.reload()
    wait_ready(page)
    assert "1 z 2 rachunków · USD" in page.inner_text("#bar-meta .view-btn")
    # podatki zostają w złotych
    page.goto(base_url + "/#/tax")
    wait_ready(page)
    assert "zł" in page.inner_text("main")
    # nie da się odznaczyć wszystkiego
    page.locator("#bar-meta .view-btn").tap()
    page.wait_for_selector(".sheet")
    page.locator(".sheet .toggle input").nth(1).uncheck()
    page.locator(".sheet .btn", has_text="Zastosuj").tap()
    page.wait_for_selector(".sheet .note.warn")
    assert not page.errors, page.errors


def test_bad_file_shows_readable_error_and_keeps_session(page, base_url, tmp_path):
    bad = tmp_path / "cos.csv"
    bad.write_text("a;b;c\n1;2;3\n")
    page.goto(base_url + "/#/import/import")
    page.wait_for_selector(".drop input")
    page.set_input_files(".drop input", str(bad))
    page.wait_for_selector(".note.error")
    assert "Nie rozpoznano" in page.inner_text(".note.error")
    page.goto(base_url + "/#/overview")
    wait_ready(page)
    assert "19 dni na 20" in page.inner_text(".statement")


def test_custom_stress_scenario_form(page, base_url):
    page.goto(base_url + "/#/sim/sim-stress")
    wait_ready(page)
    row = page.locator("table.data tbody tr", has_text="Własny scenariusz").first
    before = row.inner_text()
    page.get_by_label("S&P 500 (%)").fill("-40")
    page.locator(".btn", has_text="Przelicz scenariusz").tap()
    page.wait_for_function("t => document.querySelector('main:not(.is-loading)') && ![...document.querySelectorAll('table.data tbody tr')].some(r => r.innerText === t)", arg=before, timeout=30000)
    after = page.locator("table.data tbody tr", has_text="Własny scenariusz").first.inner_text()
    assert after != before and "−" in after                       # głębszy spadek S&P -> strata
    assert page.get_by_label("S&P 500 (%)").input_value() == "-40"  # formularz pamięta wpisane wartości
    assert not page.errors, page.errors


def test_monte_carlo_horizon_switch(page, base_url):
    page.goto(base_url + "/#/sim/sim-montecarlo")
    wait_ready(page)
    assert "Za 5 lat" in page.inner_text(".statement")
    page.locator(".segmented button", has_text="10 lat").tap()
    page.locator(".btn", has_text="Przelicz symulację").tap()
    page.wait_for_function("document.querySelector('.statement').textContent.includes('Za 10 lat')", timeout=30000)
    assert page.locator(".chart svg polygon").count() == 2      # dwa pasma percentyli
    assert not page.errors, page.errors


def test_tax_screen_and_pdf_download(page, base_url):
    page.goto(base_url + "/#/tax/tax")
    wait_ready(page)
    assert "nie porada podatkowa" in page.inner_text(".note.warn")
    with page.expect_download(timeout=30000) as info:
        page.locator(".btn", has_text="Pobierz zestawienie (PDF)").tap()
    assert info.value.suggested_filename.startswith("PIT-38_") and info.value.suggested_filename.endswith(".pdf")
    with open(info.value.path(), "rb") as fh:
        assert fh.read(5) == b"%PDF-"
    shot(page, "95-tax")
    assert not page.errors, page.errors


def test_ai_screen_memo_and_chat(page, base_url, monkeypatch):
    """Dział AI z podstawionym modelem: memo, podgląd wysyłanych danych, czat."""
    from core import ai

    class Resp:
        status_code = 200

        def __init__(self, text):
            self._text = text

        def json(self):
            return {"content": [{"type": "text", "text": self._text}]}

    def fake_post(url, headers, body, timeout):
        if "memo" in body["messages"][-1]["content"]:
            return Resp("## Stan portfela\nPortfel jest wart **151 tys. zł**.\n\n## Główne ryzyka\n- Koncentracja w CFD\n- Kurs USD/PLN")
        return Resp("Najwięcej ryzyka dokłada pozycja CFD na S&P 500.")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(ai, "_post", fake_post)
    page.reload()
    wait_ready(page)
    page.goto(base_url + "/#/more")
    page.wait_for_selector(".menu")
    assert page.locator(".menu button", has_text="Memo ryzyka").count() == 1
    page.goto(base_url + "/#/ai/ai")
    page.wait_for_selector(".btn >> text=Napisz memo ryzyka")
    page.locator(".btn", has_text="Napisz memo ryzyka").tap()
    page.wait_for_selector(".memo h3")
    assert page.locator(".memo h3").all_inner_texts() == ["Stan portfela", "Główne ryzyka"]
    assert page.locator(".memo strong").first.inner_text() == "151 tys. zł" and page.locator(".memo li").count() == 2
    page.locator("details.section summary", has_text="Co dokładnie zostanie wysłane").tap()
    page.wait_for_function("document.querySelector('pre.json').textContent.includes('var95_1d_pln')")
    assert "Jan Przykładowy" not in page.inner_text("pre.json")
    page.locator(".chat-form input").fill("Co najbardziej zwiększa moje ryzyko?")
    page.locator(".chat-form .btn").tap()
    page.wait_for_selector(".msg.assistant")
    assert "CFD na S&P 500" in page.inner_text(".msg.assistant") and page.locator(".msg.user").count() == 1
    shot(page, "96-ai")
    assert not page.errors, page.errors


def test_ai_hidden_without_key(page, base_url, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    page.reload()
    wait_ready(page)
    page.goto(base_url + "/#/more")
    page.wait_for_selector(".menu")
    assert page.locator(".menu button", has_text="Memo ryzyka").count() == 0


def test_app_shell_opens_offline_from_service_worker(browser, base_url):
    """Po pierwszej wizycie powłoka aplikacji otwiera się bez sieci (np. gdy darmowy serwer jeszcze śpi)."""
    ctx = browser.new_context(viewport=VIEWPORT, is_mobile=True, has_touch=True, locale="pl-PL")
    pg = ctx.new_page()
    pg.goto(base_url + "/")
    wait_ready(pg)
    pg.wait_for_function("navigator.serviceWorker.controller !== null || navigator.serviceWorker.ready.then(() => true)", timeout=20000)
    pg.evaluate("navigator.serviceWorker.ready")
    pg.wait_for_timeout(800)                       # service worker kończy zapisywać pliki powłoki
    manifest = pg.request.get(base_url + "/manifest.webmanifest")
    assert manifest.ok and "manifest+json" in manifest.headers["content-type"] and manifest.json()["display"] == "standalone"
    ctx.set_offline(True)
    pg.reload()
    pg.wait_for_selector(".boot-line", timeout=15000)
    assert pg.locator("#bar-title").inner_text() == "XTB Analytics"            # powłoka z pamięci telefonu
    pg.wait_for_function("document.querySelector('.boot-line').textContent.includes('Budzę serwer')", timeout=20000)
    ctx.set_offline(False)
    wait_ready(pg)                                                              # po powrocie sieci aplikacja rusza sama
    assert "19 dni na 20" in pg.inner_text(".statement")
    ctx.close()


# --------------------------------------------------------------------------- szeroki ekran i nowe funkcje widoku

@pytest.fixture()
def desktop(browser, base_url):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="pl-PL", service_workers="block")
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: pg.errors.append(f"console: {m.text}") if m.type == "error" else None)
    pg.goto(base_url + "/")
    wait_ready(pg)
    yield pg
    ctx.close()


def test_desktop_layout_has_sidebar_and_tiles(desktop):
    page = desktop
    tabs = page.locator("#tabs").bounding_box()
    assert tabs["x"] == 0 and tabs["width"] < 260 and tabs["height"] > 800            # boczny panel, nie pasek na dole
    labels = page.locator("#tabs button:visible span").all_inner_texts()
    assert "Spółki" in labels and "Podatki" in labels and "Więcej" not in labels      # działy z "Więcej" widać od razu
    tiles = page.locator(".ledger > li")
    a, b = tiles.nth(0).bounding_box(), tiles.nth(1).bounding_box()
    assert abs(a["y"] - b["y"]) < 2 and b["x"] > a["x"] + 150                         # kafelki obok siebie
    assert page.locator("figure.chart").nth(0).bounding_box()["width"] > 900          # samotny wykres na całą szerokość
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, "d1-overview", full=False)
    page.locator("#tabs button", has_text="Spółki").click()
    wait_ready(page)
    assert page.inner_text("#bar-title") == "Spółki" and "rocznie przez 10 lat" in page.inner_text(".statement")
    assert page.locator("figure.chart").count() >= 3 and "nie rekomendacja" in page.inner_text("main")
    shot(page, "d2-companies")
    assert not page.errors, page.errors


def test_view_sheet_period_and_cfd_carve_out(desktop):
    page = desktop
    twr_before = page.inner_text(".ledger")
    page.locator("#bar-meta .view-btn").click()
    page.wait_for_selector(".sheet")
    assert page.locator(".sheet .toggle input").count() == 1                          # demo ma CFD -> przełącznik "Pomiń CFD"
    page.locator(".sheet .toggle input").check()
    page.select_option(".sheet select", "12m")
    shot(page, "d3-view-sheet", full=False)
    page.locator(".sheet .btn", has_text="Zastosuj").click()
    page.wait_for_selector(".sheet", state="detached", timeout=90000)
    wait_ready(page)
    label = page.inner_text("#bar-meta .view-btn")
    assert "bez CFD" in label and "12 mies." in label
    assert page.inner_text(".ledger") != twr_before
    page.locator("#tabs button", has_text="Ryzyko").click()
    wait_ready(page)
    # własny zakres dat
    page.locator("#bar-meta .view-btn").click()
    page.wait_for_selector(".sheet")
    page.select_option(".sheet select", "custom")
    page.fill("#view-from", "2025-06-02")
    page.fill("#view-to", "2026-03-31")
    page.locator(".sheet .btn", has_text="Zastosuj").click()
    page.wait_for_selector(".sheet", state="detached", timeout=90000)
    wait_ready(page)
    assert "31.03.2026" in page.inner_text("#bar-meta .view-btn")
    page.reload()
    wait_ready(page)
    assert "31.03.2026" in page.inner_text("#bar-meta .view-btn") and "bez CFD" in page.inner_text("#bar-meta .view-btn")
    assert not page.errors, page.errors
