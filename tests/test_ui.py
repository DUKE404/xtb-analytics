"""Test interfejsu mobilnego w prawdziwej przeglądarce (Chrome bez okna, ekran telefonu 390x844).

Przechodzi przez wszystkie ekrany w trybie demo, sprawdza brak błędów JavaScript i podstawowe interakcje.
Pomijany, gdy nie ma Playwrighta lub przeglądarki. Zrzuty ekranu: ustaw UI_SHOTS=katalog.
"""
import os
import threading

import pytest

pw = pytest.importorskip("playwright.sync_api")

VIEWPORT = {"width": 390, "height": 844}


UI_USER, UI_PASSWORD = "tester", "test-haslo-123"
UI_ITERATIONS = 2000            # w aplikacji 600 000; w testach mniej, żeby logowanie było szybkie


def derive_auth_key(password: str, salt_hex: str, iterations: int) -> str:
    """To samo wyprowadzenie co web/js/vault.js (PBKDF2-SHA256 + HKDF "xtba-auth-v1") - w Pythonie.
    Konto założone tą funkcją loguje się w przeglądarce tylko wtedy, gdy obie implementacje dają ten sam klucz."""
    import hashlib
    import unicodedata

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    salt = bytes.fromhex(salt_hex)
    master = hashlib.pbkdf2_hmac("sha256", unicodedata.normalize("NFKC", password).encode(), salt, iterations, 32)
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=b"xtba-auth-v1").derive(master).hex()


@pytest.fixture(scope="module")
def accounts():
    from tests.helpers import make_accounts
    acc = make_accounts(iterations=UI_ITERATIONS)
    salt = "5a" * 16
    acc.register(UI_USER, derive_auth_key(UI_PASSWORD, salt, UI_ITERATIONS), salt)
    return acc


@pytest.fixture(scope="module")
def base_url(accounts):
    from werkzeug.serving import make_server

    from server.app import create_app
    from server.sessions import SessionStore
    from tests.test_server import synthetic_provider

    app = create_app(SessionStore(), provider_factory=synthetic_provider, accounts=accounts)
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


def clean_vault(accounts):
    """Każdy test zaczyna z pustym sejfem konta testowego (pliki i ustawienia nie przechodzą między testami)."""
    accounts.db.execute("DELETE FROM vault_items")


@pytest.fixture()
def page(browser, base_url, accounts):
    clean_vault(accounts)
    ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=2, is_mobile=True, has_touch=True, locale="pl-PL",
                              service_workers="block", bypass_csp=True)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: pg.errors.append(f"console: {m.text}") if m.type == "error" else None)
    pg.goto(base_url + "/")
    ui_login(pg)
    wait_ready(pg)
    yield pg
    ctx.close()


def ui_login(pg, user=UI_USER, password=UI_PASSWORD):
    pg.wait_for_selector(".login-form", timeout=60000)
    pg.fill("input[autocomplete=username]", user)
    pg.fill("input[type=password]", password)
    pg.locator(".login-form button[type=submit]").click()


def new_page(browser, base_url, **kw):
    ctx = browser.new_context(viewport=VIEWPORT, locale="pl-PL", service_workers="block", bypass_csp=True, **kw)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))
    pg.goto(base_url + "/")
    return ctx, pg


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


def test_statistics_screen_has_one_period_selector(page, base_url):
    """Okres wybiera się tylko paskiem nad treścią - na ekranie nie ma drugiej, osobnej kontrolki okresu."""
    page.goto(base_url + "/#/perf/perf-summary")
    wait_ready(page)
    assert page.locator("#subnav button[aria-current='page']").inner_text() == "Statystyki"
    assert page.locator("main .segmented button", has_text="12 miesięcy").count() == 0
    before = page.locator(".ledger .value").first.inner_text()
    page.locator("#viewbar button", has_text="12M").tap()
    page.wait_for_function("document.querySelector('#viewbar button[aria-pressed=true]')?.textContent === '12M'", timeout=90000)
    wait_ready(page)
    assert page.locator(".ledger .value").first.inner_text() != before
    text = page.inner_text("main")
    assert "MWR / XIRR" in text and "Przechwytywanie spadków" in text and "Największe obsunięcia" in text
    assert not page.errors, page.errors


