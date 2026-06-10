import asyncio
import aiohttp
import time
import json
from app.services.mobiett_client import MobiettClient
from app.config import settings

async def main():
    async with aiohttp.ClientSession() as s:
        client = MobiettClient(s)
        now_ms = int(time.time() * 1000)
        
        test_payloads = [
            # Exactly like dump
            {"did090101.notice.endtime": str(now_ms), "did090101.notice.starttime": str(now_ms)},
            # Without payload
            None,
            # Empty payload
            {},
            # 1 day ago to now
            {"did090101.notice.endtime": str(now_ms), "did090101.notice.starttime": str(now_ms - 86400000)}
        ]
        
        for p in test_payloads:
            try:
                print(f"Testing payload: {p}")
                res = await client._post_service("otnGetNotice", p)
                if res:
                    print("SUCCESS! Found", len(res), "records. Sample:")
                    print(json.dumps(res[0], indent=2, ensure_ascii=False))
                else:
                    print("Empty response.")
            except Exception as e:
                print("Error:", type(e).__name__, e)
            print("-" * 40)

if __name__ == '__main__':
    asyncio.run(main())
