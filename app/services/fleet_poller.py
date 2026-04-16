"""Fleet refresh helpers for one-shot and periodic fleet updates."""
from __future__ import annotations

import asyncio
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


async def refresh_fleet_forever(interval_seconds: int) -> None:
    """Trigger fleet refresh forever with a fixed minimum cadence.

    Uses deps.ensure_fleet_fresh(max_age_seconds=0) so refresh task creation
    remains deduplicated with request-triggered refreshes.
    """
    from app.deps import ensure_fleet_fresh  # noqa: PLC0415

    interval_seconds = max(1, int(interval_seconds))
    logger.info("Fleet periodic refresher started (interval=%ss)", interval_seconds)

    while True:
        try:
            await ensure_fleet_fresh(max_age_seconds=0)
        except asyncio.CancelledError:
            logger.info("Fleet periodic refresher cancelled")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error scheduling periodic fleet refresh")

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("Fleet periodic refresher cancelled during sleep")
            raise