def test_bottom_bar_and_moved_addresses(page, base_url):
    labels = page.locator("#tabs button:visible span").all_inner_texts()
    assert labels == ["Przegląd", "Spółki", "Wyniki", "Ryzyko", "Więcej"]
    page.locator("#tabs button", has_text="Spółki").tap()
    wait_ready(page)
    assert page.inner_text("#bar-title") == "Spółki" and page.locator("table.data").count() >= 1
    # stare adresy (zakładki w przeglądarce, skróty) trafiają w nowe miejsca
    for old, title, sub in (("#/fund/fund-longshort", "Ryzyko", "Long / short"), ("#/fund", "Wyniki", "Statystyki"),
                            ("#/fund/fund-tearsheet", "Wyniki", "Statystyki"), ("#/settings", "Dane i ustawienia", "Ustawienia")):
        page.evaluate(f"location.hash = '{old}'")
        page.wait_for_function(f"document.querySelector('#bar-title')?.textContent === '{title}'", timeout=30000)
        assert page.locator("#subnav button[aria-current='page']").inner_text() == sub, old
    page.evaluate("location.hash = '#/risk'")
    page.wait_for_function("document.querySelector('#bar-title')?.textContent === 'Ryzyko'")
    assert "CFD" in page.locator("#subnav").inner_text()                         # demo ma CFD
    page.locator("#viewbar button", has_text="Bez CFD").tap()
    page.wait_for_function("document.querySelector('#viewbar .opt')?.getAttribute('aria-pressed') === 'true'", timeout=90000)
    wait_ready(page)
    assert "CFD" not in page.locator("#subnav").inner_text()                     # bez CFD ekran znika z nawigacji
    assert not page.errors, page.errors


def test_table_sort_by_header_and_csv_export(page, base_url):
    page.goto(base_url + "/#/companies")
    wait_ready(page)
    table = page.locator("table.data").first
    col = lambda: [t.replace("\u2212", "-").replace("%", "").replace(",", ".").replace("+", "")
                   for t in table.locator("tbody tr td:nth-child(3)").all_inner_texts()]
    head = table.locator("th", has_text="Dzień").first
    head.locator("button").tap()
    assert head.get_attribute("aria-sort") == "descending"
    vals = [float(v) for v in col()]
    assert vals == sorted(vals, reverse=True)
    head.locator("button").tap()
    assert head.get_attribute("aria-sort") == "ascending" and [float(v) for v in col()] == sorted(vals)
    head.locator("button").tap()
    assert head.get_attribute("aria-sort") == "none"                             # trzecie kliknięcie: kolejność wyjściowa
    table.locator("th", has_text="Pozycja").locator("button").tap()
    names = table.locator("tbody tr td:first-child").all_inner_texts()
    assert names == sorted(names, key=str.casefold)
    with page.expect_download() as dl:
        page.locator(".csv-btn").first.click()
    assert dl.value.suggested_filename == "twoje-pozycje.csv"
    text = open(dl.value.path(), encoding="utf-8-sig", newline="").read()
    lines = text.split("\r\n")
    assert lines[0].startswith("Pozycja;Cena;Dzień (%);Dzień (kwota);") and len(lines) == 14
    amount = lines[1].split(";")[3]
    assert "." not in amount and len(amount.partition(",")[2]) <= 2               # przecinek dziesiętny, najwyżej grosze
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
    assert page.locator(".menu button").all_inner_texts()[0].startswith("Rynki")
    page.locator(".menu button", has_text="Dane i ustawienia").tap()
    page.wait_for_selector(".drop")
    assert "Ustawienia" in page.locator("#subnav").inner_text()
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
    ctx = browser.new_context(viewport=VIEWPORT, is_mobile=True, has_touch=True, locale="pl-PL", bypass_csp=True)
    pg = ctx.new_page()
    pg.goto(base_url + "/")
    ui_login(pg)
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
def desktop(browser, base_url, accounts):
    clean_vault(accounts)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, locale="pl-PL", service_workers="block", bypass_csp=True)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(f"pageerror: {e}"))
    pg.on("console", lambda m: pg.errors.append(f"console: {m.text}") if m.type == "error" else None)
    pg.goto(base_url + "/")
    ui_login(pg)
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
    page.locator("#tabs button", has_text="Wyniki").click()
    wait_ready(page)
    shot(page, "d6-statistics", full=False)
    page.locator("#tabs button", has_text="Przegląd").click()
    wait_ready(page)
    page.locator("#tabs button", has_text="Spółki").click()
    wait_ready(page)
    assert page.inner_text("#bar-title") == "Spółki" and "Na ostatniej sesji" in page.inner_text(".statement")
    assert page.locator("table.data tbody tr").count() >= 10 and page.locator(".feed li").count() >= 5
    assert page.locator("td.pos").count() >= 1 and page.locator("td.neg").count() >= 1      # zmiany na zielono i czerwono
    shot(page, "d5-companies-list")
    page.locator("#subnav button", has_text="Wycena").click()
    wait_ready(page)
    assert "rocznie przez 10 lat" in page.inner_text(".statement")
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


