"""Fleet router — /v1/fleet

Data is served entirely from the in-memory store populated by the background
poller.  There are no on-demand IETT upstream calls here, which prevents
rate-limiting and ensures low latency.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.deps import get_fleet_snapshot, get_fleet_updated_at, get_trail
from app.models.bus import BusPositionWithTrail

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

    Served from the background-polled in-memory store — no upstream call.
    Returns 503 for the first ~30 s while the initial poll is completing.
    """
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
    updated = get_fleet_updated_at()
    return {
        "bus_count": len(get_fleet_snapshot()),
        "updated_at": updated.isoformat() if updated else None,
    }


@router.post("/refresh", status_code=202)
async def refresh_fleet():
    """Trigger an immediate out-of-band fleet re-poll.

    Useful for a manual Force Refresh button in the settings screen.
    The actual poll runs as a fire-and-forget background task.
    """
    async def _do() -> None:
        from app.deps import get_session, update_fleet  # noqa: PLC0415
        from app.services.iett_client import IettApiError, IettClient  # noqa: PLC0415
        try:
            buses = await IettClient(get_session()).get_all_buses()
            update_fleet(buses)
            logger.info("Manual refresh: %d buses", len(buses))
        except IettApiError as exc:
            logger.warning("Manual refresh failed: %s", exc)

    asyncio.create_task(_do())
    return {"status": "refresh triggered"}


@router.get("/{kapino}", response_model=BusPositionWithTrail)
async def get_bus(kapino: str):
    """Single bus live position + trail by door number (e.g. C-325)."""
    snapshot = get_fleet_snapshot()
    match = next((b for b in snapshot if b["kapino"].upper() == kapino.upper()), None)
    if match is None:
        raise HTTPException(404, detail=f"Bus {kapino!r} not found in active fleet")
    return {**match, "trail": get_trail(match["kapino"])}
