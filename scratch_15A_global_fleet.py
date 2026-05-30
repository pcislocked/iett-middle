import asyncio
import aiohttp
from app.services.iett_client import IettClient

async def main():
    s = aiohttp.ClientSession()
    client = IettClient(s)
    try:
        fleet = await client.get_fleet()
        fleet_kapinos = {x.kapino for x in fleet}
        
        # Kapinos we know are on 15A right now
        target_kapinos = {'M2089', 'M2125', 'M2140', 'M2143', 'M2163'}
        
        intersection = target_kapinos.intersection(fleet_kapinos)
        print(f"Target kapinos in global fleet: {len(intersection)}/{len(target_kapinos)}")
        print("Missing from global fleet:", target_kapinos - fleet_kapinos)
    finally:
        await s.close()

asyncio.run(main())
