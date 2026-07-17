"""Simple async-safe in-memory TTL cache."""

from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Any, Awaitable, Callable

from app.utils.lock import LazyLock

cache_hit_time = contextvars.ContextVar("cache_hit_time", default=None)
_DYNAMIC_PREFIXES = ("stops:arrivals", "routes:announcements", "traffic")


def _set_cache_hit_time(val: float) -> None:
    container = cache_hit_time.get()
    if isinstance(container, dict):
        if container.get("hit_time") is None:
            container["hit_time"] = val
    elif container is None:
        cache_hit_time.set(val)


_store: dict[str, tuple[Any, float, float, float]] = {}
_lock = LazyLock()
_inflight: dict[str, asyncio.Future] = {}

# Track hit/miss stats per namespace (first segment of key before ":")
_hits: dict[str, int] = {}
_misses: dict[str, int] = {}

MAX_CACHE_SIZE = 10000
MAX_STATS_SIZE = 1000


def _namespace(key: str) -> str:
    return key.split(":")[0]


async def _cache_get_internal(key: str) -> tuple[Any, bool] | None:
    ns = _namespace(key)
    async with _lock:
        entry = _store.get(key)
        if entry is not None:
            value, fresh_exp, stale_exp, created_at = entry
            now = time.monotonic()
            if now < fresh_exp:
                if len(_hits) < MAX_STATS_SIZE or ns in _hits:
                    _hits[ns] = _hits.get(ns, 0) + 1
                if key.startswith(_DYNAMIC_PREFIXES):
                    _set_cache_hit_time(created_at)
                return (value, True)
            elif now < stale_exp:
                if len(_hits) < MAX_STATS_SIZE or ns in _hits:
                    _hits[ns] = _hits.get(ns, 0) + 1
                if key.startswith(_DYNAMIC_PREFIXES):
                    _set_cache_hit_time(created_at)
                return (value, False)
            # Expired
            _store.pop(key, None)
        if len(_misses) < MAX_STATS_SIZE or ns in _misses:
            _misses[ns] = _misses.get(ns, 0) + 1
        return None


async def cache_get(key: str) -> Any | None:
    result = await _cache_get_internal(key)
    if result is not None:
        value, is_fresh = result
        if is_fresh:
            return value
    return None


async def cache_set(
    key: str, value: Any, ttl: int, stale_ttl: int = 0, jitter: bool = False
) -> None:
    if ttl < 0 or stale_ttl < 0:
        raise ValueError("ttl must be >= 0")

    actual_ttl = float(ttl)
    actual_stale = float(stale_ttl)
    if jitter:
        import random

        factor = random.uniform(0.85, 1.15)
        actual_ttl = actual_ttl * factor
        actual_stale = actual_stale * factor

    async with _lock:
        if len(_store) >= MAX_CACHE_SIZE:
            # Sweep expired
            now = time.monotonic()
            expired = [k for k, (_, _, s_exp, _) in _store.items() if now >= s_exp]
            for k in expired:
                _store.pop(k, None)

            # If still too large, forcefully remove some elements in O(N) instead of O(N log N)
            if len(_store) >= MAX_CACHE_SIZE:
                import itertools

                to_remove = list(itertools.islice(_store.keys(), MAX_CACHE_SIZE // 10))
                for k in to_remove:
                    _store.pop(k, None)

        now = time.monotonic()
        now_time = time.time()
        _store.pop(key, None)
        _store[key] = (
            value,
            now + actual_ttl,
            now + actual_ttl + actual_stale,
            now_time,
        )
        if key.startswith(_DYNAMIC_PREFIXES):
            _set_cache_hit_time(now_time)


class SkipCache(Exception):
    """Raise from a fetcher to return a value without caching it."""

    def __init__(self, value: Any):
        self.value = value


_bg_tasks: set[asyncio.Task] = set()


async def cache_get_or_fetch(
    key: str,
    ttl: int,
    fetcher: Callable[[], Awaitable[Any]],
    stale_ttl: int = 0,
    jitter: bool = False,
) -> Any | None:
    """Fetch a value from cache, or execute the fetcher if missing/expired.

    Prevents cache stampedes by ensuring only one concurrent fetcher runs per key.
    If data is stale but within stale_ttl, returns stale data and triggers a background fetch.
    """
    cached = await _cache_get_internal(key)
    if cached is not None:
        value, is_fresh = cached
        if is_fresh:
            return value

        # It's stale. We should return value immediately, but kick off background fetch
        async with _lock:
            if key in _inflight:
                # Already fetching in background
                return value
            fut = asyncio.get_running_loop().create_future()
            _inflight[key] = fut

        async def background_fetch() -> None:
            try:
                new_value = await fetcher()
                await cache_set(key, new_value, ttl, stale_ttl, jitter)
                if not fut.done():
                    fut.set_result(new_value)
            except SkipCache as e:
                if not fut.done():
                    fut.set_result(e.value)
            except Exception as e:
                if not fut.done():
                    fut.set_exception(e)
            finally:
                async with _lock:
                    if key in _inflight and _inflight[key] is fut:
                        if not fut.done():
                            fut.cancel()
                        del _inflight[key]

        task = asyncio.create_task(background_fetch())
        _bg_tasks.add(task)
        task.add_done_callback(_bg_tasks.discard)
        return value

    # Normal missing fetch
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
            return await asyncio.shield(fut)
        except asyncio.CancelledError:
            # If the leader cancelled it (e.g. client disconnect), try again!
            if fut.cancelled():
                return await cache_get_or_fetch(key, ttl, fetcher, stale_ttl, jitter)
            raise

    try:
        val = await fetcher()
        await cache_set(key, val, ttl, stale_ttl, jitter)
        if not fut.done():
            fut.set_result(val)
        return val
    except SkipCache as e:
        if not fut.done():
            fut.set_result(e.value)
        return e.value
    except Exception as e:
        if not fut.done():
            fut.set_exception(e)
        raise
    finally:
        async with _lock:
            if key in _inflight and _inflight[key] is fut:
                if not fut.done():
                    fut.cancel()
                del _inflight[key]


async def cache_delete(key: str) -> bool:
    """Delete a single cache key.

    Returns True only when the key existed and was still unexpired.
    """
    async with _lock:
        existed = False
        entry = _store.get(key)
        if entry is not None:
            _, _, stale_exp, _ = entry
            existed = time.monotonic() < stale_exp
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
            _, _, stale_exp, _ = _store[k]
            if now < stale_exp:
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
                expired = [k for k, (_, _, s_exp, _) in _store.items() if now >= s_exp]
                for k in expired:
                    _store.pop(k, None)
        except Exception:
            import logging

            logging.getLogger(__name__).exception("Error in sweep_forever")


def get_cache_stats() -> dict[str, Any]:
    now = time.monotonic()
    active = sum(1 for _, (_, _, s_exp, _) in _store.items() if now < s_exp)
    return {
        "active_keys": active,
        "total_keys": len(_store),
        "hits": dict(_hits),
        "misses": dict(_misses),
    }
