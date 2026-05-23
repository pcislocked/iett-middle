"""Simple async-safe in-memory TTL cache."""
from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Any

_store: dict[str, tuple[Any, float, float]] = {}
cache_hit_time = contextvars.ContextVar("cache_hit_time", default=None)
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
        value, expires_at, created_at = entry
        if time.monotonic() < expires_at:
            _hits[ns] = _hits.get(ns, 0) + 1
            cache_hit_time.set(created_at)
            return value
        # Expired
        _store.pop(key, None)
    _misses[ns] = _misses.get(ns, 0) + 1
    return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    if ttl < 0:
        raise ValueError("ttl must be >= 0")
    async with _lock:
        now = time.time()
        _store[key] = (value, time.monotonic() + ttl, now)
        cache_hit_time.set(now)


async def cache_delete(key: str) -> bool:
    """Delete a single cache key.

    Returns True only when the key existed and was still unexpired.
    """
    async with _lock:
        existed = False
        entry = _store.get(key)
        if entry is not None:
            _, expires_at, _ = entry
            existed = time.monotonic() < expires_at
        _store.pop(key, None)
        return existed


async def cache_invalidate_namespace(namespace: str) -> int:
    """Delete all cache keys in a namespace and return number removed.

    A namespace is the first segment before ':' in the key.
    """
    prefix = f"{namespace}:"
    async with _lock:
        now = time.monotonic()
        keys = [k for k in _store if k == namespace or k.startswith(prefix)]
        removed = 0
        for k in keys:
            _, expires_at, _ = _store[k]
            if now < expires_at:
                removed += 1
            _store.pop(k, None)

        _hits.pop(namespace, None)
        _misses.pop(namespace, None)
        return removed


async def cache_clear() -> int:
    """Clear the full in-memory cache + stats and return removed key count."""
    async with _lock:
        removed = len(_store)
        _store.clear()
        _hits.clear()
        _misses.clear()
        return removed


def get_cache_stats() -> dict[str, Any]:
    now = time.monotonic()
    active = sum(1 for _, (_, exp, _) in _store.items() if now < exp)
    return {
        "active_keys": active,
        "total_keys": len(_store),
        "hits": dict(_hits),
        "misses": dict(_misses),
    }
