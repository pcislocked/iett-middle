import asyncio

class LazyLock:
    def __init__(self):
        self._lock = None
    
    async def __aenter__(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        await self._lock.acquire()
    
    async def __aexit__(self, exc_type, exc, tb):
        self._lock.release()
