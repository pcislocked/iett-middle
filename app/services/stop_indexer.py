"""Background task: fetch all IETT stops at startup and keep index fresh.

Runs once immediately, then refreshes every 24 h (stops change rarely).
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_REFRESH_INTERVAL = 24 * 60 * 60  # 24 hours


async def index_stops_forever() -> None:
    """Fetch the full stop catalogue on startup, refresh daily.

    Lazy imports to avoid circular-import problems at module load time.
    """
    from app.deps import get_session, update_stop_index  # noqa: PLC0415
    from app.services.iett_client import IettApiError, IettClient  # noqa: PLC0415

    logger.info("Stop indexer started — fetching all stops…")
    while True:
        try:
            client = IettClient(get_session())
            stops = await client.get_all_stops()
            update_stop_index(stops)
            logger.info("Stop index ready: %d stops indexed", len(stops))
        except IettApiError as exc:
            logger.warning("Stop index fetch failed (will retry in 60 s): %s", exc)
            await asyncio.sleep(60)
            continue
        except asyncio.CancelledError:
            logger.info("Stop indexer cancelled — shutting down")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error in stop indexer")

        # Wait until next daily refresh
        try:
            await asyncio.sleep(_REFRESH_INTERVAL)
        except asyncio.CancelledError:
            logger.info("Stop indexer cancelled during sleep")
            raise