def test_period_bar_is_always_visible_and_switches_period(page):
    """Okres analizy wybiera się jednym dotknięciem z paska nad treścią - na każdym ekranie."""
    chips = page.locator("#viewbar button")
    assert page.locator("#viewbar").is_visible() and chips.first.inner_text() == "Całość"
    assert chips.first.get_attribute("aria-pressed") == "true"
    start_all = page.inner_text(".statement .sub")
    page.locator("#viewbar button", has_text="12M").tap()
    page.wait_for_function("document.querySelector('#viewbar button[aria-pressed=true]')?.textContent === '12M'", timeout=90000)
    wait_ready(page)
    assert page.inner_text(".statement .sub") != start_all and "12 mies." in page.inner_text("#bar-meta .view-btn")
    shot(page, "98-period-bar", full=False)
    page.goto(page.url.split("#")[0] + "#/perf/perf-summary")
    wait_ready(page)
    assert page.locator("#viewbar button[aria-pressed='true']").inner_text() == "12M"      # ten sam okres na innych ekranach
    page.locator("#viewbar button", has_text="Bez CFD").tap()
    page.wait_for_function("document.querySelector('#viewbar .opt')?.getAttribute('aria-pressed') === 'true'", timeout=90000)
    page.locator("#viewbar .range").tap()
    page.wait_for_selector(".sheet #view-from")
    assert not page.errors, page.errors


def test_stale_screen_link_falls_back_to_overview(page, base_url):
    """Link do ekranu, którego już nie ma (np. po wycofaniu zakładki), prowadzi na Przegląd zamiast błędu."""
    page.goto(base_url + "/#/calendar/calendar-days")
    wait_ready(page)
    assert page.inner_text("#bar-title") == "Przegląd" and "Nie ma ekranu" not in page.inner_text("main")
    # stara wersja aplikacji ma w pamięci nawigację z nieistniejącym ekranem -> po błędzie wraca na Przegląd
    page.evaluate("""() => import('/js/api.js').then((api) => {
        api.state.health.nav.more.unshift({id: 'calendar', label: 'Kalendarz', screens: [{id: 'calendar-days', label: 'Dni'}]});
    })""")
    page.evaluate("location.hash = '#/calendar/calendar-days'")
    page.wait_for_function("document.querySelector('#bar-title')?.textContent === 'Przegląd'", timeout=30000)
    wait_ready(page)
    assert "Nie ma ekranu" not in page.inner_text("main")
    assert not [e for e in page.errors if "404" not in e], page.errors


