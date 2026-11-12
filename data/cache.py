"""Lokalny cache notowań i kursów (SQLite), żeby nie pobierać danych w kółko."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing

import pandas as pd

# CACHE_DIR: katalog na pobrane notowania. Na serwerze z trwałym dyskiem wskaż go tutaj - po restarcie nic nie
# trzeba pobierać od nowa.
DEFAULT_CACHE_PATH = os.path.join(os.environ.get("CACHE_DIR") or ".cache", "market.sqlite")


class Cache:
    def __init__(self, path: str = DEFAULT_CACHE_PATH):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.RLock()        # jedno połączenie używane z wielu wątków (równoległe pobieranie)
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS series(
                    kind TEXT NOT NULL, key TEXT NOT NULL, date TEXT NOT NULL,
                    close REAL, volume REAL, PRIMARY KEY(kind, key, date));
                CREATE TABLE IF NOT EXISTS coverage(
                    kind TEXT NOT NULL, key TEXT NOT NULL, start TEXT, end TEXT,
                    fetched_at REAL, source TEXT, PRIMARY KEY(kind, key));
                CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT, updated_at REAL);
                """
            )

    # ------------------------------------------------------------------ #
    def read(self, kind: str, key: str, start=None, end=None) -> pd.DataFrame:
        with self._lock:
            q = "SELECT date, close, volume FROM series WHERE kind=? AND key=?"
            args: list = [kind, key]
            if start is not None:
                q += " AND date>=?"
                args.append(pd.Timestamp(start).strftime("%Y-%m-%d"))
            if end is not None:
                q += " AND date<=?"
                args.append(pd.Timestamp(end).strftime("%Y-%m-%d"))
            with closing(self._conn.execute(q + " ORDER BY date", args)) as cur:
                rows = cur.fetchall()
            df = pd.DataFrame(rows, columns=["date", "close", "volume"])
            df["date"] = pd.to_datetime(df["date"])
            return df.set_index("date").astype(float)

    def write(self, kind: str, key: str, df: pd.DataFrame) -> None:
        with self._lock:
            if df is None or not len(df):
                return
            vol = df["volume"] if "volume" in df.columns else pd.Series(float("nan"), index=df.index)
            rows = [(kind, key, pd.Timestamp(d).strftime("%Y-%m-%d"), None if pd.isna(c) else float(c),
                     None if pd.isna(v) else float(v)) for d, c, v in zip(df.index, df["close"], vol)]
            with self._conn:
                self._conn.executemany("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?)", rows)

    def coverage(self, kind: str, key: str):
        with self._lock:
            with closing(self._conn.execute(
                    "SELECT start, end, fetched_at, source FROM coverage WHERE kind=? AND key=?", (kind, key))) as cur:
                row = cur.fetchone()
            if not row:
                return None
            return {"start": pd.Timestamp(row[0]), "end": pd.Timestamp(row[1]), "fetched_at": row[2], "source": row[3]}

    def set_coverage(self, kind: str, key: str, start, end, source: str = "") -> None:
        with self._lock:
            with self._conn:
                self._conn.execute("INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?)",
                                   (kind, key, pd.Timestamp(start).strftime("%Y-%m-%d"),
                                    pd.Timestamp(end).strftime("%Y-%m-%d"), time.time(), source))

    def get_kv(self, key: str, max_age_hours: float | None = None):
        with self._lock:
            with closing(self._conn.execute("SELECT value, updated_at FROM kv WHERE key=?", (key,))) as cur:
                row = cur.fetchone()
            if not row:
                return None
            if max_age_hours is not None and time.time() - row[1] > max_age_hours * 3600:
                return None
            return row[0]

    def set_kv(self, key: str, value: str) -> None:
        with self._lock:
            with self._conn:
                self._conn.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (key, value, time.time()))

    def clear(self) -> None:
        with self._lock:
            with self._conn:
                self._conn.executescript("DELETE FROM series; DELETE FROM coverage; DELETE FROM kv;")

    # ------------------------------------------------------------------ #
    def cached_fetch(self, kind: str, key: str, start, end, fetcher, max_age_hours: float = 12.0):
        """Zwraca (dane, źródło, ostrzeżenie). `fetcher(start, end)` -> (DataFrame[close, volume], źródło).

        Pobiera tylko wtedy, gdy cache nie pokrywa zakresu albo jest starszy niż `max_age_hours`
        i brakuje w nim najnowszych dni. Gdy pobranie się nie uda, a w cache coś jest - zwraca cache.
        """
        start, end = pd.Timestamp(start).normalize(), pd.Timestamp(end).normalize()
        cov = self.coverage(kind, key)
        stale = cov is not None and cov["end"] < end and (time.time() - cov["fetched_at"]) > max_age_hours * 3600
        need = cov is None or cov["start"] > start or stale
        warning = None
        if need:
            f_start = min(start, cov["start"]) if cov else start
            try:
                df, source = fetcher(f_start, end)
                self.write(kind, key, df)
                self.set_coverage(kind, key, f_start, end, source)
            except Exception as exc:  # brak sieci / brak tickera
                if cov is None:
                    raise
                warning = f"{key}: nie udało się odświeżyć danych ({exc}); używam cache do {cov['end'].date()}."
        cov = self.coverage(kind, key)
        return self.read(kind, key, start, end), (cov or {}).get("source", ""), warning
