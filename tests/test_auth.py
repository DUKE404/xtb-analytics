"""Konta, logowanie, sejf i izolacja użytkowników. Testy idą na SQLite i - gdy ustawiono TEST_DATABASE_URL -
także na prawdziwym Postgresie (ta sama składnia SQL, którą wykona Neon)."""
import os
import time

import pytest

from server import accounts as A
from server.accounts import Accounts, AuthError
from server.app import create_app
from server.db import Database
from server.sessions import SessionStore
from tests.helpers import TEST_KEY, TEST_SALT, authed_client, make_accounts
from tests.test_server import strict_json, synthetic_provider

PG = os.environ.get("TEST_DATABASE_URL", "")
K1, K2, K3 = "11" * 32, "22" * 32, "33" * 32
SALT = "0f" * 16
IV = "AAAAAAAAAAAAAAAA"


def _fresh_pg():
    import psycopg
    with psycopg.connect(PG, autocommit=True) as c:
        for t in ("vault_items", "auth_sessions", "invites", "users"):
            c.execute(f"DROP TABLE IF EXISTS {t}")
    return PG


@pytest.fixture(params=["sqlite"] + (["postgres"] if PG else []))
def acc(request, tmp_path):
    url = str(tmp_path / "acc.sqlite") if request.param == "sqlite" else _fresh_pg()
    a = Accounts(Database(url), secret="s", setup_code="KOD-STARTOWY")
    yield a
    a.db.close()


# --------------------------------------------------------------------------- rejestracja i pierwsze konto

def test_first_account_needs_setup_code_and_becomes_admin(acc):
    assert acc.status()["has_users"] is False and acc.status()["setup_code_required"] is True
    with pytest.raises(AuthError) as e:
        acc.register("ania", K1, SALT, "zly-kod")
    assert e.value.code == "bad_setup_code"
    out = acc.register("Ania", K1, SALT, "KOD-STARTOWY")
    assert out["user"]["username"] == "ania" and out["user"]["is_admin"] is True and len(out["token"]) >= 40
    assert acc.status()["has_users"] is True


def test_next_accounts_need_one_time_invite(acc):
    admin = acc.register("ania", K1, SALT, "KOD-STARTOWY")["user"]
    with pytest.raises(AuthError) as e:
        acc.register("bartek", K2, SALT, "KOD-STARTOWY")      # kod konfiguracji działa tylko dla pierwszego konta
    assert e.value.code == "bad_invite"
    inv = acc.create_invite(admin["id"], "dla Bartka")
    assert len(inv["code"]) == 14 and inv["code"].count("-") == 2
    b = acc.register("bartek", K2, SALT, inv["code"].lower())["user"]   # wielkość liter kodu bez znaczenia
    assert b["is_admin"] is False
    with pytest.raises(AuthError) as e:
        acc.register("celina", K3, SALT, inv["code"])         # kod jednorazowy
    assert e.value.code == "bad_invite"
    listed = acc.list_invites()
    assert listed[0]["used_by"] == "bartek" and "code" not in listed[0] and "code_hash" not in listed[0]


def test_expired_invite_and_bad_input(acc):
    admin = acc.register("ania", K1, SALT, "KOD-STARTOWY")["user"]
    inv = acc.create_invite(admin["id"])
    acc.db.execute("UPDATE invites SET expires = ? WHERE id = ?", (int(time.time()) - 1, inv["id"]))
    with pytest.raises(AuthError):
        acc.register("bartek", K2, SALT, inv["code"])
    for name in ("ab", "a" * 40, "zła nazwa", "Ania!"):
        with pytest.raises(AuthError) as e:
            acc.register(name, K2, SALT, "x")
        assert e.value.code == "bad_username"
    with pytest.raises(AuthError):
        acc.register("bartek", "to-nie-klucz", SALT, "x")
    inv2 = acc.create_invite(admin["id"])
    with pytest.raises(AuthError) as e:
        acc.register("ania", K2, SALT, inv2["code"])
    assert e.value.code == "username_taken"


# --------------------------------------------------------------------------- logowanie, blokady, tokeny

