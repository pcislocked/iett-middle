import asyncio
import aiohttp
from dotenv import load_dotenv
import os

load_dotenv()

from app.services.mobiett_client import MobiettClient

async def main():
    async with aiohttp.ClientSession() as s:
        client = MobiettClient(s)
        aliases = [
            "mainGetDurakDuyuru",
            "mainGetAnnouncement",
            "mainGetAnnouncements",
            "getDurakDuyuru",
            "mainGetNotice",
            "mainGetDuyuru"
        ]
        for alias in aliases:
            try:
                res = await client._post_service(alias, {"HATYONETIM.DURAK.DURAK_KODU": "260211"})
                print(f"Success for {alias}:", str(res)[:200])
            except Exception as e:
                print(f"Error for {alias}:", e)

if __name__ == '__main__':
    asyncio.run(main())
