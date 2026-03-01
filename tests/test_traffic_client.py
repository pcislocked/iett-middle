"""Tests for app.services.traffic — TrafficClient HTTP calls + parsing."""
from __future__ import annotations

import sys
from collections.abc import AsyncIterator

import pytest
from aioresponses import aioresponses

from app.services.iett_client import IettApiError
from app.services.traffic import TrafficClient


@pytest.fixture()
async def tc() -> AsyncIterator[TrafficClient]:
    import aiohttp as _aiohttp
    connector = _aiohttp.TCPConnector(resolver=_aiohttp.ThreadedResolver() if sys.platform == "win32" else None)
    session = _aiohttp.ClientSession(connector=connector)
    yield TrafficClient(session)
    await session.close()


class TestTrafficClient:
    async def test_get_traffic_index_returns_percent_and_description(self, tc: TrafficClient) -> None:
        from app.config import settings
        url = f"{settings.trafik_base}/TrafficIndex_Sc1_Cont"
        with aioresponses() as m:
            m.get(url, payload=48)
            result = await tc.get_traffic_index()
        assert result.percent == 48
        assert result.description  # should map to a label string

    async def test_get_traffic_index_zero_percent(self, tc: TrafficClient) -> None:
        from app.config import settings
        url = f"{settings.trafik_base}/TrafficIndex_Sc1_Cont"
        with aioresponses() as m:
            m.get(url, payload=0)
            result = await tc.get_traffic_index()
        assert result.percent == 0

    async def test_get_traffic_index_raises_on_http_error(self, tc: TrafficClient) -> None:
        from app.config import settings
        url = f"{settings.trafik_base}/TrafficIndex_Sc1_Cont"
        with aioresponses() as m:
            m.get(url, status=503)
            with pytest.raises(IettApiError):
                await tc.get_traffic_index()

    async def test_get_traffic_segments_parses_list(self, tc: TrafficClient) -> None:
        from app.config import settings
        url = f"{settings.trafik_base}/SegmentData"
        segment_data = [
            {"S": "1001", "V": "45", "C": "3", "D": "2026-03-02T01:00:00"},
            {"S": "1002", "V": "20", "C": "5", "D": "2026-03-02T01:00:00"},
        ]
        with aioresponses() as m:
            m.get(url, payload=segment_data)
            result = await tc.get_traffic_segments()
        assert len(result) == 2
        assert result[0].segment_id == 1001
        assert result[0].speed_kmh == 45
        assert result[0].congestion == 3
        assert result[1].segment_id == 1002

    async def test_get_traffic_segments_returns_empty_on_non_list(self, tc: TrafficClient) -> None:
        from app.config import settings
        url = f"{settings.trafik_base}/SegmentData"
        with aioresponses() as m:
            m.get(url, payload={"error": "unavailable"})
            result = await tc.get_traffic_segments()
        assert result == []

    async def test_get_traffic_segments_skips_malformed_entry(self, tc: TrafficClient) -> None:
        from app.config import settings
        url = f"{settings.trafik_base}/SegmentData"
        segment_data = [
            {"S": "not-an-int", "V": "xx", "C": "y"},  # malformed
            {"S": "2001", "V": "60", "C": "2", "D": "ts"},
        ]
        with aioresponses() as m:
            m.get(url, payload=segment_data)
            result = await tc.get_traffic_segments()
        # Malformed entry is skipped; valid one retained
        assert len(result) == 1
        assert result[0].segment_id == 2001

    async def test_get_traffic_segments_raises_on_http_error(self, tc: TrafficClient) -> None:
        from app.config import settings
        url = f"{settings.trafik_base}/SegmentData"
        with aioresponses() as m:
            m.get(url, status=500)
            with pytest.raises(IettApiError):
                await tc.get_traffic_segments()

    async def test_congestion_labels_mapping(self, tc: TrafficClient) -> None:
        """Different percent values map to expected congestion description labels."""
        from app.config import settings
        url = f"{settings.trafik_base}/TrafficIndex_Sc1_Cont"
        # 48% → level ~3 → "Moderate"
        with aioresponses() as m:
            m.get(url, payload=48)
            result = await tc.get_traffic_index()
        assert result.percent == 48
        assert result.description == "Moderate"
