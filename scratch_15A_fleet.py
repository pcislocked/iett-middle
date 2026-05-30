import asyncio
import aiohttp
from app.deps import set_session
from app.routers.routes import get_route_buses
from app.routers.fleet import get_fleet

async def main():
    s = aiohttp.ClientSession()
    set_session(s)
    try:
        route_buses = await get_route_buses("15A")
        route_kapinos = {x.kapino for x in route_buses}
        print("15A kapinos:", route_kapinos)
        
        fleet = await get_fleet()
        fleet_kapinos = {x.kapino for x in fleet}
        
        missing = route_kapinos - fleet_kapinos
        print("Missing from fleet:", missing)
    finally:
        await s.close()

asyncio.run(main())
