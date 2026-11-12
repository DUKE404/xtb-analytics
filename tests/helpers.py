"""Wspólne pomocnicze dla testów API: aplikacja z kontem testowym w bazie w pamięci."""
from __future__ import annotations

from server.accounts import Accounts
from server.app import create_app
from server.db import Database
from server.sessions import SessionStore

TEST_SALT = "ab" * 16
TEST_KEY = "cd" * 32                  # "klucz logowania" (w aplikacji wyprowadza go telefon z hasła)


def make_accounts(setup_code: str = "", iterations: int | None = None, url: str = ":memory:") -> Accounts:
    acc = Accounts(Database(url), secret="test-secret", setup_code=setup_code)
    if iterations:
        acc.iterations = iterations
    return acc


def authed_client(provider_factory=None, store: SessionStore | None = None, accounts: Accounts | None = None,
                  username: str = "tester"):
    """Klient testowy zalogowany na konto administratora (pierwsze konto w pustej bazie)."""
    if provider_factory is None:
        from tests.test_server import synthetic_provider
        provider_factory = synthetic_provider
    accounts = accounts or make_accounts()
    app = create_app(store or SessionStore(), provider_factory=provider_factory, accounts=accounts)
    c = app.test_client()
    r = c.post("/api/auth/register", json={"username": username, "auth_key": TEST_KEY, "salt": TEST_SALT})
    assert r.status_code == 201, r.get_data(as_text=True)
    c.environ_base["HTTP_AUTHORIZATION"] = "Bearer " + r.get_json()["token"]
    c.token = r.get_json()["token"]
    return c
