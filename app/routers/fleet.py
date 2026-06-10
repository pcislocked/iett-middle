"""Fleet router — /v1/fleet

Data is served from the in-memory store.  Fleet data is refreshed on-demand
(stale-while-revalidate): any request whose data is ≥30 s old triggers a
background refresh against the IETT all-fleet endpoint; subsequent requests
return fresh data.  This means the endpoint is only ever called once per 30 s
regardless of how many clients are connected.
"""

from __future__ import annotations

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
    get_trail,
)
from app.models.bus import BusDetail, BusPositionWithTrail

logger = logging.getLogger(__name__)
router = APIRouter()
_manual_refresh_last_triggered: float = 0.0
_manual_refresh_lock = threading.Lock()


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
        {**b, "trail": get_trail(cast(str, b["kapino"]))} for b in get_fleet_snapshot()
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

    await ensure_fleet_fresh()
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

    await ensure_fleet_fresh()
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

    await ensure_fleet_fresh()
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
        from app.routers.routes import get_route_stops

        try:
            route_stops_data = await get_route_stops(route_code)  # type: ignore
        except Exception:
            logger.exception("Failed to fetch route stops for %s", route_code)

    return {
        **bus,
        "resolved_route_code": route_code,
        "route_is_live": route_is_live,
        "route_stops": route_stops_data,
    }


@router.get("/{kapino}", response_model=BusPositionWithTrail)
async def get_bus(kapino: str) -> dict[str, Any]:
    """Single bus live position + trail by door number (e.g. C-325)."""

    await ensure_fleet_fresh()
    snapshot = get_fleet_snapshot()
    match = next((b for b in snapshot if b["kapino"].upper() == kapino.upper()), None)
    if match is None:
        raise HTTPException(404, detail=f"Bus {kapino!r} not found in active fleet")
    return {**match, "trail": get_trail(cast(str, match["kapino"]))}
