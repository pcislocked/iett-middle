import asyncio
import aiohttp
from app.deps import set_session
from app.routers.routes import get_route_stops

async def main():
    s = aiohttp.ClientSession()
    set_session(s)
    try:
        stops = await get_route_stops("15A")
        dirs = {s.get('direction') for s in stops}
        print("Stops directions:", dirs)
    finally:
        await s.close()

asyncio.run(main())
