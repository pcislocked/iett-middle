"""Fleet refresh helpers — on-demand polling replaces the old always-on background loop."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def refresh_fleet_once() -> None:
    """Fetch a single fleet snapshot from IETT and update the in-memory store.

    Safe to call concurrently — deps.ensure_fleet_fresh() deduplicates in-flight
    tasks so only one upstream call is ever in progress at a time.
    """
    from app.deps import get_session, update_fleet  # noqa: PLC0415
    from app.services.iett_client import IettApiError, IettClient  # noqa: PLC0415

    logger.debug("Fleet refresh triggered")
    try:
        client = IettClient(get_session())
        buses = await client.get_all_buses()
        update_fleet(buses)
        logger.debug("Fleet snapshot updated: %d buses", len(buses))
    except IettApiError as exc:
        logger.warning("Fleet refresh failed: %s", exc)
    except Exception:  # noqa: BLE001
        logger.exception("Unexpected fleet refresh error")