def test_login_and_token_lifecycle(acc):
    acc.register("ania", K1, SALT, "KOD-STARTOWY")
    out = acc.login("ANIA ", K1)
    u = acc.authenticate(out["token"])
    assert u["username"] == "ania" and u["is_admin"]
    assert acc.authenticate("x" * 43) is None and acc.authenticate("") is None
    acc.logout(u["token_hash"])
    assert acc.authenticate(out["token"]) is None
    # token wygasły
    t = acc.login("ania", K1)["token"]
    acc.db.execute("UPDATE auth_sessions SET expires = ?", (int(time.time()) - 1,))
    assert acc.authenticate(t) is None


def test_tokens_are_stored_only_as_hashes(acc):
    t = acc.register("ania", K1, SALT, "KOD-STARTOWY")["token"]
    rows = acc.db.all("SELECT * FROM auth_sessions")
    assert rows and all(t not in str(r.values()) for r in rows)
    users = acc.db.all("SELECT * FROM users")
    assert K1 not in str(users[0].values())                  # w bazie tylko scrypt(klucz)


def test_wrong_password_locks_account_with_growing_delay(acc):
    acc.register("ania", K1, SALT, "KOD-STARTOWY")
    for _ in range(A.LOCK_AFTER - 1):
        with pytest.raises(AuthError) as e:
            acc.login("ania", K2)
        assert e.value.code == "bad_credentials"
    with pytest.raises(AuthError):
        acc.login("ania", K2)                                # piąta próba zakłada blokadę
    with pytest.raises(AuthError) as e:
        acc.login("ania", K1)                                # nawet dobre hasło czeka
    assert e.value.code == "locked" and e.value.status == 429
    acc.db.execute("UPDATE users SET locked_until = 0")
    assert acc.login("ania", K1)["token"]
    assert acc.db.one("SELECT failed FROM users")["failed"] == 0


def test_unknown_user_looks_like_wrong_password(acc):
    acc.register("ania", K1, SALT, "KOD-STARTOWY")
    with pytest.raises(AuthError) as e:
        acc.login("nikt", K1)
    assert e.value.code == "bad_credentials"
    # sól nieistniejącego konta jest stała i wygląda jak prawdziwa
    p1, p2 = acc.params("nikt"), acc.params("nikt")
    assert p1 == p2 and len(p1["salt"]) == 32 and p1["salt"] != acc.params("ania")["salt"]
    assert acc.params("ania")["salt"] == SALT


def test_ip_rate_limit(acc):
    acc.register("ania", K1, SALT, "KOD-STARTOWY")
    for i in range(A.IP_MAX):
        with pytest.raises(AuthError):
            acc.login(f"nikt{i}", K2, ip="1.2.3.4")
    with pytest.raises(AuthError) as e:
        acc.login("ania", K1, ip="1.2.3.4")
    assert e.value.code == "rate_limited"
    assert acc.login("ania", K1, ip="5.6.7.8")["token"]


# --------------------------------------------------------------------------- sejf

def test_vault_roundtrip_quota_and_isolation(acc):
    admin = acc.register("ania", K1, SALT, "KOD-STARTOWY")["user"]
    bob = acc.register("bartek", K2, SALT, acc.create_invite(admin["id"])["code"])["user"]
    acc.vault_put(admin["id"], "f1", IV, "QUJD")
    acc.vault_put(admin["id"], "f1", IV, "QUJDRA==")             # nadpisanie
    acc.vault_put(admin["id"], "prefs", IV, "eA==")
    assert [i["key"] for i in acc.vault_list(admin["id"])] == ["f1", "prefs"]
    assert acc.vault_list(admin["id"])[0]["data"] == "QUJDRA=="
    assert acc.vault_list(bob["id"]) == []                        # cudzego sejfu nie widać
    acc.vault_delete(bob["id"], "f1")                             # ani nie da się z niego usunąć
    assert len(acc.vault_list(admin["id"])) == 2
    for bad in (("../x", IV, "QQ=="), ("k", "short", "QQ=="), ("k", IV, "<script>"), ("k", IV, "")):
        with pytest.raises(AuthError):
            acc.vault_put(admin["id"], *bad)
    old = A.MAX_USER_BYTES
    A.MAX_USER_BYTES = 20
    try:
        with pytest.raises(AuthError) as e:
            acc.vault_put(admin["id"], "big", IV, "QUJD" * 4)
        assert e.value.code == "quota"
    finally:
        A.MAX_USER_BYTES = old


