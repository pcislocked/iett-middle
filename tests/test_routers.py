"""Router integration tests using FastAPI TestClient + dependency overrides.

All external I/O is patched (IettClient methods, deps store functions).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


# ---------------------------------------------------------------------------
# Helpers — build minimal model dicts that routers will serialise
# ---------------------------------------------------------------------------

from app.models.bus import Arrival, BusPosition
from app.models.garage import Garage
from app.models.route import Announcement, RouteMetadata, RouteSearchResult, ScheduledDeparture
from app.models.stop import StopDetail, StopSearchResult


def _bus(kapino: str = "A-001", route_code: str = "500T") -> dict[str, Any]:
    return {
        "kapino": kapino,
        "plate": "34 HO 1000",
        "route_code": route_code,
        "latitude": 41.05,
        "longitude": 29.01,
        "speed": 0,
        "operator": None,
        "last_seen": "00:19:57",
        "route_name": "TUZLA - LEVENT",
        "nearest_stop": "301341",
        "direction": "D",
    }


def _arrival(route_code: str = "500T", eta: int = 3) -> Arrival:
    return Arrival(
        route_code=route_code,
        destination="4.LEVENT METRO",
        eta_minutes=eta,
        eta_raw=f"{eta} dk",
        plate=None,
        kapino=None,
    )


def _stop_search() -> StopSearchResult:
    return StopSearchResult(dcode="220602", name="AHMET MİTHAT EFENDİ", path=None)


def _nearby_stop() -> dict[str, Any]:
    return {
        "stop_code": "301341",
        "stop_name": "4.LEVENT METRO",
        "latitude": 41.0842,
        "longitude": 29.0073,
        "district": "Şişli",
        "distance_m": 120.0,
    }


def _route_meta() -> RouteMetadata:
    return RouteMetadata(
        direction_name="KADIKÖY - TAKSİM",
        full_name="500T - KADIKÖY - TAKSİM - Gidiş",
        variant_code="500T_D_D0",
        direction=0,
        depar_no=1,
    )


def _stop_detail() -> StopDetail:
    return StopDetail(
        dcode="220602",
        name="AHMET MİTHAT EFENDİ",
        latitude=41.1234,
        longitude=29.0871,
    )


def _garage() -> Garage:
    return Garage(code="IKT", name="IKITELLI GARAJ", latitude=41.062, longitude=28.798)


# ---------------------------------------------------------------------------
# Shared client fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ===========================  /v1/fleet  ===================================

class TestFleetRoot:
    def test_503_when_empty(self, client: TestClient) -> None:
        with patch("app.routers.fleet.get_fleet_snapshot", return_value=[]):
            resp = client.get("/v1/fleet")
        assert resp.status_code == 503

    def test_200_with_buses(self, client: TestClient) -> None:
        bus = _bus()
        with (
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]),
            patch("app.routers.fleet.get_trail", return_value=[]),
        ):
            resp = client.get("/v1/fleet")
        assert resp.status_code == 200
        data = resp.json()
        assert data[0]["kapino"] == "A-001"


class TestFleetMeta:
    def test_always_200(self, client: TestClient) -> None:
        with (
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[]),
            patch("app.routers.fleet.get_fleet_updated_at", return_value=None),
        ):
            resp = client.get("/v1/fleet/meta")
        assert resp.status_code == 200
        assert resp.json()["bus_count"] == 0

    def test_count_matches_snapshot(self, client: TestClient) -> None:
        now = datetime.now(timezone.utc)
        with (
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[_bus(), _bus("B-002")]),
            patch("app.routers.fleet.get_fleet_updated_at", return_value=now),
        ):
            resp = client.get("/v1/fleet/meta")
        assert resp.json()["bus_count"] == 2


class TestFleetRefresh:
    def test_202_accepted(self, client: TestClient) -> None:
        with patch("app.deps.get_session", return_value=MagicMock()):
            resp = client.post("/v1/fleet/refresh")
        assert resp.status_code == 202
        assert resp.json()["status"] == "refresh triggered"


class TestFleetKapino:
    def test_200_when_found(self, client: TestClient) -> None:
        bus = _bus("A-001")
        with (
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]),
            patch("app.routers.fleet.get_trail", return_value=[]),
        ):
            resp = client.get("/v1/fleet/A-001")
        assert resp.status_code == 200
        assert resp.json()["kapino"] == "A-001"

    def test_404_when_missing(self, client: TestClient) -> None:
        with patch("app.routers.fleet.get_fleet_snapshot", return_value=[]):
            resp = client.get("/v1/fleet/NOTEXIST")
        assert resp.status_code == 404


# ===========================  /v1/stops  ===================================

class TestStopsSearch:
    def test_returns_results(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.search_stops = AsyncMock(return_value=[_stop_search()])
        with (
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.stops.cache_set", AsyncMock()),
            patch("app.routers.stops.get_session", return_value=MagicMock()),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/search?q=ahmet")
        assert resp.status_code == 200

    def test_422_when_q_too_short(self, client: TestClient) -> None:
        resp = client.get("/v1/stops/search?q=a")
        assert resp.status_code == 422


class TestStopsNearby:
    def test_503_when_index_not_ready(self, client: TestClient) -> None:
        with patch("app.deps.get_stop_index_updated_at", return_value=None):
            resp = client.get("/v1/stops/nearby?lat=41.0&lon=29.0")
        assert resp.status_code == 503

    def test_200_when_index_ready(self, client: TestClient) -> None:
        now = datetime.now(timezone.utc)
        with (
            patch("app.deps.get_stop_index_updated_at", return_value=now),
            patch("app.deps.get_nearby_stops", return_value=[_nearby_stop()]),
        ):
            resp = client.get("/v1/stops/nearby?lat=41.0842&lon=29.0073")
        assert resp.status_code == 200
        assert resp.json()[0]["stop_code"] == "301341"

    def test_missing_lat_lon_returns_422(self, client: TestClient) -> None:
        resp = client.get("/v1/stops/nearby")
        assert resp.status_code == 422


class TestStopArrivals:
    def test_200_with_arrivals(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_stop_arrivals = AsyncMock(return_value=[_arrival()])
        with (
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.stops.cache_set", AsyncMock()),
            patch("app.routers.stops.get_session", return_value=MagicMock()),
            patch("app.routers.stops.IettClient", return_value=mock_client),
            patch("app.routers.stops.get_buses_near_stop", return_value=[]),
        ):
            resp = client.get("/v1/stops/220602/arrivals")
        assert resp.status_code == 200

    def test_empty_list_on_empty_arrivals(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_stop_arrivals = AsyncMock(return_value=[])
        with (
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.stops.cache_set", AsyncMock()),
            patch("app.routers.stops.get_session", return_value=MagicMock()),
            patch("app.routers.stops.IettClient", return_value=mock_client),
            patch("app.routers.stops.get_buses_near_stop", return_value=[]),
        ):
            resp = client.get("/v1/stops/220602/arrivals")
        assert resp.status_code == 200
        assert resp.json() == []


class TestStopDetail:
    def test_200_on_found(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_stop_detail = AsyncMock(return_value=_stop_detail())
        with (
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.stops.cache_set", AsyncMock()),
            patch("app.routers.stops.get_session", return_value=MagicMock()),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/220602")
        assert resp.status_code == 200

    def test_404_when_not_found(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_stop_detail = AsyncMock(return_value=None)
        with (
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.stops.cache_set", AsyncMock()),
            patch("app.routers.stops.get_session", return_value=MagicMock()),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/000000")
        assert resp.status_code == 404


# ===========================  /v1/routes  ===================================

class TestRoutesSearch:
    def test_200_with_results(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.search_routes = AsyncMock(return_value=[RouteSearchResult(hat_kodu="500T", name="TUZLA - LEVENT")])
        with (
            patch("app.routers.routes.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.routes.cache_set", AsyncMock()),
            patch("app.routers.routes.get_session", return_value=MagicMock()),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/search?q=500T")
        assert resp.status_code == 200

    def test_422_when_q_missing(self, client: TestClient) -> None:
        resp = client.get("/v1/routes/search")
        assert resp.status_code == 422


class TestRoutesMeta:
    def test_200_with_metadata(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_route_metadata = AsyncMock(return_value=[_route_meta()])
        with (
            patch("app.routers.routes.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.routes.cache_set", AsyncMock()),
            patch("app.routers.routes.get_session", return_value=MagicMock()),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T")
        assert resp.status_code == 200


class TestRoutesBuses:
    def test_200_with_buses(self, client: TestClient) -> None:
        bus = BusPosition(**_bus())
        mock_client = MagicMock()
        mock_client.get_route_buses = AsyncMock(return_value=[bus])
        with (
            patch("app.routers.routes.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.routes.cache_set", AsyncMock()),
            patch("app.routers.routes.get_session", return_value=MagicMock()),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/buses")
        assert resp.status_code == 200


class TestRoutesSchedule:
    def test_200_with_departures(self, client: TestClient) -> None:
        dep = ScheduledDeparture(
            route_code="500T", route_name="TUZLA - LEVENT",
            route_variant="500T_D_D0", direction="D",
            day_type="H", service_type="ÖHO", departure_time="05:55",
        )
        mock_client = MagicMock()
        mock_client.get_route_schedule = AsyncMock(return_value=[dep])
        with (
            patch("app.routers.routes.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.routes.cache_set", AsyncMock()),
            patch("app.routers.routes.get_session", return_value=MagicMock()),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/schedule")
        assert resp.status_code == 200


class TestRoutesAnnouncements:
    def test_200_with_announcements(self, client: TestClient) -> None:
        ann = Announcement(
            route_code="500T", route_name="TUZLA - LEVENT",
            type="Günlük", updated_at="09:00", message="Test",
        )
        mock_client = MagicMock()
        mock_client.get_announcements = AsyncMock(return_value=[ann])
        with (
            patch("app.routers.routes.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.routes.cache_set", AsyncMock()),
            patch("app.routers.routes.get_session", return_value=MagicMock()),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/announcements")
        assert resp.status_code == 200


# ===========================  /v1/garages  ==================================

class TestGaragesList:
    def test_200_returns_garages(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_garages = AsyncMock(return_value=[_garage()])
        with (
            patch("app.routers.garages.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.garages.cache_set", AsyncMock()),
            patch("app.routers.garages.get_session", return_value=MagicMock()),
            patch("app.routers.garages.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/garages")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) >= 1

    def test_returns_empty_list_when_no_garages(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_garages = AsyncMock(return_value=[])
        with (
            patch("app.routers.garages.cache_get", AsyncMock(return_value=None)),
            patch("app.routers.garages.cache_set", AsyncMock()),
            patch("app.routers.garages.get_session", return_value=MagicMock()),
            patch("app.routers.garages.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/garages")
        assert resp.status_code == 200
        assert resp.json() == []
