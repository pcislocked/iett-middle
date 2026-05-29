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
import math
import threading
import time
from typing import Any, Literal, TypedDict, cast

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
_manual_refresh_last_triggered: float = 0.0
_manual_refresh_lock = threading.Lock()
_inflight_probes: dict[str, asyncio.Event] = {}


class FleetMetaResponse(TypedDict):
    bus_count: int
    updated_at: str | None


class FleetRefreshQueuedResponse(TypedDict):
    status: Literal["queued"]


class FleetRefreshCooldownResponse(TypedDict):
    status: Literal["cooldown"]
    retry_after_seconds: int


FleetRefreshResponse = FleetRefreshQueuedResponse | FleetRefreshCooldownResponse


def _snapshot_with_trails() -> list[dict[str, Any]]:
    return [
        {**b, "trail": get_trail(cast(str, b["kapino"]))}
        for b in get_fleet_snapshot()
    ]


@router.get("", response_model=list[BusPositionWithTrail])
async def get_fleet() -> list[dict[str, Any]]:
    """All active Istanbul buses with 5-minute position trails.

    Served from the in-memory store.  Triggers a background refresh when data
    is ≥30 s stale (stale-while-revalidate); returns 503 only before the very
    first snapshot is available.
    
    Additionally, fleet is forcibly refreshed at least every 15 minutes to prevent
    stale FILO data (some IBB SOAP responses can be 6+ hours old).
    """
    from app.config import settings  # noqa: PLC0415
    
    # Use max age from settings to force periodic refresh (default 15min)
    await ensure_fleet_fresh(max_age_seconds=settings.fleet_cache_max_age)
    snapshot = get_fleet_snapshot()
    if not snapshot:
        raise HTTPException(
            503,
            detail="Fleet data not yet available; initial poll in progress — retry in a moment",
        )
    return _snapshot_with_trails()


@router.get("/meta", tags=["fleet"])
async def get_fleet_meta() -> FleetMetaResponse:
    """Lightweight status: bus count + last update timestamp."""
    from app.config import settings  # noqa: PLC0415
    
    await ensure_fleet_fresh(max_age_seconds=settings.fleet_cache_max_age)
    updated = get_fleet_updated_at()
    return {
        "bus_count": len(get_fleet_snapshot()),
        "updated_at": updated.isoformat() if updated else None,
    }


@router.post("/refresh", status_code=202)
async def refresh_fleet() -> FleetRefreshResponse:
    """Queue an immediate out-of-band fleet re-poll.

    Keeps stale-while-revalidate semantics while allowing operators/clients to
    request an immediate refresh cycle when needed.
    """
    from app.config import settings  # noqa: PLC0415

    cooldown = max(0, settings.fleet_manual_refresh_cooldown)
    global _manual_refresh_last_triggered  # noqa: PLW0603

    with _manual_refresh_lock:
        now = time.monotonic()
        elapsed = now - _manual_refresh_last_triggered
        if _manual_refresh_last_triggered > 0 and cooldown > 0 and elapsed < cooldown:
            retry_after = max(1, math.ceil(cooldown - elapsed))
            return {"status": "cooldown", "retry_after_seconds": retry_after}
        _manual_refresh_last_triggered = now

    await ensure_fleet_fresh(max_age_seconds=0)
    return {"status": "queued"}


