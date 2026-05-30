import asyncio
import aiohttp
from app.deps import set_session
from app.routers.fleet import get_bus_detail

async def main():
    s = aiohttp.ClientSession()
    set_session(s)
    try:
        # Use one of the 15A kapinos we found earlier
        b = await get_bus_detail("M2089")
        print("resolved_route_code:", b.get('resolved_route_code'))
        print("route_is_live:", b.get('route_is_live'))
    finally:
        await s.close()

asyncio.run(main())
