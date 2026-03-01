"""Simple async-safe in-memory TTL cache."""
from __future__ import annotations

import asyncio
import time
from typing import Any

_store: dict[str, tuple[Any, float]] = {}
_lock = asyncio.Lock()

# Track hit/miss stats per namespace (first segment of key before ":")
_hits: dict[str, int] = {}
_misses: dict[str, int] = {}


def _namespace(key: str) -> str:
    return key.split(":")[0]


async def cache_get(key: str) -> Any | None:
    ns = _namespace(key)
    entry = _store.get(key)
    if entry is not None:
        value, expires_at = entry
        if time.monotonic() < expires_at:
            _hits[ns] = _hits.get(ns, 0) + 1
            return value
        # Expired
        _store.pop(key, None)
    _misses[ns] = _misses.get(ns, 0) + 1
    return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    async with _lock:
        _store[key] = (value, time.monotonic() + ttl)


def get_cache_stats() -> dict[str, Any]:
    now = time.monotonic()
    active = sum(1 for _, (_, exp) in _store.items() if now < exp)
    return {
        "active_keys": active,
        "total_keys": len(_store),
        "hits": dict(_hits),
        "misses": dict(_misses),
    }
