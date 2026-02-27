"""Fleet router — /v1/fleet"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.main import get_session
from app.models.bus import BusPosition
from app.services.cache import cache_get, cache_set
from app.services.iett_client import IettApiError, IettClient
from app.config import settings

router = APIRouter()


@router.get("", response_model=list[BusPosition])
async def get_fleet():
    """All active Istanbul buses (~7,000 records). Cached 15s."""
    key = "fleet:all"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        buses = await client.get_all_buses()
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    await cache_set(key, [b.model_dump() for b in buses], settings.cache_ttl_fleet)
    return buses


@router.get("/{kapino}", response_model=BusPosition)
async def get_bus(kapino: str):
    """Single bus live position by door number (e.g. C-325). Reads from cached fleet."""
    key = "fleet:all"
    cached = await cache_get(key)
    if cached is None:
        # Warm the cache
        client = IettClient(get_session())
        try:
            buses = await client.get_all_buses()
        except IettApiError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        await cache_set(key, [b.model_dump() for b in buses], settings.cache_ttl_fleet)
        cached = [b.model_dump() for b in buses]
    match = next((b for b in cached if b["kapino"].upper() == kapino.upper()), None)
    if match is None:
        raise HTTPException(404, detail=f"Bus {kapino!r} not found in active fleet")
    return match
