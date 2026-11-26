"""IBB Traffic API client (trafik.ibb.gov.tr)."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

from app.config import settings
from app.models.traffic import TrafficIndex, TrafficSegment
from app.services.iett_client import IettApiError

logger = logging.getLogger(__name__)

_CONGESTION_LABELS = {
    1: "Free",
    2: "Open",
    3: "Moderate",
    4: "Dense",
    5: "Very Dense",
    6: "No Data",
    7: "Closed",
}


class TrafficClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _get_json(self, path: str) -> list | dict | int | float | str:
        url = f"{settings.trafik_base}/{path}"
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise IettApiError(f"Traffic API GET failed: {exc}") from exc

    async def get_traffic_index(self) -> TrafficIndex:
        """City-wide congestion percentage."""
        data = await self._get_json("TrafficIndex_Sc1_Cont")
        if isinstance(data, bool):
            raise IettApiError(f"Traffic API returned invalid index: {data!r}")
        if isinstance(data, (int, float, str)):
            try:
                raw_val = data.strip() if isinstance(data, str) else data
                percent = int(float(raw_val))
            except (TypeError, ValueError, OverflowError) as exc:
                raise IettApiError(f"Traffic API returned non-numeric index: {data!r}") from exc
        else:
            percent = 0
        level = min(7, max(1, round(percent / 16)))
        return TrafficIndex(percent=percent, description=_CONGESTION_LABELS.get(level, "Unknown"))

    async def get_traffic_segments(self) -> list[TrafficSegment]:
        """Per-road segment speeds and congestion levels."""
        data = await self._get_json("SegmentData")
        if not isinstance(data, list):
            return []
        result: list[TrafficSegment] = []
        for r in data:
            try:
                result.append(
                    TrafficSegment(
                        segment_id=int(r["S"]),
                        speed_kmh=int(r.get("V", 0)),
                        congestion=int(r.get("C", 6)),
                        timestamp=r.get("D", ""),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return result
