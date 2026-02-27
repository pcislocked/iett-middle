"""OSRM road-distance enrichment.

Strategy (per implementation_notes §7c):
  - On-demand only: enrich nearest bus per route, not the whole fleet.
  - Haversine+speed fallback when OSRM is unavailable or rate-limited.
"""
from __future__ import annotations

import logging
import math

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line distance (km) between two lat/lon points."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + (
        math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * math.asin(math.sqrt(a)) * 6371


def haversine_eta(
    bus_lat: float,
    bus_lon: float,
    stop_lat: float,
    stop_lon: float,
    speed_kmh: float | None,
) -> dict:
    """Estimate road distance + ETA using haversine + Istanbul detour factor."""
    dist_km = haversine(bus_lat, bus_lon, stop_lat, stop_lon)
    road_km = round(dist_km * 1.35, 2)
    eta_min: float | None = None
    if speed_kmh and speed_kmh > 2:
        eta_min = round((road_km / speed_kmh) * 60, 1)
    return {
        "haversine_km": round(dist_km, 2),
        "road_estimate_km": road_km,
        "eta_minutes": eta_min,
        "method": "haversine+speed",
    }


async def osrm_route(
    session: aiohttp.ClientSession,
    from_lon: float,
    from_lat: float,
    to_lon: float,
    to_lat: float,
) -> dict | None:
    """Road distance + ETA + geometry via OSRM.

    Returns None on any error (caller should fall back to haversine).
    Uses overview=full&geometries=geojson — required to get Leaflet-ready coords.
    """
    url = (
        f"{settings.osrm_base}/route/v1/driving/"
        f"{from_lon},{from_lat};{to_lon},{to_lat}"
    )
    try:
        async with session.get(
            url,
            params={"overview": "full", "geometries": "geojson"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
        route = data["routes"][0]
        # GeoJSON coords = [lon, lat] → flip to [lat, lon] for Leaflet
        coords = [[c[1], c[0]] for c in route["geometry"]["coordinates"]]
        return {
            "distance_km": round(route["distance"] / 1000, 2),
            "eta_minutes": round(route["duration"] / 60, 1),
            "geometry": coords,
            "method": "osrm",
        }
    except Exception:  # noqa: BLE001
        logger.debug("OSRM unavailable, caller will use haversine fallback")
        return None
