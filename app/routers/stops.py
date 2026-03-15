"""Stops router — /v1/stops"""
from __future__ import annotations

import logging
import math as _math

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.deps import get_plate_by_kapino, get_session
from app.models.bus import Arrival
from app.models.stop import NearbyStop, StopDetail, StopSearchResult
from app.services.cache import cache_get, cache_set
from app.services.iett_client import IettApiError, IettClient
from app.services import normalizers, ntcapi_client
from app.services.ntcapi_client import NtcApiError

logger = logging.getLogger(__name__)

router = APIRouter()


def _haversine_m(user_lat: float, user_lon: float, stop_lat: float, stop_lon: float) -> float:
    """Haversine distance in metres."""
    R = 6_371_000.0
    p1, p2 = _math.radians(user_lat), _math.radians(stop_lat)
    dp = p2 - p1
    dl = _math.radians(stop_lon - user_lon)
    a = _math.sin(dp / 2) ** 2 + _math.cos(p1) * _math.cos(p2) * _math.sin(dl / 2) ** 2
    return R * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(1 - a))


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

    ntcapi ``mainGetBusStopNearby`` is the primary source.  Falls back to
    the in-memory spatial index populated at startup.
    Returns up to 30 results.
    """
    session = get_session()

    # ── primary: ntcapi mainGetBusStopNearby ────────────────────────
    try:
        raw_stops = await ntcapi_client.get_nearby_stops(lat, lon, radius / 1000, session)
        canonical = [normalizers.stops.from_ntcapi_nearby_processed(r) for r in raw_stops]
        nearby_results: list[NearbyStop] = []
        for c in canonical[:30]:
            try:
                latitude = float(c["lat"])  # type: ignore[arg-type]
                longitude = float(c["lon"])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping nearby stop with invalid coordinates: %s", c.get("stop_code"))
                continue
            nearby_results.append(
                NearbyStop(
                    stop_code=c.get("stop_code") or "",
                    stop_name=c.get("stop_name") or "",
                    latitude=latitude,
                    longitude=longitude,
                    district=c.get("district"),
                    direction=c.get("direction"),
                    distance_m=c.get("distance_m") if c.get("distance_m") is not None else _haversine_m(lat, lon, latitude, longitude),
                )
            )
        return nearby_results
    except NtcApiError as exc:
        logger.warning("ntcapi nearby stops failed (lat=%s lon=%s), falling back to index: %s", lat, lon, exc)

    # ── fallback: in-memory spatial index ───────────────────────────
    from app.deps import get_nearby_stops as _get_nearby, get_stop_index_updated_at  # noqa: PLC0415

    if get_stop_index_updated_at() is None:
        raise HTTPException(503, detail="Stop index not ready yet — try again in a moment")
    results = _get_nearby(lat, lon, radius)
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
    All sources are normalised to :class:`~app.models.bus.Arrival` via the
    canonical data layer.
    """
    key = f"stops:arrivals:{dcode}" + (f":via:{via}" if via else "")
    cached = await cache_get(key)
    if cached is not None:
        arrivals_data: list[dict] = cached
    else:
        session = get_session()
        arrivals_data = []

        # ── primary: ntcapi ybs (has kapino + live bus location) ──────
        try:
            raw_items = await ntcapi_client.get_stop_arrivals(dcode, session)
            canonical = [normalizers.arrivals.from_ntcapi_ybs(r) for r in raw_items]
            canonical.sort(
                key=lambda a: a.get("eta_minutes") if a.get("eta_minutes") is not None else 9999
            )
            arrivals_data = list(canonical)
        except NtcApiError as exc:
            logger.warning("ntcapi arrivals failed for %s, falling back to HTML: %s", dcode, exc)

        # ── fallback: legacy IETT HTML (no kapino, no location) ───────
        if not arrivals_data:
            client = IettClient(session)
            try:
                if via:
                    iett_arrivals = await client.get_stop_arrivals_via(dcode, via)
                else:
                    iett_arrivals = await client.get_stop_arrivals(dcode)
            except IettApiError as exc:
                raise HTTPException(502, detail=str(exc)) from exc
            arrivals_data = [
                normalizers.arrivals.from_iett_html(a.model_dump()) for a in iett_arrivals
            ]

        # ── via filter (applied after ntcapi fetch if needed) ─────────
        if via and arrivals_data:
            try:
                client2 = IettClient(session)
                routes_via = await client2.get_routes_at_stop(via)
                arrivals_data = [a for a in arrivals_data if a.get("route_code") in routes_via]
            except IettApiError as exc:
                logger.warning(
                    "via-filter lookup failed for stop %s via %s — returning unfiltered arrivals: %s",
                    dcode, via, exc,
                )

        await cache_set(key, arrivals_data, settings.cache_ttl_arrivals)

    # Enrich with plate from in-memory fleet store (free, O(1) by kapino).
    result = []
    for a in arrivals_data:
        kapino = a.get("kapino")
        plate = a.get("plate") or (get_plate_by_kapino(kapino) if kapino else None)
        result.append(Arrival(**{k: v for k, v in a.items() if k not in ("_source", "plate")}, plate=plate))
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