def test_companies_list_on_phone(page, base_url):
    page.goto(base_url + "/#/companies")
    wait_ready(page)
    assert page.inner_text("#bar-title") == "Spółki"
    assert page.locator("#subnav button[aria-current='page']").inner_text() == "Lista"
    assert "Na ostatniej sesji" in page.inner_text(".statement")
    assert page.locator("table.data").first.locator("tbody tr").count() == 13
    plus = page.locator("td.pos").first.inner_text()
    assert plus.startswith("+")                                                  # znak, nie tylko kolor
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # nagłówki są tekstem; odnośnik (gdy jest) otwiera się w nowej karcie bez dostępu do aplikacji
    assert page.locator(".feed li").count() >= 5 and page.locator(".feed-tag").first.inner_text()
    bad = page.evaluate("[...document.querySelectorAll('.feed a')].filter(a => a.target !== '_blank' || !a.rel.includes('noopener')).length")
    assert bad == 0
    page.locator(".segmented button", has_text="Termin raportu").tap()
    page.wait_for_function("document.querySelector('.segmented button[aria-pressed=true]')?.textContent === 'Termin raportu'", timeout=30000)
    wait_ready(page)
    first = page.locator("table.data").first.locator("tbody tr").first.inner_text()
    assert "za " in first or "dziś" in first or "jutro" in first
    shot(page, "94-companies-list")
    assert not page.errors, page.errors


def test_markets_tab_curve_quotes_and_calendar(page, base_url):
    page.locator("#tabs button", has_text="Więcej").tap()
    page.wait_for_selector(".menu")
    page.locator(".menu button", has_text="Rynki").tap()
    wait_ready(page)
    assert page.locator("#subnav button[aria-current='page']").inner_text() == "Stopy USA"
    assert "obligacji USA" in page.inner_text(".statement")
    # krzywa: trzy serie (dziś, tydzień, miesiąc temu) i odczyt pod palcem
    curve = page.locator(".chart svg").first
    assert page.locator(".legend").first.locator("li").count() == 3
    assert curve.locator("circle").count() >= 3 * 13
    box = curve.bounding_box()
    page.mouse.move(box["x"] + box["width"] * 0.9, box["y"] + box["height"] / 2)
    page.mouse.down()
    assert page.locator(".readout").first.inner_text() in ("20Y", "30Y")
    page.mouse.up()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, "95-markets-rates")

    page.locator("#subnav button", has_text="Notowania").tap()
    wait_ready(page)
    tables = page.inner_text("main")
    for name in ("S&P 500", "VIX", "EUR/USD", "USD/PLN", "Złoto"):
        assert name in tables
    shot(page, "96-markets-quotes")

    page.locator("#subnav button", has_text="Kalendarz").tap()
    wait_ready(page)
    assert "Najbliższa ważna publikacja" in page.inner_text(".statement")
    page.locator(".segmented button", has_text="USA i Europa").tap()
    page.wait_for_function("document.querySelector('.segmented button[aria-pressed=true]')?.textContent === 'USA i Europa'", timeout=30000)
    wait_ready(page)
    assert "Strefa euro" in page.inner_text("main")
    assert not page.locator("#viewbar").is_visible()                            # rynki nie zależą od okresu analizy
    shot(page, "97-markets-calendar")
    assert not page.errors, page.errors


def test_app_runs_under_strict_content_security_policy(browser, base_url):
    """Bez obejścia CSP: logowanie (WebCrypto), ekrany z wykresami i tabelami - zero naruszeń polityki."""
    ctx = browser.new_context(viewport=VIEWPORT, locale="pl-PL", service_workers="block")
    pg = ctx.new_page()
    violations = []
    pg.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None)
    pg.add_init_script("document.addEventListener('securitypolicyviolation', e => console.error('Content Security Policy: ' + e.violatedDirective + ' ' + e.blockedURI))")
    pg.goto(base_url + "/")
    ui_login(pg)
    wait_ready(pg)
    for h in ("#/perf/perf-summary", "#/risk/risk-var", "#/companies", "#/sim/sim-montecarlo", "#/import/settings"):
        pg.evaluate(f"location.hash = '{h}'")
        wait_ready(pg)
    assert pg.locator("svg").count() > 0
    assert not violations, violations
    ctx.close()


# --------------------------------------------------------------------------- konta, sejf, szyfrowanie

def _upload_usd(pg, tmp_path):
    from tests import real_layout as R
    pg.goto(pg.url.split("#")[0] + "#/import/import")
    pg.wait_for_selector(".drop input")
    path = tmp_path / R.usd_account()[0]
    path.write_bytes(R.usd_account()[1])
    pg.set_input_files(".drop input", str(path))
    pg.wait_for_function("document.querySelector('.statement')?.textContent.includes('własny eksport')", timeout=90000)
    return R


