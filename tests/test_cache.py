"""Tests for app.services.cache — in-memory TTL store."""
from __future__ import annotations

import asyncio
import time

import pytest

import app.services.cache as cache_mod
from app.services.cache import (
    cache_clear,
    cache_delete,
    cache_get,
    cache_invalidate_namespace,
    cache_set,
    get_cache_stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clear() -> None:
    """Wipe state between tests."""
    cache_mod._store.clear()
    cache_mod._hits.clear()
    cache_mod._misses.clear()


# ---------------------------------------------------------------------------
# cache_get / cache_set
# ---------------------------------------------------------------------------

class TestCacheGetSet:
    def setup_method(self) -> None:
        _clear()

    def test_miss_returns_none(self) -> None:
        result = asyncio.run(cache_get("nonexistent:key"))
        assert result is None

    def test_set_then_get_returns_value(self) -> None:
        asyncio.run(cache_set("ns:key1", {"data": 42}, ttl=60))
        result = asyncio.run(cache_get("ns:key1"))
        assert result == {"data": 42}

    def test_expired_entry_returns_none(self) -> None:
        # Set with ttl=0 — expires immediately (monotonic + 0 ≤ current monotonic)
        asyncio.run(cache_set("ns:exp", "stale", ttl=0))
        # Force expiry by backdating the entry
        key = "ns:exp"
        value, _ = cache_mod._store[key]
        cache_mod._store[key] = (value, time.monotonic() - 1)
        result = asyncio.run(cache_get(key))
        assert result is None

    def test_expired_key_removed_from_store(self) -> None:
        asyncio.run(cache_set("ns:rm", "bye", ttl=60))
        key = "ns:rm"
        value, _ = cache_mod._store[key]
        cache_mod._store[key] = (value, time.monotonic() - 1)
        asyncio.run(cache_get(key))
        assert key not in cache_mod._store

    def test_overwrite_updates_value(self) -> None:
        asyncio.run(cache_set("ns:k", "first", ttl=60))
        asyncio.run(cache_set("ns:k", "second", ttl=60))
        result = asyncio.run(cache_get("ns:k"))
        assert result == "second"

    def test_different_keys_independent(self) -> None:
        asyncio.run(cache_set("ns:a", 1, ttl=60))
        asyncio.run(cache_set("ns:b", 2, ttl=60))
        assert asyncio.run(cache_get("ns:a")) == 1
        assert asyncio.run(cache_get("ns:b")) == 2

    def test_stores_list_value(self) -> None:
        asyncio.run(cache_set("ns:list", [1, 2, 3], ttl=60))
        assert asyncio.run(cache_get("ns:list")) == [1, 2, 3]

    def test_stores_none_as_falsy_does_not_confuse_miss(self) -> None:
        # Storing None is edge-case: the entry exists, but get skips None in the store check
        # (the store value is the tuple, the outer check is `if entry is not None`)
        asyncio.run(cache_set("ns:none_val", None, ttl=60))
        # entry exists in store — get returns None, indistinguishable from miss by design
        result = asyncio.run(cache_get("ns:none_val"))
        # result is None: correct — storing None is unsupported as a meaningful cached value
        assert result is None

    def test_negative_ttl_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="ttl must be >= 0"):
            asyncio.run(cache_set("ns:bad", "x", ttl=-1))


# ---------------------------------------------------------------------------
# Hit / miss tracking
# ---------------------------------------------------------------------------

class TestCacheStats:
    def setup_method(self) -> None:
        _clear()

    def test_miss_increments_counter(self) -> None:
        asyncio.run(cache_get("stops:missing"))
        stats = get_cache_stats()
        assert stats["misses"].get("stops", 0) >= 1

    def test_hit_increments_counter(self) -> None:
        asyncio.run(cache_set("stops:key", "v", ttl=60))
        asyncio.run(cache_get("stops:key"))
        stats = get_cache_stats()
        assert stats["hits"].get("stops", 0) >= 1

    def test_active_keys_counts_valid_entries(self) -> None:
        asyncio.run(cache_set("ns:x", 1, ttl=60))
        asyncio.run(cache_set("ns:y", 2, ttl=60))
        stats = get_cache_stats()
        assert stats["active_keys"] >= 2

    def test_active_keys_excludes_expired(self) -> None:
        asyncio.run(cache_set("ns:old", "gone", ttl=60))
        key = "ns:old"
        value, _ = cache_mod._store[key]
        cache_mod._store[key] = (value, time.monotonic() - 1)
        stats = get_cache_stats()
        active_before = stats["active_keys"]
        # add a fresh entry to confirm overall count is correct
        asyncio.run(cache_set("ns:fresh", "here", ttl=60))
        stats2 = get_cache_stats()
        assert stats2["active_keys"] == active_before + 1

    def test_total_keys_includes_expired(self) -> None:
        asyncio.run(cache_set("ns:exp2", "stale", ttl=60))
        key = "ns:exp2"
        value, _ = cache_mod._store[key]
        cache_mod._store[key] = (value, time.monotonic() - 1)
        stats = get_cache_stats()
        # expired entry is still in _store until next get
        assert stats["total_keys"] >= 1

    def test_namespace_is_first_segment(self) -> None:
        """Hits/misses are bucketed by the part before the first ':'."""
        asyncio.run(cache_set("routes:meta:500T", [], ttl=60))
        asyncio.run(cache_get("routes:meta:500T"))
        stats = get_cache_stats()
        assert stats["hits"].get("routes", 0) >= 1


