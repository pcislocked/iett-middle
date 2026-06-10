import asyncio
import aiohttp
from app.services.mobiett_client import MobiettClient
from app.config import settings

async def main():
    async with aiohttp.ClientSession() as s:
        client = MobiettClient(s)
        try:
            res = await client._post_service("fakeAlias12345", {})
            print("Fake alias response:", res)
        except Exception as e:
            print("Fake alias error:", type(e).__name__, e)

if __name__ == '__main__':
    asyncio.run(main())
