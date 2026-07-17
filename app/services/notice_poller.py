import asyncio
import logging
import aiohttp

from app.deps import get_session
from app.services.ntcapi_client import get_global_notices
from app.services.cache import cache_set

logger = logging.getLogger(__name__)

# Cache key for global notices
GLOBAL_NOTICES_CACHE_KEY = "global_notices"

async def notice_poll_loop() -> None:
    """Periodically fetches global notices every 1 hour and caches them."""
    logger.info("notice_poller: started")
    
    # 1 hour = 3600 seconds
    poll_interval = 3600

    while True:
        try:
            session = get_session()
            notices = await get_global_notices(session)
            
            # Cache for slightly longer than the poll interval
            await cache_set(GLOBAL_NOTICES_CACHE_KEY, notices, ttl=poll_interval + 600, stale_ttl=poll_interval * 2)
            
            logger.debug(f"notice_poller: fetched and cached {len(notices)} global notices")
        except asyncio.CancelledError:
            logger.info("notice_poller: cancelled")
            break
        except Exception as e:
            logger.error(f"notice_poller: error fetching notices: {e}", exc_info=True)
            
        await asyncio.sleep(poll_interval)
