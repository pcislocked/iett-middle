"""Stops router — /v1/stops"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.main import get_session
from app.models.bus import Arrival
from app.models.stop import StopSearchResult
from app.services.cache import cache_get, cache_set
from app.services.iett_client import IettApiError, IettClient

router = APIRouter()


@router.get("/search", response_model=list[StopSearchResult])
async def search_stops(q: str = Query(..., min_length=2)):
    """Search stops by name."""
    key = f"stops:search:{q.lower()}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        results = await client.search_stops(q)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [r.model_dump() for r in results]
    await cache_set(key, data, settings.cache_ttl_search)
    return results


@router.get("/{dcode}/arrivals", response_model=list[Arrival])
async def get_arrivals(dcode: str, via: str | None = Query(default=None)):
    """Live ETAs at a stop. Optionally filter to buses that also pass `via` stop."""
    key = f"stops:arrivals:{dcode}" + (f":via:{via}" if via else "")
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        if via:
            arrivals = await client.get_stop_arrivals_via(dcode, via)
        else:
            arrivals = await client.get_stop_arrivals(dcode)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [a.model_dump() for a in arrivals]
    await cache_set(key, data, settings.cache_ttl_arrivals)
    return arrivals


@router.get("/{dcode}/routes", response_model=list[str])
async def get_routes_at_stop(dcode: str):
    """All route codes that pass through a stop."""
    key = f"stops:routes:{dcode}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        route_codes = await client.get_routes_at_stop(dcode)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = sorted(route_codes)
    await cache_set(key, data, settings.cache_ttl_search)
    return data