def test_wrong_password_is_rejected(browser, base_url, accounts):
    ctx, pg = new_page(browser, base_url)
    ui_login(pg, password="zle-haslo-000")
    pg.wait_for_selector(".login-msg > *", timeout=30000)
    assert "Nieprawidłowa nazwa użytkownika lub hasło" in pg.inner_text(".login-form")
    accounts.db.execute("UPDATE users SET failed = 0, locked_until = 0")
    ctx.close()


def test_portfolio_follows_the_account_to_another_device_and_is_encrypted(browser, base_url, accounts, tmp_path):
    clean_vault(accounts)
    ctx, pg = new_page(browser, base_url)
    ui_login(pg)
    wait_ready(pg)
    R = _upload_usd(pg, tmp_path)
    pg.locator("#bar-meta .view-btn").click()
    pg.wait_for_selector(".sheet")
    pg.locator(".sheet .segmented button", has_text="USD").click()
    pg.locator(".sheet .btn", has_text="Zastosuj").click()
    pg.wait_for_selector(".sheet", state="detached", timeout=90000)
    wait_ready(pg)
    pg.wait_for_timeout(1500)                                  # ustawienia trafiają do sejfu z krótkim opóźnieniem
    # w bazie tylko szyfrogramy: ani nazwy pliku (z numerem rachunku), ani treści arkusza, ani ustawień
    rows = accounts.db.all("SELECT item_key, data FROM vault_items")
    assert {r["item_key"] for r in rows} >= {"prefs"} and len(rows) == 2
    blob = " ".join(r["item_key"] + r["data"] for r in rows)
    import base64
    raw = b"".join(base64.b64decode(r["data"]) for r in rows)
    for secret in (R.USD_ACC, "USD_", "Closed Positions", "RKLB"):
        assert secret not in blob and secret.encode() not in raw
    assert b"PK\x03\x04" not in raw                             # xlsx (zip) nie leży jawnie
    ctx.close()
    # inne urządzenie: logowanie i ten sam portfel z tymi samymi ustawieniami
    ctx2, pg2 = new_page(browser, base_url)
    ui_login(pg2)
    wait_ready(pg2)
    assert "USD" in pg2.inner_text("#bar-meta .view-btn") and "dane przykładowe" not in pg2.inner_text("#bar-meta")
    pg2.goto(base_url + "/#/import/import")
    pg2.wait_for_selector(".ledger .link-btn")
    assert R.USD_ACC in pg2.inner_text("main")
    # wylogowanie czyści urządzenie: po odświeżeniu znowu ekran logowania, a w pamięci przeglądarki nie ma plików
    pg2.goto(base_url + "/#/import/settings")
    pg2.locator(".btn", has_text="Wyloguj").first.wait_for()
    pg2.locator(".account .btn", has_text="Wyloguj").last.click()
    pg2.wait_for_selector(".login-form")
    pg2.reload()
    pg2.wait_for_selector(".login-form")
    leftovers = pg2.evaluate("Object.keys(localStorage).filter(k => k.startsWith('xtba:'))")
    assert leftovers == [], leftovers
    assert not pg2.errors, pg2.errors
    ctx2.close()


def test_files_from_previous_version_move_into_the_vault(browser, base_url, accounts, tmp_path):
    """Telefon z poprzednią wersją (pliki w IndexedDB bez szyfrowania): po zalogowaniu pliki trafiają do sejfu
    i znikają z urządzenia."""
    from tests import real_layout as R
    clean_vault(accounts)
    ctx, pg = new_page(browser, base_url)
    pg.wait_for_selector(".login-form")
    name, data = R.usd_account()
    pg.evaluate("""([name, bytes]) => new Promise((ok, fail) => {
        const req = indexedDB.open('xtb-analytics', 1);
        req.onupgradeneeded = () => req.result.createObjectStore('files', { keyPath: 'name' });
        req.onsuccess = () => {
            const t = req.result.transaction('files', 'readwrite');
            t.objectStore('files').put({ name, type: '', data: new Uint8Array(bytes).buffer });
            t.oncomplete = () => { req.result.close(); ok(); };
        };
        req.onerror = fail;
    })""", [name, list(data)])
    ui_login(pg)
    pg.wait_for_function("document.querySelector('.statement')?.textContent.includes('własny eksport')", timeout=90000)
    assert accounts.db.one("SELECT COUNT(*) AS n FROM vault_items WHERE item_key LIKE 'f_%'")["n"] == 1
    pg.wait_for_timeout(300)
    assert "xtb-analytics" not in pg.evaluate("indexedDB.databases().then(d => d.map(x => x.name))")
    ctx.close()


