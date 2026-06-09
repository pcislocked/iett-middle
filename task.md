# Code Review: `feature/backend-swr-jitter`

I have thoroughly reviewed the `feature/backend-swr-jitter` branch (specifically `app/services/cache.py`) and found **4 significant bugs** (memory leaks, exception handling failures, and race conditions).

## 1. Critical Memory Leak / Hang in Background Fetch Tasks
**Issue:**
In `app/services/cache.py` -> `cache_get_or_fetch()`, the background task is created like this:
```python
asyncio.create_task(background_fetch())
return value
```
No strong reference is kept to the created task. As per official Python 3.7+ behavior, the garbage collector can (and will) silently destroy unreferenced tasks mid-execution ("Task was destroyed but it is pending!"). If this happens, the `finally` block inside `background_fetch` does not execute. Therefore, `del _inflight[key]` is skipped, leaving the future forever pending in `_inflight`. Subsequent requests experiencing a cache miss on this key will join the non-leader queue and wait forever, causing unbounded memory leaks and hanging requests.

**Fix:**
Create a module-level set to hold strong references to background tasks.
```python
_bg_tasks = set()
# ...
task = asyncio.create_task(background_fetch())
_bg_tasks.add(task)
task.add_done_callback(_bg_tasks.discard)
```

## 2. "Thundering Herd" Failure on Client Disconnect
**Issue:**
In `cache_get_or_fetch`, if multiple concurrent requests ask for a missing cache key, the first becomes the leader (`is_leader = True`) and creates `fut`. The others wait as non-leaders (`return await fut`).
If the leader client abruptly disconnects (e.g., closing the browser or connection timeout), FastAPI cancels the leader task. The leader's `finally` block runs and calls `fut.cancel()`.
This forcefully raises `asyncio.CancelledError` for **all other non-leader clients** waiting on `fut`. The framework interprets this as an unhandled error, failing their HTTP requests (500 Internal Server Error) simply because a different client disconnected.

**Fix:**
Non-leaders should check if `fut` was cancelled and gracefully retry the cache lookup instead of crashing:
```python
    if not is_leader:
        try:
            return await fut
        except asyncio.CancelledError:
            if fut.cancelled():
                # Leader disconnected and cancelled the future. Retry as new leader.
                return await cache_get_or_fetch(key, ttl, fetcher, stale_ttl, jitter)
            raise
```

## 3. Double `InvalidStateError` Crash in `background_fetch` Exception Handling
**Issue:**
In `background_fetch`, if the future `fut` happens to be cancelled (e.g., due to the leader cancellation bug above or manual intervention), executing `fut.set_result(new_value)` raises an `asyncio.InvalidStateError`.
The exception handler catches this and attempts to call `fut.set_exception(e)`, which immediately throws *another* `InvalidStateError` because the future is already cancelled.

**Fix:**
Check if the future is already done before setting results or exceptions:
```python
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
                # ...
```

## 4. Unsafe Concurrent Dictionary Mutation (Minor Race Condition)
**Issue:**
In `_cache_get_internal`, expired items are aggressively purged using `_store.pop(key, None)` **without** acquiring `_lock`. While CPython's GIL generally makes dict pops atomic, this breaks the async locking contract used throughout the rest of `cache.py`. It could cause issues if iteration structures change or if deployed on a free-threaded Python 3.13+ runtime.

**Fix:**
Let the background `sweep_forever` daemon handle expired key purging, or wrap the pop in `async with _lock:`.
