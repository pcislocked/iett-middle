import asyncio
import aiohttp
from app.deps import set_session
from app.routers.routes import get_route_buses

async def main():
    s = aiohttp.ClientSession()
    set_session(s)
    try:
        buses = await get_route_buses("15A")
        for b in buses[:2]:
            print(b.model_dump())
    finally:
        await s.close()

asyncio.run(main())
