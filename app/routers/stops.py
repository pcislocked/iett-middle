"""Stops router — /v1/stops"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.deps import get_plate_by_kapino, get_session
from app.models.bus import Arrival
from app.models.stop import NearbyStop, StopDetail, StopSearchResult
from app.services.cache import cache_get, cache_set
from app.services.iett_client import IettApiError, IettClient
from app.services import ntcapi_client
from app.services.ntcapi_client import NtcApiError

logger = logging.getLogger(__name__)

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


@router.get("/nearby", response_model=list[NearbyStop])
async def nearby_stops(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius: float = Query(default=500, ge=50, le=2000),
):
    """Stops within *radius* metres of (lat, lon), sorted by distance.

    Requires the stop index to be ready (populated at startup).
    Returns up to 30 results.
    """
    from app.deps import get_nearby_stops, get_stop_index_updated_at  # noqa: PLC0415

    if get_stop_index_updated_at() is None:
        raise HTTPException(503, detail="Stop index not ready yet — try again in a moment")
    results = get_nearby_stops(lat, lon, radius)
    return results[:30]


@router.get("/{dcode}/arrivals/raw")
async def get_arrivals_raw(dcode: str):
    """Return the raw HTML from IETT GetStationInfo — debug only."""
    client = IettClient(get_session())
    try:
        html = await client._get_text(
            f"{settings.iett_rest_base}/tr/RouteStation/GetStationInfo",
            params={"dcode": dcode, "langid": "1"},
        )
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    from fastapi.responses import HTMLResponse  # noqa: PLC0415
    return HTMLResponse(content=html)


@router.get("/{dcode}/arrivals", response_model=list[Arrival])
async def get_arrivals(dcode: str, via: str | None = Query(default=None)):
    """Live ETAs at a stop, sourced from ntcapi ybs (has kapino + live location).

    Falls back to the legacy IETT HTML endpoint if ntcapi is unavailable.
    """
    key = f"stops:arrivals:{dcode}" + (f":via:{via}" if via else "")
    cached = await cache_get(key)
    if cached is not None:
        arrivals_data: list[dict] = cached
    else:
        session = get_session()
        arrivals: list[Arrival] = []

        # ── primary: ntcapi ybs (has kapino + live bus location) ──────
        try:
            arrivals = await ntcapi_client.get_stop_arrivals(dcode, session)
        except NtcApiError as exc:
            logger.warning("ntcapi arrivals failed for %s, falling back to HTML: %s", dcode, exc)
            arrivals = []

        # ── fallback: legacy IETT HTML (no kapino, no location) ───────
        if not arrivals:
            client = IettClient(session)
            try:
                if via:
                    arrivals = await client.get_stop_arrivals_via(dcode, via)
                else:
                    arrivals = await client.get_stop_arrivals(dcode)
            except IettApiError as exc:
                raise HTTPException(502, detail=str(exc)) from exc

        # ── via filter (applied after ntcapi fetch if needed) ─────────
        if via and arrivals:
            try:
                client2 = IettClient(session)
                routes_via = await client2.get_routes_at_stop(via)
                arrivals = [a for a in arrivals if a.route_code in routes_via]
            except IettApiError:
                pass  # best-effort — return unfiltered if via lookup fails

        arrivals_data = [a.model_dump() for a in arrivals]
        await cache_set(key, arrivals_data, settings.cache_ttl_arrivals)

    # Enrich with plate from in-memory fleet store (free, O(1) by kapino).
    result = []
    for a in arrivals_data:
        kapino = a.get("kapino")
        plate = a.get("plate") or (get_plate_by_kapino(kapino) if kapino else None)
        result.append({**a, "plate": plate})
    return result


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


@router.get("/{dcode}", response_model=StopDetail)
async def get_stop_detail(dcode: str):
    """Stop name and coordinates (from search + route stop lookup). Long-cached."""
    key = f"stops:detail:{dcode}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    detail = await client.get_stop_detail(dcode)
    if detail is None:
        raise HTTPException(404, detail=f"Stop {dcode!r} not found")
    await cache_set(key, detail.model_dump(), settings.cache_ttl_stops)
    return detail
