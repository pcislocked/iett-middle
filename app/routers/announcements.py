from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.deps import get_session, limiter
from app.models.announcement import OfficialAnnouncementsResponse
from app.services.cache import cache_get, cache_get_or_fetch
from app.services.iett_scraper import fetch_official_announcements
from app.services.notice_poller import GLOBAL_NOTICES_CACHE_KEY

router = APIRouter()


@router.get("/global", response_model=list[dict[str, Any]])
@limiter.limit("60/minute")
async def get_global_notices_endpoint(request: Request) -> list[dict[str, Any]]:
    """Get system-wide global announcements.

    Returns the cached global notices that affect the entire IETT system,
    such as major disruptions, holidays, severe weather warnings, or system-wide changes.
    These are overarching alerts that are **not** tied to a specific route or stop.
    """
    notices = await cache_get(GLOBAL_NOTICES_CACHE_KEY)
    if notices is None:
        return []
    return notices


@router.get("/official", response_model=OfficialAnnouncementsResponse)
@limiter.limit("30/minute")
async def get_official_announcements(
    request: Request,
    page: int = Query(0, ge=0, le=100, description="Page number (0-indexed)"),
    session=Depends(get_session),
) -> OfficialAnnouncementsResponse:
    """Get official announcements published on iett.istanbul/announcement/Index.

    Cached for 1 hour (3600s) with 24-hour (86400s) stale fallback resilience.
    """
    cache_key = f"announcements:official:{page}"

    async def _fetch():
        return await fetch_official_announcements(page=page, session=session)

    res = await cache_get_or_fetch(cache_key, 3600, _fetch, stale_ttl=86400)
    if not res:
        return OfficialAnnouncementsResponse(page=page, total_items=0, items=[])
    return OfficialAnnouncementsResponse(**res)
