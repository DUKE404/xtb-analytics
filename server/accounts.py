"""Konta, logowanie i sejf na zaszyfrowane portfele.

Model bezpieczeństwa
--------------------
* Hasło nie opuszcza telefonu. Telefon wyprowadza z niego (PBKDF2-SHA256, 600 000 iteracji, sól użytkownika)
  klucz główny, a z niego (HKDF) dwa niezależne klucze: klucz logowania - wysyłany na serwer - i klucz
  szyfrowania, który zostaje w telefonie. Z klucza logowania nie da się odtworzyć klucza szyfrowania.
* Serwer przechowuje tylko scrypt(klucz logowania) i szyfrogramy AES-GCM. Ani dostawca bazy, ani hostingu,
  ani ktoś, kto wykradnie bazę, nie odczyta plików z xStation ani ustawień. Nazwy plików też są zaszyfrowane.
* Do obliczeń telefon odszyfrowuje pliki i wysyła je na czas sesji analizy - tak jak wcześniej. Serwer trzyma
  je wtedy wyłącznie w pamięci i nie zapisuje na dysk.
* Zapomniane hasło = utracony sejf (eksport z xStation można zrobić ponownie). Nie ma "resetu hasła".
* Tokeny logowania są losowe (256 bitów), w bazie leży tylko ich skrót; wygasają po 30 dniach bez użycia.
* Limity: blokada konta po 5 nieudanych próbach (rosnąca do 1 h) i limit prób z jednego adresu IP.
* Nowe konta tylko z jednorazowym kodem zaproszenia; pierwsze konto (administrator) - z kodem
  konfiguracji, czyli dotychczasowym hasłem aplikacji APP_PASSWORD.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import threading
import time

from server.db import Database

TOKEN_TTL = 30 * 86400              # sesja wygasa po 30 dniach bez użycia
TOKEN_REFRESH = 3600                # last_seen / expires odświeżane najwyżej raz na godzinę
INVITE_TTL = 7 * 86400
LOCK_AFTER = 5                      # nieudane próby, po których konto jest chwilowo blokowane
LOCK_BASE, LOCK_MAX = 60, 3600      # 1 min, 2 min, 4 min ... do 1 h
IP_WINDOW, IP_MAX = 600, 30         # najwyżej 30 nieudanych prób z jednego adresu w 10 minut
MAX_ITEM_BYTES = 20 * 1024 * 1024   # szyfrogram w base64
MAX_USER_BYTES = 80 * 1024 * 1024
MAX_ITEMS = 60
KDF_ITERATIONS = 600_000            # PBKDF2-SHA256 w telefonie (zalecenie OWASP 2023+)
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SALT_RE = re.compile(r"^[0-9a-f]{32}$")
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
B64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


class AuthError(Exception):
    def __init__(self, message: str, status: int = 400, code: str = "error"):
        super().__init__(message)
        self.message, self.status, self.code = message, status, code


def _now() -> int:
    return int(time.time())


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _scrypt(secret: str, salt: str) -> str:
    return hashlib.scrypt(secret.encode(), salt=bytes.fromhex(salt), maxmem=64 * 1024 * 1024, **SCRYPT).hex()


def normalize_username(raw) -> str:
    return str(raw or "").strip().lower()


def check_username(name: str) -> str:
    if not USERNAME_RE.match(name):
        raise AuthError("Nazwa użytkownika: 3-32 znaki, małe litery, cyfry, kropka, myślnik lub podkreślnik.", 400, "bad_username")
    return name


def _auth_key(raw) -> str:
    key = str(raw or "").lower()
    if not HEX64_RE.match(key):
        raise AuthError("Nieprawidłowe dane logowania.", 400, "bad_request")
    return key


class Accounts:
    def __init__(self, db: Database, secret: str | None = None, setup_code: str | None = None):
        self.db = db
        # sekret serwera: do udawanych soli nieistniejących kont (żeby nie dało się sprawdzić, kto ma konto)
        self.secret = (secret if secret is not None else os.environ.get("SECRET_KEY", "")) or secrets.token_hex(32)
        self._setup_code = setup_code
        self.iterations = KDF_ITERATIONS
        self._ip_fail: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @property
    def setup_code(self) -> str:
        return (self._setup_code if self._setup_code is not None else os.environ.get("APP_PASSWORD", "")).strip()

    # ------------------------------------------------------------------ stan
    def has_users(self) -> bool:
        return self.db.one("SELECT COUNT(*) AS n FROM users")["n"] > 0

    def status(self) -> dict:
        return {"has_users": self.has_users(), "setup_code_required": bool(self.setup_code),
                "storage": self.db.label, "persistent": self.db.persistent, "kdf_iterations": self.iterations}

    # ------------------------------------------------------------------ limity prób
    def _ip_check(self, ip: str) -> None:
        now = time.time()
        with self._lock:
            recent = [t for t in self._ip_fail.get(ip, []) if now - t < IP_WINDOW]
            self._ip_fail[ip] = recent
            if len(recent) >= IP_MAX:
                raise AuthError("Za dużo nieudanych prób z tego połączenia. Spróbuj za kilka minut.", 429, "rate_limited")

    def _ip_fail_add(self, ip: str) -> None:
        with self._lock:
            self._ip_fail.setdefault(ip, []).append(time.time())
            if len(self._ip_fail) > 5000:                  # nie rośnij bez końca
                self._ip_fail = {k: v for k, v in list(self._ip_fail.items())[-1000:]}

    # ------------------------------------------------------------------ parametry logowania
    def params(self, username) -> dict:
        """Sól do wyprowadzenia kluczy. Dla nieistniejącego konta - stała, udawana sól (bez zdradzania, że go nie ma)."""
        name = normalize_username(username)
        row = self.db.one("SELECT kdf_salt FROM users WHERE username = ?", (name,)) if name else None
        salt = row["kdf_salt"] if row else hmac.new(self.secret.encode(), b"salt|" + name.encode(), hashlib.sha256).hexdigest()[:32]
        return {"salt": salt, "iterations": self.iterations}

    # ------------------------------------------------------------------ rejestracja
    def register(self, username, auth_key, kdf_salt, invite: str = "", ip: str = "?", agent: str = "") -> dict:
        self._ip_check(ip)
        name = check_username(normalize_username(username))
        key = _auth_key(auth_key)
        salt = str(kdf_salt or "").lower()
        if not SALT_RE.match(salt):
            raise AuthError("Nieprawidłowe dane rejestracji.", 400, "bad_request")
        code = str(invite or "").strip()
        with self.db.transaction() as tx:
            first = tx.one("SELECT COUNT(*) AS n FROM users")["n"] == 0
            inv = None
            if first:
                if self.setup_code and not hmac.compare_digest(code.encode(), self.setup_code.encode()):
                    self._ip_fail_add(ip)
                    raise AuthError("Pierwsze konto zakładasz kodem konfiguracji - to hasło aplikacji (APP_PASSWORD) "
                                    "ustawione w Render.", 403, "bad_setup_code")
            else:
                inv = tx.one("SELECT * FROM invites WHERE code_hash = ?", (_sha(code.upper()),)) if code else None
                if not inv or inv["used_by"] or inv["expires"] < _now():
                    self._ip_fail_add(ip)
                    raise AuthError("Kod zaproszenia jest nieprawidłowy, wygasł albo został już użyty.", 403, "bad_invite")
            if tx.one("SELECT 1 AS x FROM users WHERE username = ?", (name,)):
                raise AuthError("Ta nazwa użytkownika jest zajęta.", 409, "username_taken")
            uid = secrets.token_hex(12)
            vsalt = secrets.token_hex(16)
            tx.execute("INSERT INTO users (id, username, kdf_salt, verifier, verifier_salt, is_admin, created, last_login) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (uid, name, salt, _scrypt(key, vsalt), vsalt, int(first), _now(), _now()))
            if inv:
                tx.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE id = ?", (uid, _now(), inv["id"]))
            token = self._new_token(uid, agent)
        return {"token": token, "user": self.user(uid)}

    # ------------------------------------------------------------------ logowanie
    def login(self, username, auth_key, ip: str = "?", agent: str = "") -> dict:
        self._ip_check(ip)
        name = normalize_username(username)
        key = _auth_key(auth_key)
        row = self.db.one("SELECT * FROM users WHERE username = ?", (name,)) if name else None
        now = _now()
        if row and row["locked_until"] > now:
            wait = row["locked_until"] - now
            raise AuthError(f"Za dużo nieudanych prób. Konto odblokuje się za {max(1, round(wait / 60))} min.", 429, "locked")
        # scrypt liczony zawsze, także dla nieistniejącego konta - czas odpowiedzi nie zdradza, czy konto istnieje
        expected = row["verifier"] if row else "0" * 64
        got = _scrypt(key, row["verifier_salt"] if row else "00" * 16)
        if not row or not hmac.compare_digest(got, expected):
            self._ip_fail_add(ip)
            if row:
                failed = row["failed"] + 1
                lock = now + min(LOCK_MAX, LOCK_BASE * 2 ** (failed - LOCK_AFTER)) if failed >= LOCK_AFTER else 0
                self.db.execute("UPDATE users SET failed = ?, locked_until = ? WHERE id = ?", (failed, lock, row["id"]))
            raise AuthError("Nieprawidłowa nazwa użytkownika lub hasło.", 401, "bad_credentials")
        with self.db.transaction() as tx:
            tx.execute("UPDATE users SET failed = 0, locked_until = 0, last_login = ? WHERE id = ?", (now, row["id"]))
            tx.execute("DELETE FROM auth_sessions WHERE expires < ?", (now,))
            token = self._new_token(row["id"], agent)
        return {"token": token, "user": self.user(row["id"])}

    def _new_token(self, uid: str, agent: str) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        self.db.execute("INSERT INTO auth_sessions (token_hash, user_id, created, expires, last_seen, agent) VALUES (?, ?, ?, ?, ?, ?)",
                        (_sha(token), uid, now, now + TOKEN_TTL, now, str(agent or "")[:160]))
        return token

    def authenticate(self, token: str) -> dict | None:
        """Użytkownik dla tokenu albo None. Przedłuża ważność sesji (najwyżej raz na godzinę)."""
        if not token or len(token) > 200:
            return None
        h = _sha(token)
        row = self.db.one("SELECT s.user_id, s.expires, s.last_seen, u.username, u.is_admin FROM auth_sessions s "
                          "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (h,))
        now = _now()
        if not row or row["expires"] < now:
            return None
        if now - row["last_seen"] > TOKEN_REFRESH:
            self.db.execute("UPDATE auth_sessions SET last_seen = ?, expires = ? WHERE token_hash = ?", (now, now + TOKEN_TTL, h))
        return {"id": row["user_id"], "username": row["username"], "is_admin": bool(row["is_admin"]), "token_hash": h}

    def logout(self, token_hash: str) -> None:
        self.db.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))

    def logout_all(self, uid: str, keep: str | None = None) -> int:
        if keep:
            return self.db.execute("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash <> ?", (uid, keep))
        return self.db.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))

    def _verify(self, uid: str, auth_key) -> dict:
        row = self.db.one("SELECT * FROM users WHERE id = ?", (uid,))
        key = _auth_key(auth_key)
        if not row or not hmac.compare_digest(_scrypt(key, row["verifier_salt"]), row["verifier"]):
            raise AuthError("Obecne hasło jest nieprawidłowe.", 403, "bad_credentials")
        return row

    def user(self, uid: str) -> dict:
        row = self.db.one("SELECT id, username, is_admin, created, last_login FROM users WHERE id = ?", (uid,))
        if not row:
            raise AuthError("Konto nie istnieje.", 404, "no_user")
        n = self.db.one("SELECT COUNT(*) AS n FROM auth_sessions WHERE user_id = ? AND expires >= ?", (uid, _now()))["n"]
        used = self.db.one("SELECT COALESCE(SUM(size), 0) AS b, "
                           "COALESCE(SUM(CASE WHEN substr(item_key, 1, 2) = 'f_' THEN 1 ELSE 0 END), 0) AS n "      # bez % (psycopg)
                           "FROM vault_items WHERE user_id = ?", (uid,))
        return {"id": row["id"], "username": row["username"], "is_admin": bool(row["is_admin"]), "created": row["created"],
                "last_login": row["last_login"], "devices": int(n), "vault_bytes": int(used["b"]), "vault_items": int(used["n"]),
                "vault_limit": MAX_USER_BYTES}

    # ------------------------------------------------------------------ zmiana hasła i usuwanie konta
    def change_password(self, uid: str, old_key, new_key, new_salt, items: list[dict], keep_token: str) -> dict:
        """Nowe hasło = nowe klucze, więc telefon przesyła cały sejf zaszyfrowany od nowa. Wszystko albo nic."""
        self._verify(uid, old_key)
        key = _auth_key(new_key)
        salt = str(new_salt or "").lower()
        if not SALT_RE.match(salt):
            raise AuthError("Nieprawidłowe dane.", 400, "bad_request")
        clean = [self._check_item(i.get("key"), i.get("iv"), i.get("data")) for i in (items or [])]
        current = {r["item_key"] for r in self.db.all("SELECT item_key FROM vault_items WHERE user_id = ?", (uid,))}
        if {c[0] for c in clean} != current:
            raise AuthError("Sejf zmienił się w trakcie zmiany hasła. Spróbuj jeszcze raz.", 409, "vault_changed")
        self._check_quota(uid, clean, replace_all=True)
        vsalt = secrets.token_hex(16)
        with self.db.transaction() as tx:
            tx.execute("UPDATE users SET kdf_salt = ?, verifier = ?, verifier_salt = ? WHERE id = ?",
                       (salt, _scrypt(key, vsalt), vsalt, uid))
            for k, iv, data in clean:
                tx.execute("UPDATE vault_items SET iv = ?, data = ?, size = ?, updated = ? WHERE user_id = ? AND item_key = ?",
                           (iv, data, len(data), _now(), uid, k))
            tx.execute("DELETE FROM auth_sessions WHERE user_id = ? AND token_hash <> ?", (uid, keep_token))
        return self.user(uid)

    def delete_user(self, uid: str) -> None:
        with self.db.transaction() as tx:
            tx.execute("DELETE FROM vault_items WHERE user_id = ?", (uid,))
            tx.execute("DELETE FROM auth_sessions WHERE user_id = ?", (uid,))
            tx.execute("DELETE FROM invites WHERE created_by = ? AND used_by IS NULL", (uid,))
            tx.execute("DELETE FROM users WHERE id = ?", (uid,))

    def delete_account(self, uid: str, auth_key) -> None:
        row = self._verify(uid, auth_key)
        if row["is_admin"]:
            others = self.db.one("SELECT COUNT(*) AS n FROM users WHERE id <> ?", (uid,))["n"]
            if others:
                raise AuthError("Administrator nie może usunąć konta, dopóki są inne konta. Najpierw usuń je w ustawieniach.",
                                409, "admin_has_users")
        self.delete_user(uid)

    # ------------------------------------------------------------------ sejf
    def _check_item(self, key, iv, data) -> tuple[str, str, str]:
        key, iv, data = str(key or ""), str(iv or ""), str(data or "")
        if not KEY_RE.match(key) or not re.match(r"^[A-Za-z0-9+/]{16}$", iv) or not data or not B64_RE.match(data):
            raise AuthError("Nieprawidłowy element sejfu.", 400, "bad_item")
        if len(data) > MAX_ITEM_BYTES:
            raise AuthError(f"Plik jest za duży (limit {MAX_ITEM_BYTES // 1024 // 1024} MB po zaszyfrowaniu).", 413, "too_large")
        return key, iv, data

    def _check_quota(self, uid: str, items: list[tuple], replace_all: bool = False) -> None:
        rows = self.db.all("SELECT item_key, size FROM vault_items WHERE user_id = ?", (uid,))
        sizes = {} if replace_all else {r["item_key"]: r["size"] for r in rows}
        for k, _, data in items:
            sizes[k] = len(data)
        if len(sizes) > MAX_ITEMS:
            raise AuthError(f"W sejfie mieści się najwyżej {MAX_ITEMS} plików.", 413, "too_many")
        if sum(sizes.values()) > MAX_USER_BYTES:
            raise AuthError(f"Sejf jest pełny (limit {MAX_USER_BYTES // 1024 // 1024} MB). Usuń stare eksporty.", 413, "quota")

    def vault_list(self, uid: str) -> list[dict]:
        return self.db.all("SELECT item_key AS key, iv, data, size, updated FROM vault_items WHERE user_id = ? ORDER BY item_key", (uid,))

    def vault_put(self, uid: str, key, iv, data) -> dict:
        item = self._check_item(key, iv, data)
        self._check_quota(uid, [item])
        self.db.execute("INSERT INTO vault_items (user_id, item_key, iv, data, size, updated) VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT (user_id, item_key) DO UPDATE SET iv = excluded.iv, data = excluded.data, "
                        "size = excluded.size, updated = excluded.updated",
                        (uid, item[0], item[1], item[2], len(item[2]), _now()))
        return {"key": item[0], "size": len(item[2])}

    def vault_delete(self, uid: str, key) -> None:
        if not KEY_RE.match(str(key or "")):
            raise AuthError("Nieprawidłowy element sejfu.", 400, "bad_item")
        self.db.execute("DELETE FROM vault_items WHERE user_id = ? AND item_key = ?", (uid, str(key)))

    # ------------------------------------------------------------------ zaproszenia i konta (administrator)
    def create_invite(self, admin_id: str, note: str = "") -> dict:
        # 12 znaków z alfabetu bez mylących znaków (0/O, 1/I) ~ 60 bitów, w grupach po 4
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        raw = "".join(secrets.choice(alphabet) for _ in range(12))
        code = "-".join(raw[i:i + 4] for i in range(0, 12, 4))
        iid = secrets.token_hex(8)
        self.db.execute("INSERT INTO invites (id, code_hash, created_by, created, expires, note) VALUES (?, ?, ?, ?, ?, ?)",
                        (iid, _sha(code), admin_id, _now(), _now() + INVITE_TTL, str(note or "")[:80]))
        return {"id": iid, "code": code, "expires": _now() + INVITE_TTL}

    def list_invites(self) -> list[dict]:
        return self.db.all("SELECT i.id, i.created, i.expires, i.note, i.used_at, u.username AS used_by FROM invites i "
                           "LEFT JOIN users u ON u.id = i.used_by ORDER BY i.created DESC")

    def delete_invite(self, iid: str) -> None:
        self.db.execute("DELETE FROM invites WHERE id = ? AND used_by IS NULL", (str(iid),))

    def list_users(self) -> list[dict]:
        return self.db.all("SELECT u.id, u.username, u.is_admin, u.created, u.last_login, "
                           "(SELECT COALESCE(SUM(size), 0) FROM vault_items v WHERE v.user_id = u.id) AS vault_bytes "
                           "FROM users u ORDER BY u.created")
