"""Simple async-safe in-memory TTL cache with SQLite persistence."""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import sqlite3
import time
from typing import Any

DB_PATH = "data/cache.db"
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# We use time.time() for persistence because time.monotonic() resets on reboot.
_store: dict[str, tuple[Any, float, float]] = {}
cache_hit_time = contextvars.ContextVar("cache_hit_time", default=None)
_DYNAMIC_PREFIXES = ("stops:arrivals:", "routes:announcements:", "traffic:")
_lock = asyncio.Lock()

# Track hit/miss stats per namespace
_hits: dict[str, int] = {}
_misses: dict[str, int] = {}

def _init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires_at REAL, created_at REAL)"
        )
        # Load unexpired keys into memory
        now = time.time()
        c = conn.cursor()
        c.execute("SELECT key, value, expires_at, created_at FROM cache")
        for row in c.fetchall():
            key, value_json, expires_at, created_at = row
            if now < expires_at:
                try:
                    value = json.loads(value_json)
                    _store[key] = (value, expires_at, created_at)
                except Exception:
                    pass
        conn.commit()

_init_db()

def _db_set(key: str, value: Any, expires_at: float, created_at: float) -> None:
    try:
        value_json = json.dumps(value)
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache (key, value, expires_at, created_at) VALUES (?, ?, ?, ?)",
                (key, value_json, expires_at, created_at),
            )
            conn.commit()
    except Exception:
        pass  # JSON serialization might fail for some types, though our caches are mostly dicts/lists.

def _db_delete(key: str) -> None:
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            conn.commit()
    except Exception:
        pass

def _db_clear() -> None:
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()
    except Exception:
        pass

def _db_delete_namespace(namespace: str) -> None:
    try:
        prefix = f"{namespace}:"
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("DELETE FROM cache WHERE key = ? OR key LIKE ?", (namespace, f"{prefix}%"))
            conn.commit()
    except Exception:
        pass

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
        value, expires_at, created_at = entry
        if time.time() < expires_at:
            _hits[ns] = _hits.get(ns, 0) + 1
            if key.startswith(_DYNAMIC_PREFIXES):
                _set_cache_hit_time(created_at)
            return value
        # Expired
        _store.pop(key, None)
        await asyncio.to_thread(_db_delete, key)
    _misses[ns] = _misses.get(ns, 0) + 1
    return None

async def cache_set(key: str, value: Any, ttl: int) -> None:
    if ttl < 0:
        raise ValueError("ttl must be >= 0")
    async with _lock:
        now = time.time()
        expires_at = now + ttl
        _store[key] = (value, expires_at, now)
        if key.startswith(_DYNAMIC_PREFIXES):
            _set_cache_hit_time(now)
        await asyncio.to_thread(_db_set, key, value, expires_at, now)

async def cache_delete(key: str) -> bool:
    async with _lock:
        existed = False
        entry = _store.get(key)
        if entry is not None:
            _, expires_at, _ = entry
            existed = time.time() < expires_at
        _store.pop(key, None)
        await asyncio.to_thread(_db_delete, key)
        return existed

async def cache_invalidate_namespace(namespace: str) -> int:
    prefix = f"{namespace}:"
    async with _lock:
        now = time.time()
        keys = [k for k in _store if k == namespace or k.startswith(prefix)]
        removed = 0
        for k in keys:
            _, expires_at, _ = _store[k]
            if now < expires_at:
                removed += 1
            _store.pop(k, None)
        await asyncio.to_thread(_db_delete_namespace, namespace)
        _hits.pop(namespace, None)
        _misses.pop(namespace, None)
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
    now = time.time()
    active = sum(1 for _, (_, exp, _) in _store.items() if now < exp)
    return {
        "active_keys": active,
        "total_keys": len(_store),
        "hits": dict(_hits),
        "misses": dict(_misses),
    }
