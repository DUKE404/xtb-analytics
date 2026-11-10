"""Magazyn sesji w pamięci procesu (bez zapisu na dysk). Telefon trzyma kopię eksportu u siebie
i sam odtwarza sesję, gdy serwer ją zgubi (np. po uśpieniu darmowej instancji)."""
from __future__ import annotations

import secrets
import threading
import time

MAX_SESSIONS = 6          # darmowa instancja ma 512 MB RAM
TTL_SECONDS = 3 * 3600


class SessionStore:
    def __init__(self, max_sessions: int = MAX_SESSIONS, ttl: float = TTL_SECONDS):
        self._data: dict[str, tuple[object, float]] = {}
        self._lock = threading.Lock()
        self.max_sessions, self.ttl = max_sessions, ttl

    def _prune(self) -> None:
        now = time.time()
        for sid in [s for s, (_, t) in self._data.items() if now - t > self.ttl]:
            del self._data[sid]
        while len(self._data) > self.max_sessions:
            oldest = min(self._data, key=lambda s: self._data[s][1])
            del self._data[oldest]

    def put(self, ctx, sid: str | None = None) -> str:
        sid = sid or secrets.token_urlsafe(18)
        with self._lock:
            self._data[sid] = (ctx, time.time())
            self._prune()
        return sid

    def get(self, sid: str):
        with self._lock:
            item = self._data.get(sid)
            if item is None or time.time() - item[1] > self.ttl:
                self._data.pop(sid, None)
                return None
            self._data[sid] = (item[0], time.time())
            return item[0]

    def drop(self, sid: str) -> None:
        with self._lock:
            self._data.pop(sid, None)

    def __len__(self) -> int:
        return len(self._data)