def test_invite_registration_and_password_change(browser, base_url, accounts):
    clean_vault(accounts)
    # administrator generuje kod w ustawieniach
    ctx, pg = new_page(browser, base_url)
    ui_login(pg)
    wait_ready(pg)
    pg.goto(base_url + "/#/import/settings")
    pg.wait_for_selector(".account .btn", timeout=30000)
    assert "administrator" in pg.inner_text(".account")
    pg.locator(".btn", has_text="Wygeneruj kod zaproszenia").click()
    pg.wait_for_selector(".invite-code")
    code = pg.inner_text(".invite-code")
    assert len(code) == 14
    shot(pg, "a1-settings-account")
    ctx.close()
    # nowa osoba zakłada konto kodem
    ctx2, pg2 = new_page(browser, base_url)
    pg2.wait_for_selector(".login-form")
    pg2.locator(".link-btn", has_text="Mam kod zaproszenia").click()
    pg2.wait_for_selector("input[autocomplete=new-password]")
    pg2.fill("input[autocomplete=username]", "bartek")
    pw = pg2.locator("input[type=password]")
    pw.nth(0).fill("krotkie")
    pw.nth(1).fill("krotkie")
    pg2.fill("input[placeholder^='np.']", code)
    pg2.locator(".login-form button[type=submit]").click()
    assert "co najmniej 10 znaków" in pg2.inner_text(".login-form")            # walidacja w telefonie
    pw.nth(0).fill("bartek-ma-dobre-haslo")
    pw.nth(1).fill("bartek-ma-dobre-haslo")
    shot(pg2, "a2-register", full=False)
    pg2.locator(".login-form button[type=submit]").click()
    wait_ready(pg2)
    bart = accounts.db.one("SELECT id, is_admin FROM users WHERE username = 'bartek'")
    assert bart and not bart["is_admin"]
    # zmiana hasła: sejf szyfrowany od nowa, stare hasło przestaje działać
    pg2.wait_for_timeout(1200)
    before = accounts.db.one("SELECT data FROM vault_items WHERE user_id = ? AND item_key = 'prefs'", (bart["id"],))
    pg2.goto(base_url + "/#/import/settings")
    pg2.locator(".btn", has_text="Zmień hasło").click()
    sheet = pg2.locator(".sheet input[type=password]")
    sheet.nth(0).fill("bartek-ma-dobre-haslo")
    sheet.nth(1).fill("nowe-haslo-bartka-1")
    sheet.nth(2).fill("nowe-haslo-bartka-1")
    pg2.locator(".sheet .btn").click()
    pg2.locator(".account .note", has_text="Hasło zmienione").wait_for(timeout=60000)
    after = accounts.db.one("SELECT data FROM vault_items WHERE user_id = ? AND item_key = 'prefs'", (bart["id"],))
    assert before is None or after["data"] != before["data"]
    assert not pg2.errors, pg2.errors
    ctx2.close()
    ctx3, pg3 = new_page(browser, base_url)
    ui_login(pg3, "bartek", "bartek-ma-dobre-haslo")
    pg3.wait_for_selector(".login-msg > *")
    ui_login(pg3, "bartek", "nowe-haslo-bartka-1")
    wait_ready(pg3)
    ctx3.close()
    accounts.delete_user(bart["id"])