def test_change_password_reencrypts_vault_atomically(acc):
    out = acc.register("ania", K1, SALT, "KOD-STARTOWY")
    uid, keep = out["user"]["id"], acc.authenticate(out["token"])["token_hash"]
    other = acc.login("ania", K1)["token"]                        # drugie urządzenie
    acc.vault_put(uid, "f1", IV, "c3RhcmU=")
    with pytest.raises(AuthError) as e:                           # telefon nie przesłał całego sejfu
        acc.change_password(uid, K1, K2, "1e" * 16, [], keep)
    assert e.value.code == "vault_changed"
    with pytest.raises(AuthError):
        acc.change_password(uid, K3, K2, "1e" * 16, [{"key": "f1", "iv": IV, "data": "bm93ZQ=="}], keep)
    acc.change_password(uid, K1, K2, "1e" * 16, [{"key": "f1", "iv": IV, "data": "bm93ZQ=="}], keep)
    assert acc.vault_list(uid)[0]["data"] == "bm93ZQ==" and acc.params("ania")["salt"] == "1e" * 16
    assert acc.authenticate(out["token"]) is not None and acc.authenticate(other) is None   # inne urządzenia wylogowane
    with pytest.raises(AuthError):
        acc.login("ania", K1)
    assert acc.login("ania", K2)["token"]


def test_delete_account_and_admin_rules(acc):
    admin = acc.register("ania", K1, SALT, "KOD-STARTOWY")["user"]
    bob = acc.register("bartek", K2, SALT, acc.create_invite(admin["id"])["code"])["user"]
    acc.vault_put(bob["id"], "f1", IV, "QQ==")
    with pytest.raises(AuthError) as e:
        acc.delete_account(admin["id"], K1)
    assert e.value.code == "admin_has_users"
    with pytest.raises(AuthError):
        acc.delete_account(bob["id"], K1)                         # cudze hasło
    acc.delete_account(bob["id"], K2)
    assert acc.vault_list(bob["id"]) == [] and [u["username"] for u in acc.list_users()] == ["ania"]
    assert acc.db.one("SELECT COUNT(*) AS n FROM auth_sessions WHERE user_id = ?", (bob["id"],))["n"] == 0
    acc.delete_account(admin["id"], K1)
    assert not acc.has_users()


def test_database_survives_reconnect(tmp_path):
    path = str(tmp_path / "x.sqlite")
    a = Accounts(Database(path), secret="s", setup_code="")
    a.register("ania", K1, SALT)
    a.db.close()
    b = Accounts(Database(path), secret="s", setup_code="")
    assert b.login("ania", K1)["token"] and b.has_users()


@pytest.mark.skipif(not PG, reason="brak TEST_DATABASE_URL")
def test_postgres_reconnects_after_dropped_connection():
    a = Accounts(Database(_fresh_pg()), secret="s", setup_code="")
    a.register("ania", K1, SALT)
    a.db._conn.close()                                            # Neon zamyka bezczynne połączenia
    assert a.login("ania", K1)["token"] and a.db.label.startswith("Postgres (")


# --------------------------------------------------------------------------- API

def _app(**kw):
    acc = make_accounts(setup_code="KOD")
    return create_app(SessionStore(), provider_factory=synthetic_provider, accounts=acc).test_client(), acc


def _register(c, name, key, code):
    r = c.post("/api/auth/register", json={"username": name, "auth_key": key, "salt": SALT, "invite": code})
    assert r.status_code == 201, r.get_data(as_text=True)
    return {"Authorization": "Bearer " + r.get_json()["token"]}


