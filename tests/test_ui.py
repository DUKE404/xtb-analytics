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


def shot(pg, name):
    out = os.environ.get("UI_SHOTS")
    if out:
        os.makedirs(out, exist_ok=True)
        pg.screenshot(path=os.path.join(out, f"{name}.png"), full_page=True)


def nav_from_health(pg, base_url):
    return pg.request.get(base_url + "/api/health").json()["nav"]


def test_overview_loads_with_statement_and_six_numbers(page):
    assert page.inner_text("#bar-title") == "Przegląd"
    assert "19 dni na 20" in page.inner_text(".statement")
    assert page.locator(".ledger").first.locator("li").count() == 6
    assert page.locator(".chart svg").count() >= 2
    assert "dane przykładowe" in page.inner_text("#bar-meta")
    assert page.locator("#tabs button").count() <= 5
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