# ---------------------------------------------------------------------------
# Invalidation helpers
# ---------------------------------------------------------------------------

class TestCacheInvalidation:
    def setup_method(self) -> None:
        _clear()

    def test_cache_delete_existing_key(self) -> None:
        asyncio.run(cache_set("ns:key", "v", ttl=60))
        removed = asyncio.run(cache_delete("ns:key"))
        assert removed is True
        assert asyncio.run(cache_get("ns:key")) is None

    def test_cache_delete_expired_key_returns_false(self) -> None:
        asyncio.run(cache_set("ns:exp", "v", ttl=60))
        value, _ = cache_mod._store["ns:exp"]
        cache_mod._store["ns:exp"] = (value, time.monotonic() - 1)

        removed = asyncio.run(cache_delete("ns:exp"))
        assert removed is False
        assert "ns:exp" not in cache_mod._store

    def test_cache_delete_missing_key(self) -> None:
        removed = asyncio.run(cache_delete("ns:missing"))
        assert removed is False

    def test_cache_invalidate_namespace_removes_only_matching_namespace(self) -> None:
        asyncio.run(cache_set("fleet:a", 1, ttl=60))
        asyncio.run(cache_set("fleet:b", 2, ttl=60))
        asyncio.run(cache_set("routes:a", 3, ttl=60))

        removed = asyncio.run(cache_invalidate_namespace("fleet"))
        assert removed == 2
        assert asyncio.run(cache_get("fleet:a")) is None
        assert asyncio.run(cache_get("fleet:b")) is None
        assert asyncio.run(cache_get("routes:a")) == 3

    def test_cache_invalidate_namespace_removed_count_excludes_expired(self) -> None:
        asyncio.run(cache_set("fleet:live", 1, ttl=60))
        asyncio.run(cache_set("fleet:expired", 2, ttl=60))
        value, _ = cache_mod._store["fleet:expired"]
        cache_mod._store["fleet:expired"] = (value, time.monotonic() - 1)

        removed = asyncio.run(cache_invalidate_namespace("fleet"))
        assert removed == 1

    def test_cache_invalidate_namespace_clears_namespace_stats(self) -> None:
        asyncio.run(cache_set("fleet:a", 1, ttl=60))
        asyncio.run(cache_get("fleet:a"))
        asyncio.run(cache_get("fleet:missing"))
        asyncio.run(cache_set("routes:a", 2, ttl=60))
        asyncio.run(cache_get("routes:a"))

        asyncio.run(cache_invalidate_namespace("fleet"))
        stats = get_cache_stats()
        assert "fleet" not in stats["hits"]
        assert "fleet" not in stats["misses"]
        assert stats["hits"].get("routes", 0) >= 1

    def test_cache_clear_removes_everything(self) -> None:
        asyncio.run(cache_set("a:1", 1, ttl=60))
        asyncio.run(cache_set("b:1", 2, ttl=60))
        asyncio.run(cache_get("a:1"))
        asyncio.run(cache_get("b:missing"))
        removed = asyncio.run(cache_clear())

        assert removed == 2
        stats = get_cache_stats()
        assert stats["total_keys"] == 0
        assert stats["active_keys"] == 0
        assert stats["hits"] == {}
        assert stats["misses"] == {}


# ---------------------------------------------------------------------------
# cache_get_or_fetch
# ---------------------------------------------------------------------------

class TestCacheGetOrFetch:
    def setup_method(self) -> None:
        _clear()

    @pytest.mark.asyncio
    async def test_single_flight_concurrency(self) -> None:
        fetch_count = 0
        
        async def slow_fetch():
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.1)
            return "data"
            
        results = await asyncio.gather(*(
            cache_mod.cache_get_or_fetch("ns:sf", 60, slow_fetch)
            for _ in range(5)
        ))
        
        assert all(r == "data" for r in results)
        assert fetch_count == 1

    @pytest.mark.asyncio
    async def test_exception_fan_out(self) -> None:
        async def fail_fetch():
            await asyncio.sleep(0.1)
            raise ValueError("fetch failed")
            
        results = await asyncio.gather(*(
            cache_mod.cache_get_or_fetch("ns:fail", 60, fail_fetch)
            for _ in range(3)
        ), return_exceptions=True)
        
        assert all(isinstance(r, ValueError) for r in results)
        assert all(str(r) == "fetch failed" for r in results)

    @pytest.mark.asyncio
    async def test_skip_cache_behavior(self) -> None:
        async def skip_fetch():
            await asyncio.sleep(0.1)
            raise cache_mod.SkipCache("fallback_data")
            
        results = await asyncio.gather(*(
            cache_mod.cache_get_or_fetch("ns:skip", 60, skip_fetch)
            for _ in range(3)
        ))
        
        assert all(r == "fallback_data" for r in results)
        assert await cache_mod.cache_get("ns:skip") is None
