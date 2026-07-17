from typing import Any
from fastapi import APIRouter
from app.services.cache import cache_get
from app.services.notice_poller import GLOBAL_NOTICES_CACHE_KEY

router = APIRouter()

@router.get("/global", response_model=list[dict[str, Any]])
async def get_global_notices_endpoint() -> list[dict[str, Any]]:
    """Returns the cached global notices."""
    notices = await cache_get(GLOBAL_NOTICES_CACHE_KEY)
    if notices is None:
        return []
    return notices