def test_api_requires_login_everywhere_except_public_endpoints():
    c, _ = _app()
    for path in ("/api/health", "/api/auth/status", "/api/auth/params?u=x"):
        assert c.get(path).status_code == 200, path
    assert c.get("/").status_code == 200                           # powłoka aplikacji jest publiczna
    for method, path in (("post", "/api/session"), ("get", "/api/vault"), ("get", "/api/auth/me"),
                         ("get", "/api/admin/invites"), ("put", "/api/vault/x"), ("get", "/api/session/abc/screen/overview")):
        r = getattr(c, method)(path)
        assert r.status_code == 401 and r.get_json()["code"] == "auth_required", path
    r = c.get("/api/vault", headers={"Authorization": "Bearer zly-token"})
    assert r.status_code == 401
    assert c.get("/api/auth/status").get_json()["has_users"] is False


def test_api_users_cannot_see_each_others_sessions_or_vaults():
    c, _ = _app()
    ha = _register(c, "ania", K1, "KOD")
    code = strict_json(c.post("/api/admin/invites", json={"note": "Bartek"}, headers=ha))["code"]
    hb = _register(c, "bartek", K2, code)
    assert c.get("/api/admin/invites", headers=hb).status_code == 403          # nie-administrator
    sid = strict_json(c.post("/api/session", data={"demo": "1"}, headers=ha))["sid"]
    assert c.get(f"/api/session/{sid}/screen/overview", headers=ha).status_code == 200
    r = c.get(f"/api/session/{sid}/screen/overview", headers=hb)
    assert r.status_code == 404 and r.get_json()["code"] == "session_expired"   # cudza sesja wygląda jak nieistniejąca
    c.delete(f"/api/session/{sid}", headers=hb)
    assert c.get(f"/api/session/{sid}", headers=ha).status_code == 200          # i nie da się jej usunąć
    assert c.put("/api/vault/f_1", json={"iv": IV, "data": "QUJD"}, headers=ha).status_code == 200
    assert strict_json(c.get("/api/vault", headers=hb))["items"] == []
    assert strict_json(c.get("/api/vault", headers=ha))["items"][0]["data"] == "QUJD"
    me = strict_json(c.get("/api/auth/me", headers=ha))
    assert me["username"] == "ania" and me["vault_items"] == 1 and me["storage"].startswith("SQLite")
    # administrator usuwa konto Bartka: jego token przestaje działać
    bid = next(u["id"] for u in strict_json(c.get("/api/admin/invites", headers=ha))["users"] if u["username"] == "bartek")
    assert c.delete(f"/api/admin/users/{bid}", headers=ha).status_code == 200
    assert c.get("/api/vault", headers=hb).status_code == 401


def test_api_logout_ends_token_and_analysis_sessions():
    c, _ = _app()
    h = _register(c, "ania", K1, "KOD")
    sid = strict_json(c.post("/api/session", data={"demo": "1"}, headers=h))["sid"]
    h2 = {"Authorization": "Bearer " + strict_json(c.post("/api/auth/login", json={"username": "ania", "auth_key": K1}))["token"]}
    assert strict_json(c.post("/api/auth/logout-others", headers=h2))["ended"] == 1
    assert c.get("/api/vault", headers=h).status_code == 401 and c.get("/api/vault", headers=h2).status_code == 200
    c.post("/api/auth/logout", json={}, headers=h2)
    assert c.get("/api/vault", headers=h2).status_code == 401
    h3 = {"Authorization": "Bearer " + strict_json(c.post("/api/auth/login", json={"username": "ania", "auth_key": K1}))["token"]}
    assert c.get(f"/api/session/{sid}", headers=h3).status_code == 404          # pliki zniknęły z pamięci serwera


def test_security_headers():
    c, _ = _app()
    r = c.get("/")
    csp = r.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in r.headers
    r = c.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["Strict-Transport-Security"].startswith("max-age=") and r.headers["Cache-Control"] == "no-store"


def test_authed_helper_client_works():
    c = authed_client()
    assert strict_json(c.get("/api/auth/me"))["username"] == "tester"
    assert TEST_KEY and TEST_SALT
