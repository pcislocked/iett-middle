import asyncio
from app.deps import set_session, close_session
from app.services.ntcapi_client import get_route_metadata, get_route_stops
import aiohttp

async def main():
    s = aiohttp.ClientSession()
    set_session(s)
    try:
        meta = await get_route_metadata("15A", s)
        print("Meta:", meta)
        stops = await get_route_stops("15A", "G", s)
        print("Stops:", len(stops))
    finally:
        await s.close()

asyncio.run(main())
