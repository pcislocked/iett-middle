"""Simple async-safe in-memory TTL cache with SQLite persistence."""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = "data/cache.db"
_db_initialized = False
_db_disabled = False

# We use time.time() for persistence because time.monotonic() resets on reboot.
_store: dict[str, tuple[Any, float, float]] = {}
cache_hit_time = contextvars.ContextVar("cache_hit_time", default=None)
_DYNAMIC_PREFIXES = ("stops:arrivals:", "routes:announcements:", "traffic:")
_lock = asyncio.Lock()

# Track hit/miss stats per namespace
_hits: dict[str, int] = {}
_misses: dict[str, int] = {}

_last_fallback_log_time = 0.0
FALLBACK_LOG_INTERVAL = 60.0  # Log at most once per 60 seconds

def _log_db_fallback(operation: str) -> None:
    global _last_fallback_log_time
    now = time.time()
    if now - _last_fallback_log_time >= FALLBACK_LOG_INTERVAL:
        _last_fallback_log_time = now
        logger.warning(
            "SQLite database is disabled; cache operations are falling back to in-memory only. Operation: %s",
            operation,
        )

def _init_db() -> list[tuple[str, Any, float, float]]:
    global _db_initialized, _db_disabled
    rows_to_load = []
    if _db_disabled:
        _log_db_fallback("init")
        return rows_to_load
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires_at REAL, created_at REAL)"
            )
            # Clear expired items
            now = time.time()
            conn.execute("DELETE FROM cache WHERE expires_at <= ?", (now,))
            # Load unexpired keys into memory
            c = conn.cursor()
            c.execute("SELECT key, value, expires_at, created_at FROM cache")
            for row in c.fetchall():
                key, value_json, expires_at, created_at = row
                try:
                    value = json.loads(value_json)
                    rows_to_load.append((key, value, expires_at, created_at))
                except Exception as exc:
                    logger.warning("Skipping cache row with invalid JSON for key %r: %s", key, exc)
            conn.commit()
            _db_initialized = True
    except Exception as exc:
        logger.warning("cache.db initialization failed (read-only or permission issue?): %s", exc)
        _db_disabled = True
    return rows_to_load

async def init_cache() -> None:
    rows = await asyncio.to_thread(_init_db)
    async with _lock:
        now_time = time.time()
        now_mono = time.monotonic()
        for key, value, expires_at, created_at in rows:
            expires_at_mono = now_mono + (expires_at - now_time)
            _store[key] = (value, expires_at_mono, created_at)

def _db_set(key: str, value: Any, expires_at: float, created_at: float) -> None:
    global _db_disabled
    if _db_disabled:
        _log_db_fallback("set")
        return
    if not _db_initialized:
        _init_db()
        if _db_disabled:
            _log_db_fallback("set")
            return
    try:
        value_json = json.dumps(value)
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (key, value_json, expires_at, created_at),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("SQLite _db_set failed: %s", exc)
        _db_disabled = True

def _db_delete(key: str, created_at: float | None = None) -> None:
    global _db_disabled
    if _db_disabled:
        _log_db_fallback("delete")
        return
    if not _db_initialized:
        _init_db()
        if _db_disabled:
            _log_db_fallback("delete")
            return
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            if created_at is not None:
                conn.execute("DELETE FROM cache WHERE key = ? AND created_at = ?", (key, created_at))
            else:
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
    except Exception as exc:
        logger.warning("SQLite _db_delete failed: %s", exc)
        _db_disabled = True

def _db_delete_batch(expired_keys: list[tuple[str, float]]) -> None:
    global _db_disabled
    if _db_disabled:
        _log_db_fallback("delete_batch")
        return
    if not _db_initialized:
        _init_db()
        if _db_disabled:
            _log_db_fallback("delete_batch")
            return
    if not expired_keys:
        return
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.executemany("DELETE FROM cache WHERE key = ? AND created_at = ?", expired_keys)
            conn.commit()
    except Exception as exc:
        logger.warning("SQLite _db_delete_batch failed: %s", exc)
        _db_disabled = True
        raise exc

def _db_clear() -> None:
    global _db_disabled
    if _db_disabled:
        _log_db_fallback("clear")
        return
    if not _db_initialized:
        _init_db()
        if _db_disabled:
            _log_db_fallback("clear")
            return
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()
    except Exception as exc:
        logger.warning("SQLite _db_clear failed: %s", exc)
        _db_disabled = True

