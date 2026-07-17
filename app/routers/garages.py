"""Garages router — /v1/garages"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.deps import get_session
from app.models.garage import Garage
from app.services.cache import cache_get, cache_set
from app.services.iett_client import IettApiError, IettClient

router = APIRouter()

_CACHE_KEY = "garages:list"
_TTL = 86_400  # 24 h — garages almost never change


@router.get("", response_model=list[Garage])
async def list_garages():
    """Get all IETT bus garage locations.
    
    Returns a list of all bus garages/depots operated by IETT along with their 
    coordinates. This data changes very rarely and is cached for 24 hours.
    """
    cached = await cache_get(_CACHE_KEY)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        garages = await client.get_garages()
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [g.model_dump() for g in garages]
    await cache_set(_CACHE_KEY, data, _TTL)
    return garages
