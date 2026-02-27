"""Background task: polls IETT fleet on a fixed interval and updates the in-memory store."""
from __future__ import annotations

import asyncio
import logging

from app.config import settings

logger = logging.getLogger(__name__)


async def poll_fleet_forever() -> None:
    """Poll IETT every ``settings.fleet_poll_interval`` seconds.

    Imports from deps/services at call time to avoid circular imports at
    module load.  Runs until the task is cancelled (on app shutdown).
    """
    # Lazy imports — resolved after the app is fully initialised
    from app.deps import get_session, update_fleet  # noqa: PLC0415
    from app.services.iett_client import IettApiError, IettClient  # noqa: PLC0415

    logger.info("Fleet poller started (interval=%ds, trail=%dmin)",
                settings.fleet_poll_interval, settings.fleet_trail_minutes)
    while True:
        try:
            client = IettClient(get_session())
            buses = await client.get_all_buses()
            update_fleet(buses)
            logger.debug("Fleet snapshot updated: %d buses", len(buses))
        except IettApiError as exc:
            logger.warning("Fleet poll failed: %s", exc)
        except asyncio.CancelledError:
            logger.info("Fleet poller cancelled — shutting down")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected fleet poll error")
        await asyncio.sleep(settings.fleet_poll_interval)