def test_dropped_connection_on_wake_does_not_switch_portfolio_to_demo(browser, base_url, accounts, tmp_path):
    """Telefon uśpił kartę podczas budzenia serwera i pierwsze przeliczenie portfela zerwało się:
    aplikacja ponawia próbę i pokazuje własny portfel, a w sejfie nie zapisuje trybu demo."""
    clean_vault(accounts)
    ctx, pg = new_page(browser, base_url)
    ui_login(pg)
    wait_ready(pg)
    _upload_usd(pg, tmp_path)
    pg.wait_for_timeout(1500)
    # następne uruchomienie: pierwsze POST /api/session zrywa się po stronie sieci
    dropped = {"n": 0}

    def flaky(route):
        if route.request.method == "POST" and dropped["n"] == 0:
            dropped["n"] += 1
            return route.abort("connectionreset")
        return route.continue_()

    pg.route("**/api/session", flaky)
    pg.reload()
    wait_ready(pg)
    assert dropped["n"] == 1
    assert "dane przykładowe" not in pg.inner_text("#bar-meta")
    assert pg.evaluate("JSON.parse(localStorage.getItem('xtba:mode'))") == "upload"
    pg.unroute("**/api/session")
    ctx.close()


def test_portfolio_stuck_on_demo_by_earlier_failure_comes_back(browser, base_url, accounts, tmp_path):
    """Stan po błędzie starszej wersji: pliki w sejfie, a zapisany tryb to demo (choć nikt go nie wybrał).
    Po starcie aplikacja wraca do plików; świadomy wybór danych przykładowych jest za to pamiętany."""
    clean_vault(accounts)
    ctx, pg = new_page(browser, base_url)
    ui_login(pg)
    wait_ready(pg)
    _upload_usd(pg, tmp_path)
    pg.wait_for_timeout(1500)
    # zapisz w sejfie tryb demo bez znacznika wyboru (tak jak robiła to poprzednia wersja po błędzie)
    pg.evaluate("""async () => {
        const store = await import('/js/store.js');
        store.prefs.set('mode', 'demo');
        store.prefs.remove('demo:chosen');
        await store.prefs.flush();
    }""")
    ctx.close()
    ctx2, pg2 = new_page(browser, base_url)
    ui_login(pg2)
    wait_ready(pg2)
    assert "dane przykładowe" not in pg2.inner_text("#bar-meta")
    # świadomy wybór danych przykładowych zostaje także po ponownym otwarciu
    pg2.goto(base_url + "/#/import/import")
    pg2.locator(".btn", has_text="Wróć do danych przykładowych").click()
    pg2.wait_for_function("document.querySelector('#bar-meta')?.textContent.includes('dane przykładowe')", timeout=90000)
    pg2.wait_for_timeout(1500)
    pg2.reload()
    wait_ready(pg2)
    assert "dane przykładowe" in pg2.inner_text("#bar-meta")
    assert not pg2.errors, pg2.errors
    ctx2.close()


# --------------------------------------------------------------------------- wykres z przybliżaniem

def _first_line(pg):
    return pg.locator("figure.chart:has(.chart-tools)").first


def _range_text(fig):
    return fig.locator(".window-sum .window-dates").inner_text() if fig.locator(".window-sum .window-dates").count() else ""


