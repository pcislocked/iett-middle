"""Tests for OSRM helper and haversine utility."""
from __future__ import annotations

import sys
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aioresponses import aioresponses

from app.services.osrm import haversine, haversine_eta, osrm_route


@pytest.fixture()
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.ThreadedResolver() if sys.platform == "win32" else None
    )
    s = aiohttp.ClientSession(connector=connector)
    yield s
    await s.close()


class TestHaversine:
    def test_same_point_is_zero(self):
        assert haversine(41.0, 29.0, 41.0, 29.0) == 0.0

    def test_known_distance(self):
        # Approximate distance between two Istanbul stops ~1 km
        d = haversine(41.0883, 29.0508, 41.0950, 29.0550)
        assert 0.5 < d < 2.0

    def test_symmetry(self):
        a = haversine(41.0, 29.0, 41.1, 29.1)
        b = haversine(41.1, 29.1, 41.0, 29.0)
        assert abs(a - b) < 0.001


class TestHaversineEta:
    def test_moving_bus(self):
        result = haversine_eta(41.08, 29.05, 41.09, 29.06, speed_kmh=30.0)
        assert result["eta_minutes"] is not None
        assert result["eta_minutes"] > 0
        assert result["method"] == "haversine+speed"

    def test_stopped_bus(self):
        result = haversine_eta(41.08, 29.05, 41.09, 29.06, speed_kmh=0.0)
        assert result["eta_minutes"] is None

    def test_none_speed(self):
        result = haversine_eta(41.08, 29.05, 41.09, 29.06, speed_kmh=None)
        assert result["eta_minutes"] is None


_OSRM_PARAMS = {"overview": "full", "geometries": "geojson"}


class TestOsrmRoute:
    async def test_success_parses_route(self, session: aiohttp.ClientSession) -> None:
        from app.config import settings

        url = f"{settings.osrm_base}/route/v1/driving/29.0,41.0;29.01,41.01?overview=full&geometries=geojson"
        payload = {
            "routes": [{
                "distance": 5000,
                "duration": 600,
                "geometry": {"coordinates": [[29.0, 41.0], [29.01, 41.01]]},
            }]
        }
        with aioresponses() as m:
            m.get(url, payload=payload)  # type: ignore[reportUnknownMemberType]
            result = await osrm_route(session, 29.0, 41.0, 29.01, 41.01)

        assert result is not None
        assert result["distance_km"] == 5.0
        assert result["eta_minutes"] == 10.0
        assert result["method"] == "osrm"
        # coords are flipped from [lon, lat] → [lat, lon] for Leaflet
        assert result["geometry"] == [[41.0, 29.0], [41.01, 29.01]]

    async def test_non_200_returns_none(self, session: aiohttp.ClientSession) -> None:
        from app.config import settings

        url = f"{settings.osrm_base}/route/v1/driving/29.0,41.0;29.01,41.01?overview=full&geometries=geojson"
        with aioresponses() as m:
            m.get(url, status=503)  # type: ignore[reportUnknownMemberType]
            result = await osrm_route(session, 29.0, 41.0, 29.01, 41.01)

        assert result is None

    async def test_network_exception_returns_none(self, session: aiohttp.ClientSession) -> None:
        from app.config import settings

        url = f"{settings.osrm_base}/route/v1/driving/29.0,41.0;29.01,41.01?overview=full&geometries=geojson"
        with aioresponses() as m:
            m.get(url, exception=ConnectionError("OSRM unreachable"))  # type: ignore[reportUnknownMemberType]
            result = await osrm_route(session, 29.0, 41.0, 29.01, 41.01)

        assert result is None
