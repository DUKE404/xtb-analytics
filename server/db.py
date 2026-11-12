"""Baza kont i zaszyfrowanych portfeli: Postgres (np. Neon) przez DATABASE_URL, a bez niego plik SQLite.

SQL jest pisany raz, w składni wspólnej dla obu baz (znaczniki `?` zamieniane na `%s` dla Postgresa,
INSERT ... ON CONFLICT, liczby całkowite zamiast dat). Jedno połączenie na proces, chronione blokadą -
przy kilku użytkownikach to wystarcza, a darmowy Neon i tak usypia bazę po kilku minutach bezczynności.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        kdf_salt TEXT NOT NULL,          -- sól do wyprowadzenia kluczy w telefonie (nie jest tajna)
        verifier TEXT NOT NULL,          -- scrypt(klucz logowania); samego hasła serwer nigdy nie widzi
        verifier_salt TEXT NOT NULL,
        is_admin INTEGER NOT NULL DEFAULT 0,
        created INTEGER NOT NULL,
        failed INTEGER NOT NULL DEFAULT 0,
        locked_until INTEGER NOT NULL DEFAULT 0,
        last_login INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS auth_sessions (
        token_hash TEXT PRIMARY KEY,     -- sha256 tokenu; sam token zna tylko urządzenie
        user_id TEXT NOT NULL,
        created INTEGER NOT NULL,
        expires INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,
        agent TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions (user_id)",
    """CREATE TABLE IF NOT EXISTS vault_items (
        user_id TEXT NOT NULL,
        item_key TEXT NOT NULL,          -- losowy identyfikator; nazwa pliku jest w zaszyfrowanej treści
        iv TEXT NOT NULL,
        data TEXT NOT NULL,              -- szyfrogram AES-GCM w base64
        size INTEGER NOT NULL,
        updated INTEGER NOT NULL,
        PRIMARY KEY (user_id, item_key)
    )""",
    """CREATE TABLE IF NOT EXISTS invites (
        id TEXT PRIMARY KEY,
        code_hash TEXT NOT NULL UNIQUE,
        created_by TEXT NOT NULL,
        created INTEGER NOT NULL,
        expires INTEGER NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        used_by TEXT,
        used_at INTEGER
    )""",
]


def _is_postgres(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


class Database:
    """Cienka warstwa nad sqlite3 / psycopg: execute, all, one, transakcja."""

    def __init__(self, url: str | None = None):
        url = (url if url is not None else os.environ.get("DATABASE_URL", "")).strip()
        self.url = url
        self.kind = "postgres" if _is_postgres(url) else "sqlite"
        self._lock = threading.RLock()
        self._conn = None
        self._depth = 0
        if self.kind == "sqlite":
            path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else (url or os.path.join(
                os.environ.get("CACHE_DIR") or ".cache", "accounts.sqlite"))
            self.path = path
            if path != ":memory:":
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with self.transaction() as tx:
            for stmt in SCHEMA:
                tx.execute(stmt)

    # ------------------------------------------------------------------ opis dla użytkownika
    @property
    def persistent(self) -> bool:
        """Czy dane przetrwają restart serwera. Plik SQLite na darmowym Render znika przy każdym uśpieniu."""
        if self.kind == "postgres":
            return True
        return self.path != ":memory:" and os.environ.get("RENDER", "") == ""

    @property
    def label(self) -> str:
        if self.kind == "postgres":
            host = self.url.split("@")[-1].split("/")[0].split(":")[0]
            return f"Postgres ({host})"
        return "SQLite (plik lokalny)" if self.path != ":memory:" else "SQLite (pamięć)"

    # ------------------------------------------------------------------ połączenie
    def _connect(self):
        if self.kind == "postgres":
            import psycopg
            from psycopg.rows import dict_row
            return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=15)
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL" if self.path != ":memory:" else "PRAGMA journal_mode=MEMORY")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _sql(self, sql: str) -> str:
        # SQL w tym projekcie nie zawiera znaków "?" ani "%" poza znacznikami parametrów (psycopg traktuje "%" specjalnie)
        if self.kind == "postgres":
            assert "%" not in sql, "w SQL dla Postgresa nie używaj znaku % (LIKE) - psycopg bierze go za parametr"
            return sql.replace("?", "%s")
        return sql

    def _alive(self) -> bool:
        if self._conn is None:
            return False
        if self.kind == "postgres":
            return not self._conn.closed and not self._conn.broken
        return True

    @contextmanager
    def transaction(self):
        """Wszystko w środku albo nic. Zagnieżdżone wywołania należą do zewnętrznej transakcji."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                for attempt in range(2):          # Neon zamyka bezczynne połączenia - jedna próba ponownego połączenia
                    try:
                        if not self._alive():
                            self._conn = self._connect()
                        if self.kind == "sqlite":
                            self._conn.execute("BEGIN IMMEDIATE")
                        break
                    except Exception:
                        self._conn = None
                        if attempt:
                            raise
                        time.sleep(0.3)
            self._depth += 1
            try:
                yield self
                if outer:
                    self._conn.commit() if self.kind == "postgres" else self._conn.execute("COMMIT")
            except BaseException:
                if outer and self._conn is not None:
                    try:
                        self._conn.rollback() if self.kind == "postgres" else self._conn.execute("ROLLBACK")
                    except Exception:
                        self._conn = None
                raise
            finally:
                self._depth -= 1

    # ------------------------------------------------------------------ zapytania
    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.transaction():
            cur = self._conn.execute(self._sql(sql), params)
            return cur.rowcount if cur.rowcount is not None else 0

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.transaction():
            rows = self._conn.execute(self._sql(sql), params).fetchall()
        return [dict(r) for r in rows]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.all(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
