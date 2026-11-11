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
from server import blocks as B
from server.blocks import jsonable
from data.loader import BASE_CHOICES, SINCE_PRESETS
from server.context import BENCH_CHOICES, DEFAULT_BENCH, DEFAULT_RF, build_context
from server.screens import SCREENS, Params, nav_config
from server.sessions import SessionStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
VERSION = "1.1.0"


def build_id(root: str) -> str:
    """Odcisk plików interfejsu i listy ekranów. Zmienia się przy każdym wdrożeniu, które coś zmienia -
    otwarta na telefonie stara wersja aplikacji porównuje go i sama się przeładowuje."""
    h = hashlib.sha1(",".join(sorted(SCREENS)).encode())
    for base, _, names in sorted(os.walk(root)):
        for n in sorted(names):
            if n.endswith((".js", ".css", ".html", ".webmanifest")):
                with open(os.path.join(base, n), "rb") as fh:
                    h.update(n.encode() + fh.read())
    return h.hexdigest()[:12]
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
        from core import ai
        return ai.enabled()

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
            "base": ctx.base, "base_choices": BASE_CHOICES,
            "accounts": [{k: a[k] for k in ("id", "currency", "closed", "open", "cash", "as_of", "selected", "files")}
                         for a in ctx.accounts],
            "files_used": sorted({f for a in ctx.accounts for f in a.get("files", [])}),
            "has_cfd": ctx.has_cfd, "exclude_cfd": ctx.exclude_cfd, "since": ctx.since, "since_date": ctx.since_date,
            "first_date": (ctx.pf_full or pf).dates[0], "until": ctx.until, "last_date": (ctx.pf_full or pf).dates[-1],
            "since_choices": [{"value": "", "label": "Od początku"}] + [
                {"value": k, "label": v} for k, v in SINCE_PRESETS.items()],
        }

    def make_ctx(files, overrides, base="PLN", accounts=None, parsed=None, exclude=None, exclude_cfd=False, since="",
                 until=""):
        provider = provider_factory(files is None) if provider_factory else None
        try:
            return build_context(files, overrides, provider, base=base, accounts=accounts, parsed=parsed, exclude=exclude,
                                 exclude_cfd=exclude_cfd, since=since, until=until)
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
    build = build_id(WEB_DIR)

    @app.get("/api/health")
    def health():
        return ok({"ok": True, "version": VERSION, "build": build, "auth_required": bool(password()), "ai_enabled": ai_enabled(),
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
        ctx = make_ctx(files, overrides, _base(request.form.get("base")), exclude=_accounts(request.form.get("exclude")),
                       exclude_cfd=request.form.get("exclude_cfd") in ("1", "true"), since=_since(request.form.get("since")),
                       until=_date(request.form.get("until")))
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
        ctx = make_ctx(None if old.is_demo else old.files, overrides, old.base,
                       [a["id"] for a in old.accounts if a["selected"]], old.parsed,
                       exclude_cfd=old.exclude_cfd, since=old.since, until=old.until)
        sessions.put(ctx, sid)
        return ok(session_meta(sid, ctx))

    @app.put("/api/session/<sid>/view")
    def update_view(sid):
        """Zmiana waluty wyświetlania albo zaznaczonych rachunków -> przeliczenie w tej samej sesji."""
        old = get_ctx(sid)
        body = request.get_json(silent=True) or {}
        base = _base(body.get("base")) if body.get("base") else old.base
        accounts = body.get("accounts")
        if isinstance(accounts, list):
            accounts = [str(a) for a in accounts]
            if not accounts:
                raise ApiError("Zaznacz przynajmniej jeden rachunek.", 400, "no_account")
        else:
            accounts = [a["id"] for a in old.accounts if a["selected"]]
        exclude_cfd = bool(body["exclude_cfd"]) if "exclude_cfd" in body else old.exclude_cfd
        since = _since(body["since"]) if "since" in body else old.since
        ctx = make_ctx(None if old.is_demo else old.files, old.overrides, base, accounts, old.parsed,
                       exclude_cfd=exclude_cfd, since=since, until=_date(body["until"]) if "until" in body else old.until)
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
        B.set_currency(ctx.base)
        out = fn(ctx, Params(raw))
        out["currency"] = ctx.base
        out["ms"] = int((time.time() - t0) * 1000)
        return ok(out)

    def ai_payload(sid):
        from core import ai
        from server import ai_payload as builder

        if not ai.enabled():
            raise ApiError("Dział AI jest wyłączony - na serwerze nie ustawiono ANTHROPIC_API_KEY.", 404, "ai_disabled")
        ctx = get_ctx(sid)
        B.set_currency(ctx.base)
        body = request.get_json(silent=True) or {}
        p = Params({**dict(request.args), **{k: v for k, v in body.items() if k in ("rf", "bench")}})
        return ctx.memo(("ai_payload", round(p.rf, 4), p.bench), lambda: builder.build(ctx, p.rf, p.bench)), body

    @app.get("/api/session/<sid>/ai/payload")
    def ai_payload_view(sid):
        payload, _ = ai_payload(sid)
        return ok({"payload": payload})

    @app.post("/api/session/<sid>/ai/memo")
    def ai_memo(sid):
        from core import ai
        payload, _ = ai_payload(sid)
        try:
            return ok({"text": ai.risk_memo(payload), "model": ai.model_name()})
        except ai.AIError as exc:
            raise ApiError(str(exc), 502, "ai_error") from exc

    @app.post("/api/session/<sid>/ai/chat")
    def ai_chat(sid):
        from core import ai
        payload, body = ai_payload(sid)
        history = body.get("messages") if isinstance(body.get("messages"), list) else []
        try:
            return ok({"text": ai.chat(payload, history), "model": ai.model_name()})
        except ai.AIError as exc:
            raise ApiError(str(exc), 502 if history else 400, "ai_error") from exc

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
        if path.endswith(".webmanifest"):
            resp.headers["Content-Type"] = "application/manifest+json; charset=utf-8"
        return resp

    return app


def _bench_label(key: str) -> str:
    from data.providers import BENCHMARK_NAMES
    return BENCHMARK_NAMES.get(key, key)


def _base(raw) -> str:
    code = str(raw or "PLN").upper()
    return code if code in BASE_CHOICES else "PLN"


def _date(raw) -> str:
    try:
        return pd.Timestamp(str(raw).strip()).strftime("%Y-%m-%d") if raw and str(raw).strip() else ""
    except (ValueError, TypeError):
        return ""


def _since(raw) -> str:
    """Kod okna analizy: pusty, jeden z gotowych okresów albo data RRRR-MM-DD."""
    code = str(raw or "").strip().lower()
    if code in SINCE_PRESETS:
        return code
    try:
        return pd.Timestamp(code).strftime("%Y-%m-%d") if code else ""
    except (ValueError, TypeError):
        return ""


def _accounts(raw: str | None) -> list[str] | None:
    """Lista numerów rachunków do wliczenia (JSON); brak albo błąd = wszystkie."""
    if not raw:
        return None
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return [str(a) for a in rows] if isinstance(rows, list) and rows else None


def _overrides(raw: str | None) -> pd.DataFrame | None:
    if not raw:
        return None
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return pd.DataFrame(rows) if isinstance(rows, list) and rows else None


app = create_app()
