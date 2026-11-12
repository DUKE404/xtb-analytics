"""Magazyn sesji analizy w pamięci procesu (bez zapisu na dysk).

Sesja należy do konta, które ją utworzyło: inne konto nie zobaczy jej nawet ze znanym identyfikatorem.
Telefon trzyma zaszyfrowane pliki w sejfie i sam odtwarza sesję, gdy serwer ją zgubi (np. po uśpieniu).
"""
from __future__ import annotations

import secrets
import threading
import time

MAX_SESSIONS = 6          # darmowa instancja ma 512 MB RAM
TTL_SECONDS = 3 * 3600


class SessionStore:
    def __init__(self, max_sessions: int = MAX_SESSIONS, ttl: float = TTL_SECONDS):
        self._data: dict[str, tuple[object, float, str | None]] = {}
        self._lock = threading.Lock()
        self.max_sessions, self.ttl = max_sessions, ttl

    def _prune(self) -> None:
        now = time.time()
        for sid in [s for s, (_, t, _o) in self._data.items() if now - t > self.ttl]:
            del self._data[sid]
        while len(self._data) > self.max_sessions:
            oldest = min(self._data, key=lambda s: self._data[s][1])
            del self._data[oldest]

    def put(self, ctx, sid: str | None = None, owner: str | None = None) -> str:
        with self._lock:
            if sid and sid in self._data and self._data[sid][2] != owner:
                sid = None                                 # cudzy identyfikator: zawsze nowa sesja
            sid = sid or secrets.token_urlsafe(18)
            self._data[sid] = (ctx, time.time(), owner)
            self._prune()
        return sid

    def get(self, sid: str, owner: str | None = None):
        with self._lock:
            item = self._data.get(sid)
            if item is None or time.time() - item[1] > self.ttl:
                self._data.pop(sid, None)
                return None
            if item[2] != owner:
                return None
            self._data[sid] = (item[0], time.time(), item[2])
            return item[0]

    def drop(self, sid: str, owner: str | None = None) -> None:
        with self._lock:
            item = self._data.get(sid)
            if item is not None and item[2] == owner:
                del self._data[sid]

    def drop_owner(self, owner: str) -> None:
        """Usuwa wszystkie sesje analizy konta (wylogowanie, usunięcie konta) - pliki znikają z pamięci serwera."""
        with self._lock:
            for sid in [s for s, (_, _t, o) in self._data.items() if o == owner]:
                del self._data[sid]

    def __len__(self) -> int:
        return len(self._data)
