import asyncio
import aiohttp
import json
from app.services.mobiett_client import MobiettClient
from app.config import settings

async def main():
    async with aiohttp.ClientSession() as s:
        client = MobiettClient(s)
        payload = {
            "data": {
                "password": settings.ntcapi_ybs_password,
                "username": settings.ntcapi_ybs_username
            },
            "method": "POST",
            "path": ["real-time-information", "stop-status", "260211"]
        }
        try:
            res = await client._post_service("ybs", payload)
            print("stop-status response:", json.dumps(res, indent=2, ensure_ascii=False))
        except Exception as e:
            print("Error:", type(e).__name__, e)

if __name__ == '__main__':
    asyncio.run(main())
