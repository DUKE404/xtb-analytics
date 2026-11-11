"""Robi zrzuty ekranów aplikacji w trybie demo (do README). Wymaga: pip install playwright pillow.

Użycie: python scripts/make_screenshots.py
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from data.synthetic import SyntheticMarketData  # noqa: E402
from server.app import create_app  # noqa: E402
from server.sessions import SessionStore  # noqa: E402

SCREENS = [("overview", "#/overview"), ("ryzyko", "#/risk/risk-var"), ("symulacje", "#/sim/sim-montecarlo"), ("podatki", "#/tax/tax")]


def main():
    app = create_app(SessionStore(), provider_factory=lambda demo: SyntheticMarketData())
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    out = os.path.join(ROOT, "docs")
    shots = []
    with sync_playwright() as p:
        browser = None
        for kw in ({"channel": "chrome"}, {"executable_path": "/usr/bin/google-chrome"}, {}):
            try:
                browser = p.chromium.launch(args=["--no-sandbox"], **kw)
                break
            except Exception:
                continue
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                  has_touch=True, locale="pl-PL", service_workers="block")
        page = ctx.new_page()
        page.goto(base + "/")
        page.wait_for_selector("#tabs:not([hidden])", timeout=60000)
        for name, route in SCREENS:
            page.goto(base + "/" + route)
            page.wait_for_selector("main:not(.is-loading) .screen:not(.skeleton)", timeout=60000)
            page.wait_for_timeout(400)
            path = os.path.join(out, f"ekran-{name}.png")
            page.screenshot(path=path)
            shots.append(path)
        browser.close()
    srv.shutdown()
    ims = [Image.open(s).convert("RGB") for s in shots]
    gap = 24
    sheet = Image.new("RGB", (sum(i.width for i in ims) + gap * (len(ims) + 1), ims[0].height + 2 * gap), "#0b1015")
    x = gap
    for im in ims:
        sheet.paste(im, (x, gap))
        x += im.width + gap
    sheet = sheet.resize((sheet.width // 2, sheet.height // 2), Image.LANCZOS)
    sheet.save(os.path.join(out, "ekrany.png"), optimize=True)
    for s in shots:
        os.remove(s)
    print("zapisano docs/ekrany.png", sheet.size)


if __name__ == "__main__":
    main()