def test_line_chart_zoom_presets_navigator_and_linked_drawdown(page):
    fig = _first_line(page)
    tools = fig.locator(".chart-tools")
    assert tools.locator("button", has_text="Całość").get_attribute("aria-pressed") == "true"
    # zakres 3M: okno się zawęża, pod wykresem daty i zmiana w oknie, obsunięcie przybliża się razem
    tools.locator("button", has_text="3M").click()
    page.wait_for_timeout(100)
    assert tools.locator("button", has_text="3M").get_attribute("aria-pressed") == "true"
    dates = _range_text(fig)
    assert " – " in dates
    assert "Portfel" in fig.locator(".window-sum").inner_text()
    dd = page.locator("figure.chart", has_text="Obsunięcie od szczytu")
    assert _range_text(dd) == dates
    shot(page, "zoom-3m")
    # przybliż przyciskiem +, potem przesuń okno na mini-wykresie w lewo
    tools.locator("button.zoom[aria-label=Przybliż]").click()
    page.wait_for_timeout(100)
    narrower = _range_text(fig)
    assert narrower != dates
    nav = fig.locator(".chart-nav svg")
    nb = nav.bounding_box()
    win = fig.locator(".nav-window").bounding_box()
    cx, cy = win["x"] + win["width"] / 2, nb["y"] + nb["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    page.mouse.move(cx - nb["width"] * 0.4, cy, steps=6)
    page.mouse.up()
    page.wait_for_timeout(100)
    moved = _range_text(fig)
    assert moved != narrower and moved.split(" – ")[0] < narrower.split(" – ")[0] or moved != narrower
    # dwuklik wraca do całości
    fig.locator(".chart-body svg").dblclick()
    page.wait_for_timeout(100)
    assert tools.locator("button", has_text="Całość").get_attribute("aria-pressed") == "true"
    assert _range_text(dd) == ""
    assert not page.errors, page.errors


def test_line_chart_pinch_and_mouse_brush_zoom(page):
    fig = _first_line(page)
    body = fig.locator(".chart-body")
    b = body.bounding_box()
    y = b["y"] + b["height"] / 2
    # dwa palce rozsuwane od środka = przybliżenie
    page.evaluate("""([sel, y, x1, x2, w]) => {
        const node = document.querySelector(sel);
        const ev = (type, id, x) => node.dispatchEvent(new PointerEvent(type, { pointerId: id, pointerType: 'touch',
            clientX: x, clientY: y, bubbles: true, cancelable: true, pressure: 0.5, isPrimary: id === 1 }));
        ev('pointerdown', 1, x1); ev('pointerdown', 2, x2);
        for (let k = 1; k <= 5; k++) { ev('pointermove', 1, x1 - k * w); ev('pointermove', 2, x2 + k * w); }
        ev('pointerup', 1, x1 - 5 * w); ev('pointerup', 2, x2 + 5 * w);
    }""", ["figure.chart:has(.chart-tools) .chart-body", y, b["x"] + b["width"] * 0.45, b["x"] + b["width"] * 0.55, b["width"] * 0.06])
    page.wait_for_timeout(120)
    pinched = _range_text(fig)
    assert " – " in pinched
    assert fig.locator(".as-period").is_visible()
    fig.locator(".chart-tools button", has_text="Całość").click()
    page.wait_for_timeout(100)
    # przeciągnięcie myszą zaznacza fragment i go przybliża
    page.mouse.move(b["x"] + b["width"] * 0.3, y)
    page.mouse.down()
    page.mouse.move(b["x"] + b["width"] * 0.6, y, steps=8)
    page.mouse.up()
    page.wait_for_timeout(120)
    assert " – " in _range_text(fig)
    assert not page.errors, page.errors


def test_zoomed_range_becomes_analysis_period(page):
    fig = _first_line(page)
    fig.locator(".chart-tools button", has_text="6M").click()
    page.wait_for_timeout(100)
    start = _range_text(fig).split(" – ")[0]
    fig.locator(".as-period").click()
    page.wait_for_function(f"document.querySelector('#viewbar .range')?.textContent.includes('{start[:5]}')"
                           f" || document.querySelector('#bar-meta')?.textContent.includes('{start[:5]}')", timeout=90000)
    wait_ready(page)
    # po przeliczeniu cały wykres to już wybrany okres
    assert _first_line(page).locator(".chart-tools button", has_text="Całość").get_attribute("aria-pressed") == "true"
    page.evaluate("async () => { const api = await import('/js/api.js'); await api.setView({since: '', until: ''}); }")
    assert not page.errors, page.errors


def test_benchmark_curves_rebase_to_window_start(page, base_url):
    page.goto(base_url + "/#/perf/perf-benchmarks")
    wait_ready(page)
    fig = _first_line(page)
    fig.locator(".chart-tools button", has_text="1R").click()
    page.wait_for_timeout(120)
    # pierwszy punkt widocznego okna = 100 dla każdej serii
    box = fig.locator(".chart-body svg").bounding_box()
    page.mouse.move(box["x"] + 21, box["y"] + box["height"] / 2)
    page.wait_for_timeout(60)
    vals = fig.locator(".legend b").all_inner_texts()
    assert vals and all(v in ("100", "–") for v in vals), vals
    assert "%" in fig.locator(".window-sum").inner_text()
    shot(page, "zoom-bench")
    assert not page.errors, page.errors
