"""Tiny in-memory TTL cache (no external deps, single-process)."""
import time
from typing import Any, Optional


class TTLCache:
    """Dict-based cache with per-entry TTL and simple oldest-first eviction."""

    def __init__(self, maxsize: int = 1024):
        self.maxsize = maxsize
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        if len(self._store) >= self.maxsize and key not in self._store:
            # Evict the entry expiring soonest
            oldest = min(self._store, key=lambda k: self._store[k][0])
            self._store.pop(oldest, None)
        self._store[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        self._store.clear()


# Shared cache for social platform responses
social_cache = TTLCache()
