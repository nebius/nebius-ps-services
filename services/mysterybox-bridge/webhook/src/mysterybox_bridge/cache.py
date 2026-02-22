from __future__ import annotations

import time
from threading import Lock


class TTLCache[K, V]:
    """Small thread-safe in-memory TTL cache."""

    def __init__(self) -> None:
        self._items: dict[K, tuple[V, float]] = {}
        self._lock = Lock()

    def get(self, key: K) -> V | None:
        now = time.time()
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            value, expires_at = item
            if now >= expires_at:
                self._items.pop(key, None)
                return None
            return value

    def set(self, key: K, value: V, ttl_seconds: int) -> None:
        expires_at = time.time() + max(ttl_seconds, 1)
        with self._lock:
            self._items[key] = (value, expires_at)
