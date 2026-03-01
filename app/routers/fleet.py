"""Fleet router — /v1/fleet

Data is served from the in-memory store.  Fleet data is refreshed on-demand
(stale-while-revalidate): any request whose data is ≥30 s old triggers a
background refresh against the IETT all-fleet endpoint; subsequent requests
return fresh data.  This means the endpoint is only ever called once per 30 s
regardless of how many clients are connected.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.deps import (
    ensure_fleet_fresh,
    get_fleet_snapshot,
    get_fleet_updated_at,
    get_last_route_by_kapino,
    get_session,
    get_trail,
)
from app.models.bus import BusDetail, BusPositionWithTrail

logger = logging.getLogger(__name__)
router = APIRouter()


def _snapshot_with_trails() -> list[dict]:
    return [
        {**b, "trail": get_trail(b["kapino"])}
        for b in get_fleet_snapshot()
    ]


@router.get("", response_model=list[BusPositionWithTrail])
async def get_fleet():
    """All active Istanbul buses with 5-minute position trails.

    Served from the in-memory store.  Triggers a background refresh when data
    is ≥30 s stale (stale-while-revalidate); returns 503 only before the very
    first snapshot is available.
    """
    await ensure_fleet_fresh()
    snapshot = get_fleet_snapshot()
    if not snapshot:
        raise HTTPException(
            503,
            detail="Fleet data not yet available; initial poll in progress — retry in a moment",
        )
    return _snapshot_with_trails()


@router.get("/meta", tags=["fleet"])
async def get_fleet_meta():
    """Lightweight status: bus count + last update timestamp."""
    await ensure_fleet_fresh()
    updated = get_fleet_updated_at()
    return {
        "bus_count": len(get_fleet_snapshot()),
        "updated_at": updated.isoformat() if updated else None,
    }


@router.post("/refresh", status_code=202)
async def refresh_fleet():
    """Trigger an immediate out-of-band fleet re-poll.

    Currently disabled — use the 30 s stale-while-revalidate cycle instead.
    """
    raise HTTPException(503, detail="Manual refresh is temporarily disabled.")


@router.get("/{kapino}/detail", response_model=BusDetail)
async def get_bus_detail(kapino: str):
    """Single bus with resolved route code + ordered stop list in one call.

    ``resolved_route_code`` uses the live ``route_code`` when available; falls
    back to the last route seen for this kapino since server startup so that
    parked / nightly-service buses still show their route.
    ``route_stops`` is fetched from cache or ntcapi so the client can draw a
    route polyline without a second round-trip.
    """
    import asyncio  # noqa: PLC0415

    from app.config import settings  # noqa: PLC0415
    from app.models.stop import RouteStop  # noqa: PLC0415
    from app.services import normalizers, ntcapi_client  # noqa: PLC0415
    from app.services.cache import cache_get, cache_set  # noqa: PLC0415
    from app.services.iett_client import IettApiError, IettClient  # noqa: PLC0415
    from app.services.ntcapi_client import NtcApiError  # noqa: PLC0415

    await ensure_fleet_fresh()
    snapshot = get_fleet_snapshot()
    match = next((b for b in snapshot if b["kapino"].upper() == kapino.upper()), None)
    if match is None:
        raise HTTPException(404, detail=f"Bus {kapino!r} not found in active fleet")

    bus = {**match, "trail": get_trail(match["kapino"])}

    # Resolve route: prefer live field, fall back to last known
    route_code: str | None = match.get("route_code") or get_last_route_by_kapino(kapino)

    route_stops_data: list[dict] = []
    if route_code:
        cache_key = f"routes:stops:{route_code}"
        cached = await cache_get(cache_key)
        if cached is not None:
            route_stops_data = cached
        else:
            session = get_session()
            try:
                raw_g, raw_d = await asyncio.gather(
                    ntcapi_client.get_route_stops(route_code, "G", session),
                    ntcapi_client.get_route_stops(route_code, "D", session),
                )
                canonical = [
                    normalizers.route_stops.from_ntcapi_route_processed(r)
                    for r in raw_g + raw_d
                ]
                stops = [
                    RouteStop(
                        route_code=c.get("route_code") or route_code,
                        direction=c.get("direction") or "G",
                        sequence=c.get("sequence") or 0,
                        stop_code=c.get("stop_code") or "",
                        stop_name=c.get("stop_name") or "",
                        latitude=c.get("lat"),
                        longitude=c.get("lon"),
                        district=c.get("district"),
                    )
                    for c in canonical
                ]
                route_stops_data = [s.model_dump() for s in stops]
                if stops and all(s.latitude is not None for s in stops):
                    await cache_set(cache_key, route_stops_data, settings.cache_ttl_stops)
            except (NtcApiError, IettApiError, Exception):  # noqa: BLE001
                # Route stops unavailable — return bus position only, no polyline
                try:
                    client = IettClient(session)
                    stops = await client.get_route_stops(route_code)
                    route_stops_data = [s.model_dump() for s in stops]
                except Exception:  # noqa: BLE001
                    pass

    return {**bus, "resolved_route_code": route_code, "route_stops": route_stops_data}


@router.get("/{kapino}", response_model=BusPositionWithTrail)
async def get_bus(kapino: str):
    """Single bus live position + trail by door number (e.g. C-325)."""
    snapshot = get_fleet_snapshot()
    match = next((b for b in snapshot if b["kapino"].upper() == kapino.upper()), None)
    if match is None:
        raise HTTPException(404, detail=f"Bus {kapino!r} not found in active fleet")
    return {**match, "trail": get_trail(match["kapino"])}