@router.get("/{kapino}/detail", response_model=BusDetail)
async def get_bus_detail(kapino: str) -> dict[str, Any]:
    """Single bus with resolved route code + ordered stop list in one call.

    ``resolved_route_code`` uses the live ``route_code`` when available; falls
    back to the last route seen for this kapino since server startup so that
    parked / nightly-service buses still show their route.
    ``route_stops`` is fetched from cache or ntcapi so the client can draw a
    route polyline without a second round-trip.
    """
    from app.config import settings  # noqa: PLC0415
    from app.models.stop import RouteStop  # noqa: PLC0415
    from app.services import normalizers, ntcapi_client  # noqa: PLC0415
    from app.services.cache import cache_get, cache_set  # noqa: PLC0415
    from app.services.iett_client import IettApiError, IettClient  # noqa: PLC0415
    from app.services.ntcapi_client import NtcApiError  # noqa: PLC0415

    await ensure_fleet_fresh(max_age_seconds=settings.fleet_cache_max_age)
    snapshot = get_fleet_snapshot()
    match = next((b for b in snapshot if b["kapino"].upper() == kapino.upper()), None)
    if match is None:
        raise HTTPException(404, detail=f"Bus {kapino!r} not found in active fleet")

    bus: dict[str, Any] = {**match, "trail": get_trail(cast(str, match["kapino"]))}

    # Resolve route: prefer live field, fall back to last known
    _raw_live: str | None = match.get("route_code")
    live_route_code: str | None = _raw_live.strip().upper() if _raw_live else None
    route_code: str | None = live_route_code or get_last_route_by_kapino(kapino)
    route_is_live: bool = bool(live_route_code)

    route_stops_data: list[dict[str, Any]] = []
    if route_code:
        cache_key = f"routes:stops:v2:{route_code}"
        cached = await cache_get(cache_key)
        if cached is not None:
            route_stops_data = cast(list[dict[str, Any]], cached)
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
                from app.deps import get_stop  # noqa: PLC0415
                for s in stops:
                    if s.stop_direction is None:
                        idx_stop = get_stop(s.stop_code)
                        if idx_stop:
                            s.stop_direction = idx_stop.get("direction")
                route_stops_data = [s.model_dump() for s in stops]
                if stops and all(
                    s.latitude is not None and s.longitude is not None for s in stops
                ):
                    await cache_set(cache_key, route_stops_data, settings.cache_ttl_stops)
            except (NtcApiError, IettApiError):
                needs_fallback = True
            except Exception:  # noqa: BLE001
                logger.exception("Unexpected error fetching ntcapi route stops for route %r", route_code)
                needs_fallback = True
            else:
                needs_fallback = False
            if needs_fallback:
                try:
                    client = IettClient(session)
                    stops = await client.get_route_stops(route_code)
                    from app.deps import get_stop  # noqa: PLC0415
                    for s in stops:
                        if s.stop_direction is None:
                            idx_stop = get_stop(s.stop_code)
                            if idx_stop:
                                s.stop_direction = idx_stop.get("direction")
                    route_stops_data = [s.model_dump() for s in stops]
                    if stops:
                        await cache_set(cache_key, route_stops_data, settings.cache_ttl_stops)
                except Exception:  # noqa: BLE001
                    logger.exception("IETT fallback for route stops failed for route %r", route_code)

        # Safety enrichment for cached stops that might lack direction
        from app.deps import get_stop  # noqa: PLC0415
        for s in route_stops_data:
            if s.get("stop_direction") is None:
                idx_stop = get_stop(s.get("stop_code") or "")
                if idx_stop:
                    s["stop_direction"] = idx_stop.get("direction")

    # ── Probe & Cache Amenities ─────────────────────────────────────────────
    # If the bus is active on a route and we have its upcoming stops, we can
    # try to hit the stop-arrivals endpoint for its next stop to extract its
    # wifi/klima/usb capabilities (since fleet data doesn't provide them).
    cache_key = f"amenities:kapino:{kapino.upper()}"
    amenities = await cache_get(cache_key)
    
    if amenities is not None and not isinstance(amenities, dict):
        amenities = None
        
    if amenities is None and route_is_live and route_stops_data:
        event = _inflight_probes.get(kapino.upper())
        if event:
            await event.wait()
            amenities = await cache_get(cache_key)
            
        if amenities is None:
            event = asyncio.Event()
            _inflight_probes[kapino.upper()] = event
            try:
                # Find up to 3 upcoming stops this bus is approaching
                # If we have stop_sequence from fleet, use it; otherwise fallback
                seq = bus.get("stop_sequence")
                target_stop_codes = []
                if seq is not None and seq > 0:
                    for s in route_stops_data:
                        s_seq = s.get("sequence")
                        # Start from the 2nd stop ahead (seq + 2) to avoid race conditions
                        if s_seq and seq + 2 <= s_seq <= seq + 4:
                            target_stop_codes.append(s.get("stop_code"))
                            
                if not target_stop_codes:
                    # Fallback to nearest stop or first stop
                    ns = bus.get("nearest_stop")
                    if ns:
                        target_stop_codes.append(ns)
                    elif route_stops_data:
                        target_stop_codes.append(route_stops_data[0].get("stop_code"))

                # Make targets unique and preserve order
                seen_targets = set()
                unique_targets = []
                for t in target_stop_codes:
                    if t and t not in seen_targets:
                        seen_targets.add(t)
                        unique_targets.append(t)

                session = get_session()
                for target_stop_code in unique_targets[:2]:
                    try:
                        raw_arrs = await ntcapi_client.get_stop_arrivals(target_stop_code, session)
                        canonical_arrs = [normalizers.arrivals.from_ntcapi_ybs(r) for r in raw_arrs]
                        for arr in canonical_arrs:
                            arr_kapino = arr.get("kapino")
                            if arr_kapino and arr_kapino.upper() == kapino.upper():
                                found_amenities = arr.get("amenities")
                                if found_amenities:
                                    amenities = found_amenities if isinstance(found_amenities, dict) else getattr(found_amenities, "model_dump", lambda: found_amenities)()
                                    # Cache for 30 days
                                    await cache_set(cache_key, amenities, 86400 * 30)
                                break
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Failed to probe amenities for %r at %r: %s", kapino, target_stop_code, exc)
                    
                    # If we found amenities from this stop's arrivals, stop probing!
                    if amenities:
                        break
                
                # If still not found after probing, set a negative cache for 15 minutes to prevent spamming
                if not amenities:
                    await cache_set(cache_key, {}, 900)
            finally:
                event.set()
                if _inflight_probes.get(kapino.upper()) is event:
                    _inflight_probes.pop(kapino.upper(), None)

    if amenities:
        bus["has_usb"] = amenities.get("usb")
        bus["has_wifi"] = amenities.get("wifi")
        bus["is_air_conditioned"] = amenities.get("ac")
        bus["accessible"] = amenities.get("accessible")

    return {**bus, "resolved_route_code": route_code, "route_is_live": route_is_live, "route_stops": route_stops_data}


@router.get("/{kapino}", response_model=BusPositionWithTrail)
async def get_bus(kapino: str) -> dict[str, Any]:
    """Single bus live position + trail by door number (e.g. C-325)."""
    from app.config import settings  # noqa: PLC0415
    
    await ensure_fleet_fresh(max_age_seconds=settings.fleet_cache_max_age)
    snapshot = get_fleet_snapshot()
    match = next((b for b in snapshot if b["kapino"].upper() == kapino.upper()), None)
    if match is None:
        raise HTTPException(404, detail=f"Bus {kapino!r} not found in active fleet")
    return {**match, "trail": get_trail(cast(str, match["kapino"]))}
