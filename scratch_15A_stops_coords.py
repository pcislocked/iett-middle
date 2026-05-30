import asyncio
import aiohttp
from app.deps import set_session
from app.routers.routes import get_route_stops

async def main():
    s = aiohttp.ClientSession()
    set_session(s)
    try:
        stops = await get_route_stops("15A")
        empty = [s for s in stops if s.get('latitude') is None or s.get('longitude') is None]
        print(f"Total stops: {len(stops)}")
        print(f"Empty coordinate stops: {len(empty)}")
        for e in empty[:5]:
            print(e)
    finally:
        await s.close()

asyncio.run(main())
