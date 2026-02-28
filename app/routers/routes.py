"""Routes router — /v1/routes"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.deps import get_session
from app.models.bus import BusPosition
from app.models.route import Announcement, RouteMetadata, RouteSearchResult, ScheduledDeparture
from app.models.stop import RouteStop
from app.services.cache import cache_get, cache_set
from app.services.iett_client import IettApiError, IettClient

router = APIRouter()


@router.get("/search", response_model=list[RouteSearchResult])
async def search_routes(q: str = Query(..., min_length=1)):
    """Search routes by name or code (e.g. '14M', 'kadikoy')."""
    key = f"routes:search:{q.lower()}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        results = await client.search_routes(q)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [r.model_dump() for r in results]
    await cache_set(key, data, settings.cache_ttl_search)
    return results


@router.get("/{hat_kodu}", response_model=list[RouteMetadata])
async def get_route_metadata(hat_kodu: str):
    """Route variant/direction metadata (names, codes, direction) via GetAllRoute."""
    key = f"routes:meta:{hat_kodu}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        meta = await client.get_route_metadata(hat_kodu)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [m.model_dump() for m in meta]
    await cache_set(key, data, settings.cache_ttl_search)
    return meta


@router.get("/{hat_kodu}/buses", response_model=list[BusPosition])
async def get_route_buses(hat_kodu: str):
    """Live GPS positions of all buses on a route."""
    key = f"routes:buses:{hat_kodu}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        buses = await client.get_route_buses(hat_kodu)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [b.model_dump() for b in buses]
    await cache_set(key, data, settings.cache_ttl_fleet)
    return buses


@router.get("/{hat_kodu}/stops", response_model=list[RouteStop])
async def get_route_stops(hat_kodu: str):
    """Ordered stop list for a route with coordinates."""
    key = f"routes:stops:{hat_kodu}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        stops = await client.get_route_stops(hat_kodu)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [s.model_dump() for s in stops]
    # Only cache when stop index was ready (all coords present);
    # coord-less responses are cheap to re-fetch and should not poison the cache.
    if stops and all(s.latitude is not None for s in stops):
        await cache_set(key, data, settings.cache_ttl_stops)
    return stops


@router.get("/{hat_kodu}/schedule", response_model=list[ScheduledDeparture])
async def get_route_schedule(hat_kodu: str):
    """Planned departure times for a route (all day types)."""
    key = f"routes:schedule:{hat_kodu}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        schedule = await client.get_route_schedule(hat_kodu)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [d.model_dump() for d in schedule]
    await cache_set(key, data, settings.cache_ttl_schedule)
    return schedule


@router.get("/{hat_kodu}/announcements", response_model=list[Announcement])
async def get_route_announcements(hat_kodu: str):
    """Active disruption announcements for a route."""
    key = f"routes:announcements:{hat_kodu}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        announcements = await client.get_announcements(hat_kodu)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [a.model_dump() for a in announcements]
    await cache_set(key, data, settings.cache_ttl_announcements)
    return announcements
