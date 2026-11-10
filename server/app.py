"""Aplikacja Flask: API + pliki statyczne interfejsu mobilnego.

Uruchomienie lokalne:  python -m server            (http://localhost:8000)
Produkcja (Render):    gunicorn server.app:app     (patrz render.yaml)

Zmienne środowiskowe:
  APP_PASSWORD        - jeśli ustawione, aplikacja wymaga hasła (zalecane przy publicznym adresie)
  ANTHROPIC_API_KEY   - włącza dział AI (memo ryzyka, czat)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import traceback

import pandas as pd
from flask import Flask, Response, request, send_from_directory
from werkzeug.exceptions import HTTPException

from data.xtb_parser import XTBFormatError
from server.blocks import jsonable
from server.context import BENCH_CHOICES, DEFAULT_BENCH, DEFAULT_RF, build_context
from server.screens import SCREENS, Params, nav_config
from server.sessions import SessionStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
VERSION = "1.0.0"
MAX_UPLOAD_MB = 40


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "error"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def _token(password: str) -> str:
    return hmac.new(password.encode(), b"xtb-analytics-session-v1", hashlib.sha256).hexdigest()


def create_app(store: SessionStore | None = None, provider_factory=None) -> Flask:
    """provider_factory(is_demo) pozwala testom podstawić dostawcę notowań (bez sieci)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(ROOT, ".env"))
    except Exception:
        pass
    os.chdir(ROOT)  # ścieżki względne (.cache, data/sample)

    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    app.json.ensure_ascii = False
    sessions = store or SessionStore()
    app.extensions["sessions"] = sessions
    login_attempts: dict[str, list[float]] = {}

    def password() -> str:
        return os.environ.get("APP_PASSWORD", "").strip()

    def ai_enabled() -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())

    def ok(payload, status: int = 200) -> Response:
        return Response(json.dumps(jsonable(payload), ensure_ascii=False, allow_nan=False),
                        status=status, mimetype="application/json")

    def get_ctx(sid: str):
        ctx = sessions.get(sid)
        if ctx is None:
            raise ApiError("Sesja wygasła - aplikacja odtworzy ją automatycznie.", 404, "session_expired")
        return ctx

    def session_meta(sid: str, ctx) -> dict:
        pf = ctx.pf
        return {
            "sid": sid, "is_demo": ctx.is_demo, "summary": ctx.export.summary(), "notes": ctx.export.notes,
            "warnings": pf.warnings, "as_of": pf.as_of, "start": pf.dates[0], "source": ctx.market.source,
            "equity": float(pf.equity.iloc[-1]), "mapping": ctx.mapping.to_dict("records"),
            "unmapped": [s for s in ctx.mapping.loc[(ctx.mapping["yf_ticker"] == "") & (ctx.mapping["stooq_ticker"] == ""), "symbol"]],
            "price_sources": getattr(ctx.provider, "sources", {}),
        }

    def make_ctx(files, overrides):
        provider = provider_factory(files is None) if provider_factory else None
        try:
            return build_context(files, overrides, provider)
        except XTBFormatError as exc:
            raise ApiError(str(exc), 422, "bad_format") from exc
        except ValueError as exc:
            raise ApiError(f"Nie udało się odtworzyć portfela: {exc}", 422, "bad_data") from exc

    # ------------------------------------------------------------------ błędy i autoryzacja
    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return ok({"error": exc.message, "code": exc.code}, exc.status)

    @app.errorhandler(413)
    def _too_large(_):
        return ok({"error": f"Plik jest za duży (limit {MAX_UPLOAD_MB} MB).", "code": "too_large"}, 413)

    @app.errorhandler(404)
    def _not_found(_):
        if request.path.startswith("/api/"):
            return ok({"error": "Nie ma takiego adresu API.", "code": "not_found"}, 404)
        return send_from_directory(WEB_DIR, "index.html")   # aplikacja jednostronicowa

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        if isinstance(exc, HTTPException):
            return ok({"error": exc.description or exc.name, "code": f"http_{exc.code}"}, exc.code or 500)
        app.logger.error("Błąd: %s\n%s", exc, traceback.format_exc())
        return ok({"error": f"Błąd serwera: {exc}", "code": "server_error"}, 500)

    @app.before_request
    def _auth():
        if not request.path.startswith("/api/") or request.path in ("/api/health", "/api/login"):
            return None
        pw = password()
        if not pw:
            return None
        header = request.headers.get("Authorization", "")
        supplied = header[7:] if header.startswith("Bearer ") else ""
        if not hmac.compare_digest(supplied, _token(pw)):
            return ok({"error": "Podaj hasło do aplikacji.", "code": "auth_required"}, 401)
        return None

    @app.after_request
    def _headers(resp: Response):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp

    # ------------------------------------------------------------------ API
    @app.get("/api/health")
    def health():
        return ok({"ok": True, "version": VERSION, "auth_required": bool(password()), "ai_enabled": ai_enabled(),
                   "nav": nav_config(ai_enabled()),
                   "settings": {"rf": DEFAULT_RF, "bench": DEFAULT_BENCH, "bench_choices": [
                       {"value": k, "label": _bench_label(k)} for k in BENCH_CHOICES]}})

    @app.post("/api/login")
    def login():
        pw = password()
        if not pw:
            return ok({"token": ""})
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        now = time.time()
        recent = [t for t in login_attempts.get(ip, []) if now - t < 60]
        if len(recent) >= 8:
            raise ApiError("Za dużo prób. Odczekaj minutę.", 429, "rate_limited")
        supplied = str((request.get_json(silent=True) or {}).get("password", ""))
        if not hmac.compare_digest(supplied.encode(), pw.encode()):
            login_attempts[ip] = recent + [now]
            raise ApiError("Nieprawidłowe hasło.", 401, "bad_password")
        return ok({"token": _token(pw)})

    @app.post("/api/session")
    def create_session():
        """Nowa sesja: dane przykładowe (demo=1) albo wgrane pliki eksportu (+ opcjonalne poprawki mapowania)."""
        overrides = _overrides(request.form.get("overrides"))
        if request.form.get("demo") in ("1", "true"):
            files = None
        else:
            files = [(f.filename or "plik", f.read()) for f in request.files.getlist("files")]
            files = [(n, b) for n, b in files if b]
            if not files:
                raise ApiError("Nie wybrano pliku. Wgraj eksport z xStation 5 (.xlsx lub .csv).", 400, "no_file")
        ctx = make_ctx(files, overrides)
        sid = sessions.put(ctx)
        return ok(session_meta(sid, ctx), 201)

    @app.get("/api/session/<sid>")
    def get_session(sid):
        return ok(session_meta(sid, get_ctx(sid)))

    @app.delete("/api/session/<sid>")
    def delete_session(sid):
        sessions.drop(sid)
        return ok({"ok": True})

    @app.put("/api/session/<sid>/mapping")
    def update_mapping(sid):
        """Poprawki tabeli mapowań -> przeliczenie portfela w tej samej sesji."""
        old = get_ctx(sid)
        body = request.get_json(silent=True) or {}
        rows = body.get("mapping") or []
        overrides = pd.DataFrame(rows) if rows else None
        ctx = make_ctx(None if old.is_demo else old.files, overrides)
        sessions.put(ctx, sid)
        return ok(session_meta(sid, ctx))

    @app.route("/api/session/<sid>/screen/<name>", methods=["GET", "POST"])
    def screen(sid, name):
        ctx = get_ctx(sid)
        fn = SCREENS.get(name)
        if fn is None:
            raise ApiError(f"Nie ma ekranu „{name}”.", 404, "no_screen")
        raw = dict(request.args)
        if request.method == "POST":
            raw.update(request.get_json(silent=True) or {})
        t0 = time.time()
        out = fn(ctx, Params(raw))
        out["ms"] = int((time.time() - t0) * 1000)
        return ok(out)

    @app.get("/api/session/<sid>/tax/<int:year>/<method>/<name>")
    def tax_export(sid, year, method, name):
        """Eksport zestawienia podatkowego: PDF albo CSV (średnik, przecinek dziesiętny, BOM dla Excela)."""
        from core import tax as tax_core
        from server.screens import tax as tax_screen

        ctx = get_ctx(sid)
        if method not in ("nbp", "report") or year not in tax_screen.years(ctx):
            raise ApiError("Nie ma takiego zestawienia.", 404, "no_report")
        rep = tax_screen.build(ctx, year, method)
        if name == "zestawienie.pdf":
            data, mime = tax_core.report_pdf(rep, os.path.join(ROOT, "server", "fonts")), "application/pdf"
        else:
            maker = {"transakcje.csv": tax_core.fifo_csv, "dywidendy.csv": tax_core.dividends_csv,
                     "podsumowanie.csv": tax_core.summary_csv}.get(name)
            if maker is None:
                raise ApiError("Nie ma takiego pliku.", 404, "no_report")
            data, mime = ("\ufeff" + maker(rep)).encode("utf-8"), "text/csv; charset=utf-8"
        return Response(data, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="PIT-38_{year}_{name}"'})

    # ------------------------------------------------------------------ pliki statyczne
    @app.get("/")
    def index():
        resp = send_from_directory(WEB_DIR, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/<path:path>")
    def static_files(path):
        full = os.path.join(WEB_DIR, path)
        if not os.path.isfile(full):
            if path.startswith("api/"):
                raise ApiError("Nie ma takiego adresu API.", 404, "not_found")
            resp = send_from_directory(WEB_DIR, "index.html")
            resp.headers["Cache-Control"] = "no-cache"
            return resp
        resp = send_from_directory(WEB_DIR, path)
        if path in ("sw.js", "manifest.webmanifest") or path.endswith((".js", ".css", ".html")):
            resp.headers["Cache-Control"] = "no-cache"      # zawsze sprawdzaj świeżość; offline obsługuje service worker
        if path == "sw.js":
            resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    return app


def _bench_label(key: str) -> str:
    from data.providers import BENCHMARK_LABELS
    return BENCHMARK_LABELS.get(key, key)


def _overrides(raw: str | None) -> pd.DataFrame | None:
    if not raw:
        return None
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return pd.DataFrame(rows) if isinstance(rows, list) and rows else None


app = create_app()
