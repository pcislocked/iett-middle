import asyncio
import aiohttp
from app.deps import set_session
from app.routers.routes import get_route_buses
from app.routers.fleet import get_bus_detail

async def main():
    s = aiohttp.ClientSession()
    set_session(s)
    try:
        # 1. Fetch 15A buses to populate the fleet
        await get_route_buses("15A")
        
        # 2. Try fetching details for M2089
        b = await get_bus_detail("M2089")
        print("resolved_route_code:", b.get('resolved_route_code'))
        print("route_is_live:", b.get('route_is_live'))
        print("route_stops count:", len(b.get('route_stops', [])))
    finally:
        await s.close()

asyncio.run(main())
