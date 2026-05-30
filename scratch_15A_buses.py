import asyncio
import aiohttp
from app.deps import set_session
from app.routers.routes import get_route_buses

async def main():
    s = aiohttp.ClientSession()
    set_session(s)
    try:
        b = await get_route_buses("15A")
        print([(x.kapino, x.direction_letter) for x in b])
    finally:
        await s.close()

asyncio.run(main())
