"""Traffic router — /v1/traffic"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.deps import get_session
from app.models.traffic import TrafficIndex, TrafficSegment
from app.services.cache import cache_get_or_fetch
from app.services.iett_client import IettApiError
from app.services.traffic import TrafficClient

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/index", response_model=TrafficIndex)
async def get_traffic_index():
    """Get the city-wide Istanbul traffic congestion index.

    Returns a single percentage value representing the overall traffic density
    across Istanbul. Cached for 30 seconds.
    """
    key = "traffic:index"

    async def _fetch():
        client = TrafficClient(get_session())
        try:
            index = await client.get_traffic_index()
        except IettApiError as exc:
            logger.warning("get_traffic_index failed: %s", exc)
            raise HTTPException(502, detail="Trafik indeksi servisine ulaşılamadı.") from exc
        return index.model_dump()

    return await cache_get_or_fetch(
        key,
        settings.cache_ttl_traffic,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
    )


@router.get("/segments", response_model=list[TrafficSegment])
async def get_traffic_segments():
    """Get live traffic speeds and congestion levels for all road segments.

    Returns a large array (~587 kB) of road segments with their current speed (km/h)
    and congestion category (1-6). Cached for 30 seconds.
    """
    key = "traffic:segments"

    async def _fetch():
        client = TrafficClient(get_session())
        try:
            segments = await client.get_traffic_segments()
        except IettApiError as exc:
            logger.warning("get_traffic_segments failed: %s", exc)
            raise HTTPException(502, detail="Trafik segment servisine ulaşılamadı.") from exc
        return [s.model_dump() for s in segments]

    return await cache_get_or_fetch(
        key,
        settings.cache_ttl_traffic,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
    )
