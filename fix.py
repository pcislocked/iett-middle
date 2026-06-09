with open('app/services/cache.py', 'r', encoding='utf-8') as f: content = f.read()

# Fix 4: _cache_get_internal popping without lock
replacement1 = '''async def _cache_get_internal(key: str) -> tuple[Any, bool] | None:
    ns = _namespace(key)
    async with _lock:
        entry = _store.get(key)
        if entry is not None:
            value, fresh_exp, stale_exp = entry
            now = time.monotonic()
            if now < fresh_exp:
                if len(_hits) < MAX_STATS_SIZE or ns in _hits:
                    _hits[ns] = _hits.get(ns, 0) + 1
                return (value, True)
            elif now < stale_exp:
                if len(_hits) < MAX_STATS_SIZE or ns in _hits:
                    _hits[ns] = _hits.get(ns, 0) + 1
                return (value, False)
            # Expired
            _store.pop(key, None)
        if len(_misses) < MAX_STATS_SIZE or ns in _misses:
            _misses[ns] = _misses.get(ns, 0) + 1
        return None'''

content = content.replace('''async def _cache_get_internal(key: str) -> tuple[Any, bool] | None:
    ns = _namespace(key)
    entry = _store.get(key)
    if entry is not None:
        value, fresh_exp, stale_exp = entry
        now = time.monotonic()
        if now < fresh_exp:
            if len(_hits) < MAX_STATS_SIZE or ns in _hits:
                _hits[ns] = _hits.get(ns, 0) + 1
            return (value, True)
        elif now < stale_exp:
            if len(_hits) < MAX_STATS_SIZE or ns in _hits:
                _hits[ns] = _hits.get(ns, 0) + 1
            return (value, False)
        # Expired
        _store.pop(key, None)
    if len(_misses) < MAX_STATS_SIZE or ns in _misses:
        _misses[ns] = _misses.get(ns, 0) + 1
    return None''', replacement1)


# Fix 1, 2, 3: background task ref, disconnect herd, double invalidstate
replacement2 = '''_bg_tasks: set[asyncio.Task] = set()

async def cache_get_or_fetch(
    key: str, 
    ttl: int, 
    fetcher: Callable[[], Awaitable[Any]], 
    stale_ttl: int = 0, 
    jitter: bool = False
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
            return await cache_get_or_fetch(key, ttl, fetcher, stale_ttl, jitter)
        
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
                del _inflight[key]'''

content = content.replace('''async def cache_get_or_fetch(
    key: str, 
    ttl: int, 
    fetcher: Callable[[], Awaitable[Any]], 
    stale_ttl: int = 0, 
    jitter: bool = False
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
            fut.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            _inflight[key] = fut
            
        async def background_fetch() -> None:
            try:
                new_value = await fetcher()
                await cache_set(key, new_value, ttl, stale_ttl, jitter)
                fut.set_result(new_value)
            except SkipCache as e:
                fut.set_result(e.value)
            except Exception as e:
                fut.set_exception(e)
            finally:
                async with _lock:
                    if key in _inflight and _inflight[key] is fut:
                        if not fut.done():
                            fut.cancel()
                        del _inflight[key]

        asyncio.create_task(background_fetch())
        return value

    # Normal missing fetch
    async with _lock:
        if key in _inflight:
            fut = _inflight[key]
            is_leader = False
        else:
            fut = asyncio.get_running_loop().create_future()
            fut.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            _inflight[key] = fut
            is_leader = True

    if not is_leader:
        return await fut
        
    try:
        value = await fetcher()
        await cache_set(key, value, ttl, stale_ttl, jitter)
        fut.set_result(value)
        return value
    except SkipCache as e:
        fut.set_result(e.value)
        return e.value
    except Exception as e:
        fut.set_exception(e)
        raise
    finally:
        async with _lock:
            if key in _inflight and _inflight[key] is fut:
                if not fut.done():
                    fut.cancel()
                del _inflight[key]''', replacement2)

with open('app/services/cache.py', 'w', encoding='utf-8') as f: f.write(content)