def _db_delete_namespace(namespace: str) -> None:
    global _db_disabled
    if _db_disabled:
        _log_db_fallback("delete_namespace")
        return
    if not _db_initialized:
        _init_db()
        if _db_disabled:
            _log_db_fallback("delete_namespace")
            return
    try:
        prefix = f"{namespace}:"
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("DELETE FROM cache WHERE key = ? OR key LIKE ?", (namespace, f"{prefix}%"))
            conn.commit()
    except Exception as exc:
        logger.warning("SQLite _db_delete_namespace failed: %s", exc)
        _db_disabled = True

def _set_cache_hit_time(val: float) -> None:
    container = cache_hit_time.get()
    if isinstance(container, dict):
        if container.get("hit_time") is None:
            container["hit_time"] = val
    elif container is None:
        cache_hit_time.set(val)

def _namespace(key: str) -> str:
    return key.split(":")[0]

async def cache_get(key: str) -> Any | None:
    ns = _namespace(key)
    entry = _store.get(key)
    if entry is not None:
        value, expires_at_mono, created_at = entry
        if time.monotonic() < expires_at_mono:
            _hits[ns] = _hits.get(ns, 0) + 1
            if key.startswith(_DYNAMIC_PREFIXES):
                _set_cache_hit_time(created_at)
            return value
        # Expired
        deleted_created_at = None
        async with _lock:
            # Re-check under lock in case another task updated it
            entry = _store.get(key)
            if entry is not None and time.monotonic() >= entry[1]:
                _, _, created_at = entry
                _store.pop(key, None)
                deleted_created_at = created_at
        if deleted_created_at is not None:
            await asyncio.to_thread(_db_delete, key, deleted_created_at)
    _misses[ns] = _misses.get(ns, 0) + 1
    return None

async def cache_set(key: str, value: Any, ttl: int) -> None:
    if ttl < 0:
        raise ValueError("ttl must be >= 0")
    
    now_time = time.time()
    now_mono = time.monotonic()
    expires_at_time = now_time + ttl
    expires_at_mono = now_mono + ttl
    
    async with _lock:
        _store[key] = (value, expires_at_mono, now_time)
        if key.startswith(_DYNAMIC_PREFIXES):
            _set_cache_hit_time(now_time)
            
    if not key.startswith(_DYNAMIC_PREFIXES):
        await asyncio.to_thread(_db_set, key, value, expires_at_time, now_time)

async def cache_delete(key: str) -> bool:
    existed = False
    created_at = None
    async with _lock:
        entry = _store.get(key)
        if entry is not None:
            _, expires_at_mono, created_at = entry
            existed = time.monotonic() < expires_at_mono
            _store.pop(key, None)
    if created_at is not None:
        await asyncio.to_thread(_db_delete, key, created_at)
    return existed

async def cache_invalidate_namespace(namespace: str) -> int:
    prefix = f"{namespace}:"
    removed = 0
    has_keys = False
    async with _lock:
        now_mono = time.monotonic()
        keys = [k for k in _store if k == namespace or k.startswith(prefix)]
        for k in keys:
            _, expires_at_mono, _ = _store[k]
            if now_mono < expires_at_mono:
                removed += 1
            _store.pop(k, None)
        _hits.pop(namespace, None)
        _misses.pop(namespace, None)
        has_keys = len(keys) > 0
    if has_keys:
        await asyncio.to_thread(_db_delete_namespace, namespace)
    return removed

async def cache_clear() -> int:
    async with _lock:
        removed = len(_store)
        _store.clear()
        _hits.clear()
        _misses.clear()
    await asyncio.to_thread(_db_clear)
    return removed

def get_cache_stats() -> dict[str, Any]:
    now_mono = time.monotonic()
    active = sum(1 for _, (_, exp, _) in _store.items() if now_mono < exp)
    return {
        "active_keys": active,
        "total_keys": len(_store),
        "hits": dict(_hits),
        "misses": dict(_misses),
    }

async def sweep_expired_forever() -> None:
    """Periodically iterate over the cache and evict expired items to prevent unbounded growth."""
    consecutive_failures = 0
    while True:
        if consecutive_failures > 0:
            backoff = min(600, 2 ** consecutive_failures)
            await asyncio.sleep(backoff)
        else:
            await asyncio.sleep(600)  # Sweep every 10 minutes
            
        try:
            now_mono = time.monotonic()
            expired_keys = []
            async with _lock:
                for k, v in _store.items():
                    if now_mono >= v[1]:
                        expired_keys.append((k, v[2]))
                for k, _ in expired_keys:
                    _store.pop(k, None)
            if expired_keys:
                await asyncio.to_thread(_db_delete_batch, expired_keys)
            consecutive_failures = 0
        except asyncio.CancelledError:
            break
        except Exception as exc:
            consecutive_failures += 1
            logger.error(
                "sweep_expired_forever failed (consecutive failure #%d): %s",
                consecutive_failures,
                exc,
            )
