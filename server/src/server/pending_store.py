"""Short-lived one-time server state: WebAuthn challenges and device codes.

Entries live in process memory, matching the single-worker deployment. Values
are returned by reference, so a caller may mutate an entry in place (the device
grant stamps the approving user onto a pending entry this way).
"""
from __future__ import annotations

import time
from typing import Any, Callable

# Sweeping expired entries is O(n); throttling keeps put() amortized cheap so a
# flood of unauthenticated inserts cannot pin the event loop scanning the store.
PURGE_INTERVAL_SECONDS = 1.0


class PendingStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._entries: dict[str, tuple[float, Any]] = {}
        self._last_purge = clock()

    def put(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._purge_expired()
        self._entries[key] = (self._clock() + ttl_seconds, value)

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        deadline, value = entry
        if self._clock() >= deadline:
            del self._entries[key]
            return None
        return value

    def consume(self, key: str) -> Any | None:
        value = self.get(key)
        if value is not None:
            del self._entries[key]
        return value

    def _purge_expired(self) -> None:
        now = self._clock()
        if now - self._last_purge < PURGE_INTERVAL_SECONDS:
            return
        self._last_purge = now
        expired = [key for key, (deadline, _) in self._entries.items() if now >= deadline]
        for key in expired:
            del self._entries[key]
