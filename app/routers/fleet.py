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

from app.deps import ensure_fleet_fresh, get_fleet_snapshot, get_fleet_updated_at, get_trail
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


@router.get("/{kapino}", response_model=BusPositionWithTrail)
async def get_bus(kapino: str):
    """Single bus live position + trail by door number (e.g. C-325)."""
    snapshot = get_fleet_snapshot()
    match = next((b for b in snapshot if b["kapino"].upper() == kapino.upper()), None)
    if match is None:
        raise HTTPException(404, detail=f"Bus {kapino!r} not found in active fleet")
    return {**match, "trail": get_trail(match["kapino"])}
