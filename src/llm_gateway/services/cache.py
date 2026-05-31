from __future__ import annotations

import time
from typing import Any

_CACHE_MISS = object()


class TTLCache:
    def __init__(self, ttl: float = 30.0, max_size: int = 4096):
        self._ttl = ttl
        self._max_size = max_size
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry[0] > self._ttl:
            del self._store[key]
            return None
        value = entry[1]
        if value is _CACHE_MISS:
            return _CACHE_MISS
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)
        if len(self._store) > self._max_size:
            self._evict()

    def invalidate(self, prefix: str = "") -> None:
        if not prefix:
            self._store.clear()
            return
        keys_to_delete = [k for k in self._store if k.startswith(prefix)]
        for k in keys_to_delete:
            del self._store[k]

    def _evict(self) -> None:
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]
        if len(self._store) > self._max_size:
            sorted_keys = sorted(self._store, key=lambda k: self._store[k][0])
            for k in sorted_keys[: len(sorted_keys) // 2]:
                del self._store[k]


auth_cache = TTLCache(ttl=30.0)
route_cache = TTLCache(ttl=30.0)
policy_cache = TTLCache(ttl=30.0)
