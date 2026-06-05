"""Simple async-safe in-memory TTL cache."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Awaitable

_store: dict[str, tuple[Any, float]] = {}
_lock = asyncio.Lock()
_inflight: dict[str, asyncio.Future] = {}

# Track hit/miss stats per namespace (first segment of key before ":")
_hits: dict[str, int] = {}
_misses: dict[str, int] = {}

MAX_CACHE_SIZE = 10000
MAX_STATS_SIZE = 1000

def _namespace(key: str) -> str:
    return key.split(":")[0]


async def cache_get(key: str) -> Any | None:
    ns = _namespace(key)
    entry = _store.get(key)
    if entry is not None:
        value, expires_at = entry
        if time.monotonic() < expires_at:
            if len(_hits) < MAX_STATS_SIZE or ns in _hits:
                _hits[ns] = _hits.get(ns, 0) + 1
            return value
        # Expired
        _store.pop(key, None)
    if len(_misses) < MAX_STATS_SIZE or ns in _misses:
        _misses[ns] = _misses.get(ns, 0) + 1
    return None


async def cache_set(key: str, value: Any, ttl: int) -> None:
    if ttl < 0:
        raise ValueError("ttl must be >= 0")
    async with _lock:
        if len(_store) >= MAX_CACHE_SIZE:
            # Sweep expired
            now = time.monotonic()
            expired = [k for k, (_, exp) in _store.items() if now >= exp]
            for k in expired:
                _store.pop(k, None)
            
            # If still too large, forcefully remove some elements
            if len(_store) >= MAX_CACHE_SIZE:
                to_remove = list(_store.keys())[: MAX_CACHE_SIZE // 10]
                for k in to_remove:
                    _store.pop(k, None)
                    
        _store[key] = (value, time.monotonic() + ttl)


class SkipCache(Exception):
    """Raise from a fetcher to return a value without caching it."""
    def __init__(self, value: Any):
        self.value = value


async def cache_get_or_fetch(key: str, ttl: int, fetcher: Callable[[], Awaitable[Any]]) -> Any | None:
    """Fetch a value from cache, or execute the fetcher if missing/expired.
    
    Prevents cache stampedes by ensuring only one concurrent fetcher runs per key.
    """
    cached = await cache_get(key)
    if cached is not None:
        return cached

    # Use a lock to check/set the inflight future safely
    async with _lock:
        if key in _inflight:
            fut = _inflight[key]
            is_leader = False
        else:
            fut = asyncio.get_running_loop().create_future()
            _inflight[key] = fut
            is_leader = True

    if not is_leader:
        try:
            return await fut
        except SkipCache as e:
            return e.value
        
    try:
        value = await fetcher()
        await cache_set(key, value, ttl)
        fut.set_result(value)
        return value
    except Exception as e:
        fut.set_exception(e)
        if isinstance(e, SkipCache):
            return e.value
        raise
    finally:
        async with _lock:
            if key in _inflight and _inflight[key] is fut:
                del _inflight[key]


async def cache_delete(key: str) -> bool:
    """Delete a single cache key.

    Returns True only when the key existed and was still unexpired.
    """
    async with _lock:
        existed = False
        entry = _store.get(key)
        if entry is not None:
            _, expires_at = entry
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
            _, expires_at = _store[k]
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


async def sweep_forever(interval: int = 60) -> None:
    """Periodically clean up expired cache entries in the background."""
    while True:
        await asyncio.sleep(interval)
        try:
            async with _lock:
                now = time.monotonic()
                expired = [k for k, (_, exp) in _store.items() if now >= exp]
                for k in expired:
                    _store.pop(k, None)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("Error in sweep_forever: %s", e)


def get_cache_stats() -> dict[str, Any]:
    now = time.monotonic()
    active = sum(1 for _, (_, exp) in _store.items() if now < exp)
    return {
        "active_keys": active,
        "total_keys": len(_store),
        "hits": dict(_hits),
        "misses": dict(_misses),
    }
