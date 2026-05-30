import asyncio
import aiohttp
from app.deps import set_session
from app.services.iett_client import IettClient

async def main():
    s = aiohttp.ClientSession()
    set_session(s)
    client = IettClient(s)
    res = await client.search_routes("15A")
    print(res)
    await s.close()

asyncio.run(main())
