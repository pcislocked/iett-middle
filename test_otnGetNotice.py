import asyncio
import aiohttp
import time
from app.services.mobiett_client import MobiettClient
from app.config import settings

async def main():
    async with aiohttp.ClientSession() as s:
        client = MobiettClient(s)
        now_ms = int(time.time() * 1000)
        try:
            res = await client._post_service("otnGetNotice", {
                "did090101.notice.endtime": str(now_ms),
                "did090101.notice.starttime": str(now_ms)
            })
            print("otnGetNotice Response:", str(res)[:1000])
        except Exception as e:
            print("Error:", e)

if __name__ == '__main__':
    asyncio.run(main())
