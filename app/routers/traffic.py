"""Traffic router — /v1/traffic"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.main import get_session
from app.models.traffic import TrafficIndex, TrafficSegment
from app.services.cache import cache_get, cache_set
from app.services.iett_client import IettApiError
from app.services.traffic import TrafficClient

router = APIRouter()


@router.get("/index", response_model=TrafficIndex)
async def get_traffic_index():
    """City-wide Istanbul congestion percentage."""
    key = "traffic:index"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = TrafficClient(get_session())
    try:
        index = await client.get_traffic_index()
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    await cache_set(key, index.model_dump(), settings.cache_ttl_traffic)
    return index


@router.get("/segments", response_model=list[TrafficSegment])
async def get_traffic_segments():
    """Per-road segment speeds and congestion levels (~587 kB, cached 30s)."""
    key = "traffic:segments"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = TrafficClient(get_session())
    try:
        segments = await client.get_traffic_segments()
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [s.model_dump() for s in segments]
    await cache_set(key, data, settings.cache_ttl_traffic)
    return segments
