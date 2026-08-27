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
# Helpers â€” build minimal model dicts that routers will serialise
# ---------------------------------------------------------------------------
from app.models.bus import Arrival, BusPosition
from app.models.garage import Garage
from app.models.route import (
    Announcement,
    RouteMetadata,
    RouteSearchResult,
    ScheduledDeparture,
)
from app.models.stop import RouteStop, StopDetail, StopSearchResult
from app.models.traffic import TrafficIndex, TrafficSegment
from app.services.iett_client import IettApiError
from app.services.ntcapi_client import NtcApiError

# Convenience: AsyncMock that raises NtcApiError (simulates ntcapi unavailable)
_NTCAPI_DOWN = AsyncMock(side_effect=NtcApiError("test: ntcapi unavailable"))


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
    return StopSearchResult(dcode="220602", name="AHMET MÄ°THAT EFENDÄ°", path=None)


def _nearby_stop() -> dict[str, Any]:
    return {
        "stop_code": "301341",
        "stop_name": "4.LEVENT METRO",
        "latitude": 41.0842,
        "longitude": 29.0073,
        "district": "ÅžiÅŸli",
        "distance_m": 120.0,
    }


def _route_meta() -> RouteMetadata:
    return RouteMetadata(
        direction_name="KADIKÃ–Y - TAKSÄ°M",
        full_name="500T - KADIKÃ–Y - TAKSÄ°M - GidiÅŸ",
        variant_code="500T_D_D0",
        direction=0,
        depar_no=1,
    )


def _stop_detail() -> StopDetail:
    return StopDetail(
        dcode="220602",
        name="AHMET MÄ°THAT EFENDÄ°",
        latitude=41.1234,
        longitude=29.0871,
    )


def _garage() -> Garage:
    return Garage(code="IKT", name="IKITELLI GARAJ", latitude=41.062, longitude=28.798)


def _arac_bus(kapino: str = "C-1753") -> dict[str, Any]:
    return {
        "kapino": kapino,
        "plate": "34 HO 1753",
        "latitude": 41.01,
        "longitude": 29.02,
        "speed": 12,
        "operator": "Istanbul Halk Ulasim",
        "last_seen": "18-04-2026 00:16:56",
        "route_code": "14R",
        "direction": None,
        "direction_letter": "G",
        "nearest_stop": None,
        "stop_sequence": None,
        "operator_id": 5,
        "operator_name": "Istanbul Halk Ulasim",
        "vehicle_brand": "MERCEDES CONECTO",
        "model_year": 2015,
        "vehicle_type": "Solo -12m",
        "seating_capacity": 27,
        "full_capacity": 96,
        "accessible": True,
        "has_usb": True,
        "has_wifi": False,
        "has_bicycle_rack": False,
        "is_air_conditioned": None,
        "garage_code": None,
        "garage_name": None,
        "vehicle_software_version": 2,
    }


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
            patch(
                "app.routers.fleet.get_fleet_snapshot",
                return_value=[_bus(), _bus("B-002")],
            ),
            patch("app.routers.fleet.get_fleet_updated_at", return_value=now),
        ):
            resp = client.get("/v1/fleet/meta")
        assert resp.json()["bus_count"] == 2


class TestFleetRefresh:
    def test_202_manual_refresh_queued(self, client: TestClient) -> None:
        """Manual fleet refresh queues an immediate background refresh."""
        with (
            patch("app.config.settings.fleet_manual_refresh_cooldown", 0),
            patch("app.routers.fleet._manual_refresh_last_triggered", 0.0),
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()) as mocked_refresh,
        ):
            resp = client.post("/v1/fleet/refresh")
        assert resp.status_code == 202
        assert resp.json() == {"status": "queued"}
        mocked_refresh.assert_awaited_once_with(max_age_seconds=0)

    def test_202_manual_refresh_cooldown(self, client: TestClient) -> None:
        """Second refresh within cooldown returns status=cooldown and retry hint."""
        with (
            patch("app.config.settings.fleet_manual_refresh_cooldown", 600),
            patch("app.routers.fleet._manual_refresh_last_triggered", 0.0),
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()) as mocked_refresh,
        ):
            first = client.post("/v1/fleet/refresh")
            second = client.post("/v1/fleet/refresh")

        assert first.status_code == 202
        assert first.json() == {"status": "queued"}
        assert second.status_code == 202
        body = second.json()
        assert body["status"] == "cooldown"
        assert isinstance(body["retry_after_seconds"], int)
        assert body["retry_after_seconds"] > 0
        mocked_refresh.assert_awaited_once_with(max_age_seconds=0)


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


class TestFleetDetailRouter:
    """Tests for GET /v1/fleet/{kapino}/detail"""

    def test_404_when_kapino_not_in_fleet(self, client: TestClient) -> None:
        with (
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()),
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[]),
        ):
            resp = client.get("/v1/fleet/GHOST/detail")
        assert resp.status_code == 404

    def test_200_live_route_sets_route_is_live_true(self, client: TestClient) -> None:
        bus = _bus("A-001", "500T")
        with (
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()),
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]),
            patch("app.routers.fleet.get_trail", return_value=[]),
            patch("app.routers.fleet.get_session", return_value=MagicMock(), create=True),
            patch(
                "app.services.cache._cache_get_internal",
                AsyncMock(return_value=([], True)),
            ),
            patch("app.services.cache.cache_set", AsyncMock()),
        ):
            resp = client.get("/v1/fleet/A-001/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["route_is_live"] is True
        assert body["resolved_route_code"] == "500T"
        assert body["kapino"] == "A-001"

    def test_200_parked_bus_uses_last_known_route(self, client: TestClient) -> None:
        """Bus with null live route_code falls back to _kapino_last_route."""
        bus = _bus("A-001", None)  # type: ignore[arg-type]
        with (
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()),
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]),
            patch("app.routers.fleet.get_trail", return_value=[]),
            patch("app.routers.fleet.get_last_route_by_kapino", return_value="15F"),
            patch("app.routers.fleet.get_session", return_value=MagicMock(), create=True),
            patch(
                "app.services.cache._cache_get_internal",
                AsyncMock(return_value=([], True)),
            ),
            patch("app.services.cache.cache_set", AsyncMock()),
        ):
            resp = client.get("/v1/fleet/A-001/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["route_is_live"] is False
        assert body["resolved_route_code"] == "15F"

    def test_200_route_stops_returned_from_cache(self, client: TestClient) -> None:
        """route_stops field is populated from cache when available."""
        bus = _bus("A-001", "500T")
        cached_stops = [
            {
                "route_code": "500T",
                "direction": "G",
                "sequence": 1,
                "stop_code": "301341",
                "stop_name": "Levent",
                "latitude": 41.08,
                "longitude": 29.01,
                "district": None,
            }
        ]
        with (
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()),
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]),
            patch("app.routers.fleet.get_trail", return_value=[]),
            patch("app.routers.fleet.get_session", return_value=MagicMock(), create=True),
            patch(
                "app.services.cache._cache_get_internal",
                AsyncMock(return_value=(cached_stops, True)),
            ),
            patch("app.services.cache.cache_set", AsyncMock()),
        ):
            resp = client.get("/v1/fleet/A-001/detail")
        assert resp.status_code == 200
        assert resp.json()["route_stops"] == cached_stops

    def test_200_no_route_code_returns_empty_stops(self, client: TestClient) -> None:
        """Bus with no live or last-known route_code â†’ route_stops is empty, route_is_live False."""
        bus = _bus("A-001", None)  # type: ignore[arg-type]
        with (
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()),
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]),
            patch("app.routers.fleet.get_trail", return_value=[]),
            patch("app.routers.fleet.get_last_route_by_kapino", return_value=None),
            patch("app.routers.fleet.get_session", return_value=MagicMock(), create=True),
            patch("app.services.cache._cache_get_internal", AsyncMock(return_value=None)),
            patch("app.services.cache.cache_set", AsyncMock()),
        ):
            resp = client.get("/v1/fleet/A-001/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["route_is_live"] is False
        assert body["resolved_route_code"] is None
        assert body["route_stops"] == []


# ===========================  /v1/stops  ===================================


class TestStopsSearch:
    def test_returns_results(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.search_stops = AsyncMock(return_value=[_stop_search()])
        with (
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/search?q=ahmet")
        assert resp.status_code == 200

    def test_422_when_q_too_short(self, client: TestClient) -> None:
        resp = client.get("/v1/stops/search?q=a")
        assert resp.status_code == 422


class TestStopsNearby:
    def test_503_when_index_not_ready(self, client: TestClient) -> None:
        # ntcapi fails â†’ fallback to index â†’ index not ready â†’ 503
        with (
            patch("app.routers.stops.ntcapi_client.get_nearby_stops", _NTCAPI_DOWN),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.deps.get_stop_index_updated_at", return_value=None),
        ):
            resp = client.get("/v1/stops/nearby?lat=41.0&lon=29.0")
        assert resp.status_code == 503

    def test_200_when_index_ready(self, client: TestClient) -> None:
        now = datetime.now(timezone.utc)
        # ntcapi fails â†’ fallback to in-memory index â†’ returns results
        with (
            patch("app.routers.stops.ntcapi_client.get_nearby_stops", _NTCAPI_DOWN),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.deps.get_stop_index_updated_at", return_value=now),
            patch("app.deps.get_nearby_stops", return_value=[_nearby_stop()]),
        ):
            resp = client.get("/v1/stops/nearby?lat=41.0842&lon=29.0073")
        assert resp.status_code == 200
        assert resp.json()[0]["stop_code"] == "301341"

    def test_missing_lat_lon_returns_422(self, client: TestClient) -> None:
        resp = client.get("/v1/stops/nearby")
        assert resp.status_code == 422

    def test_haversine_used_when_ntcapi_gives_no_distance(self, client: TestClient) -> None:
        """When ntcapi returns a stop with distance_m=None, haversine computes a positive distance."""
        normalised = {
            "stop_code": "301341",
            "stop_name": "4.LEVENT METRO",
            "lat": 41.0842,
            "lon": 29.0073,
            "district": "ÅžiÅŸli",
            "direction": None,
            "distance_m": None,  # ntcapi didn't provide distance
        }
        with (
            patch(
                "app.routers.stops.ntcapi_client.get_nearby_stops",
                AsyncMock(return_value=[{"raw": "stop"}]),
            ),
            patch(
                "app.services.normalizers.stops.from_ntcapi_nearby_processed",
                return_value=normalised,
            ),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
        ):
            resp = client.get("/v1/stops/nearby?lat=41.0&lon=29.0")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["distance_m"] > 0, "haversine should compute a positive distance, not 0"


class TestHaversine:
    """Unit tests for the _haversine_m pure function."""

    def test_positive_distance_for_distinct_points(self) -> None:
        from app.routers.stops import _haversine_m

        d = _haversine_m(41.0, 29.0, 41.0842, 29.0073)
        assert d > 0

    def test_zero_distance_for_same_point(self) -> None:
        from app.routers.stops import _haversine_m

        d = _haversine_m(41.0, 29.0, 41.0, 29.0)
        assert d == pytest.approx(0.0, abs=0.01)

    def test_known_distance_roughly_correct(self) -> None:
        """0.004Â° north at Istanbul latitude â‰ˆ 445 m."""
        from app.routers.stops import _haversine_m

        d = _haversine_m(41.0, 29.0, 41.004, 29.0)
        assert 400 < d < 500, f"Expected ~445 m, got {d:.1f} m"


class TestStopArrivals:
    def test_200_with_arrivals(self, client: TestClient) -> None:
        # ntcapi down â†’ fallback to IETT HTML
        mock_client = MagicMock()
        mock_client.get_stop_arrivals = AsyncMock(return_value=[_arrival()])
        with (
            patch("app.routers.stops.ntcapi_client.get_stop_arrivals", _NTCAPI_DOWN),
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_client),
            patch("app.routers.stops.get_plate_by_kapino", return_value=None),
        ):
            resp = client.get("/v1/stops/220602/arrivals")
        assert resp.status_code == 200

    def test_empty_list_on_empty_arrivals(self, client: TestClient) -> None:
        # ntcapi down â†’ fallback to IETT HTML â†’ empty
        mock_client = MagicMock()
        mock_client.get_stop_arrivals = AsyncMock(return_value=[])
        with (
            patch("app.routers.stops.ntcapi_client.get_stop_arrivals", _NTCAPI_DOWN),
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_client),
            patch("app.routers.stops.get_plate_by_kapino", return_value=None),
        ):
            resp = client.get("/v1/stops/220602/arrivals")
        assert resp.status_code == 200
        assert resp.json() == []


class TestStopDetail:
    def test_200_on_found(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_stop_detail = AsyncMock(return_value=_stop_detail())
        with (
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/220602")
        assert resp.status_code == 200

    def test_404_when_not_found(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_stop_detail = AsyncMock(return_value=None)
        with (
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/000000")
        assert resp.status_code == 404


# ===========================  /v1/routes  ===================================


class TestRoutesSearch:
    def test_200_with_results(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.search_routes = AsyncMock(
            return_value=[RouteSearchResult(hat_kodu="500T", name="TUZLA - LEVENT")]
        )
        with (
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/search?q=500T")
        assert resp.status_code == 200

    def test_422_when_q_missing(self, client: TestClient) -> None:
        resp = client.get("/v1/routes/search")
        assert resp.status_code == 422


class TestRoutesMeta:
    def test_200_with_metadata(self, client: TestClient) -> None:
        # ntcapi down â†’ fallback to IETT SOAP
        mock_client = MagicMock()
        mock_client.get_route_metadata = AsyncMock(return_value=[_route_meta()])
        with (
            patch("app.routers.routes.ntcapi_client.get_route_metadata", _NTCAPI_DOWN),
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
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
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/buses")
        assert resp.status_code == 200


class TestRoutesSchedule:
    def test_200_with_departures(self, client: TestClient) -> None:
        dep = ScheduledDeparture(
            route_code="500T",
            route_name="TUZLA - LEVENT",
            route_variant="500T_D_D0",
            direction="D",
            day_type="H",
            service_type="Ã–HO",
            departure_time="05:55",
        )
        # ntcapi down â†’ fallback to IETT SOAP
        mock_client = MagicMock()
        mock_client.get_route_schedule = AsyncMock(return_value=[dep])
        with (
            patch("app.routers.routes.ntcapi_client.get_timetable", _NTCAPI_DOWN),
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/schedule")
        assert resp.status_code == 200


class TestRoutesAnnouncements:
    def test_200_with_announcements(self, client: TestClient) -> None:
        ann = Announcement(
            route_code="500T",
            route_name="TUZLA - LEVENT",
            type="GÃ¼nlÃ¼k",
            updated_at="09:00",
            message="Test",
        )
        mock_client = MagicMock()
        mock_client.get_announcements = AsyncMock(return_value=[ann])
        with (
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/announcements")
        assert resp.status_code == 200


class TestRoutesBatchAnnouncements:
    def test_200_with_multiple_routes(self, client: TestClient) -> None:
        ann1 = Announcement(
            route_code="500T",
            route_name="TUZLA",
            type="G",
            updated_at="09",
            message="M1",
        )
        ann2 = Announcement(
            route_code="15F",
            route_name="BEYKOZ",
            type="G",
            updated_at="09",
            message="M2",
        )
        ann3 = Announcement(
            route_code="11US",
            route_name="USKUDAR",
            type="G",
            updated_at="09",
            message="M3",
        )
        mock_client = MagicMock()
        mock_client.get_announcements = AsyncMock(return_value=[ann1, ann2, ann3])
        with (
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/announcements/batch?routes=500T,15F,15F")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        # Verify deduplication of 15F and exclusion of 11US
        codes = [a["route_code"] for a in body]
        assert "500T" in codes
        assert "15F" in codes
        assert "11US" not in codes

    def test_empty_whitespace_route_parameter(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_announcements = AsyncMock(return_value=[])
        with (
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/announcements/batch?routes=,,,")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_negative_caching_on_iett_error(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_announcements = AsyncMock(side_effect=IettApiError("API Error"))

        mock_cache_set = AsyncMock()
        with (
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", mock_cache_set, create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/announcements/batch?routes=500T")

        assert resp.status_code == 200
        assert resp.json() == []

        # Verify negative cache was set with 60s TTL
        mock_cache_set.assert_called_once()
        args = mock_cache_set.call_args
        assert args[0][0] == "routes:announcements:global"
        assert args[0][1] == []
        assert args[0][2] == 60


# ===========================  /v1/garages  ==================================


class TestGaragesList:
    def test_200_returns_garages(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_garages = AsyncMock(return_value=[_garage()])
        with (
            patch(
                "app.routers.garages.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.garages.cache_set", AsyncMock(), create=True),
            patch("app.routers.garages.get_session", return_value=MagicMock(), create=True),
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
            patch(
                "app.routers.garages.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.garages.cache_set", AsyncMock(), create=True),
            patch("app.routers.garages.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.garages.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/garages")
        assert resp.status_code == 200
        assert resp.json() == []


# ===========================  /v1/traffic  ==================================


class TestTrafficIndex:
    def test_200_returns_traffic_index(self, client: TestClient) -> None:
        mock_tc = MagicMock()
        mock_tc.get_traffic_index = AsyncMock(
            return_value=TrafficIndex(percent=45, description="Moderate")
        )
        with (
            patch(
                "app.routers.traffic.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.traffic.cache_set", AsyncMock(), create=True),
            patch("app.routers.traffic.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.traffic.TrafficClient", return_value=mock_tc),
        ):
            resp = client.get("/v1/traffic/index")
        assert resp.status_code == 200
        body = resp.json()
        assert body["percent"] == 45
        assert body["description"] == "Moderate"

    def test_200_returns_cached_index(self, client: TestClient) -> None:
        cached = {"percent": 30, "description": "Open"}
        with patch("app.routers.traffic.cache_get", AsyncMock(return_value=cached)):
            resp = client.get("/v1/traffic/index")
        assert resp.status_code == 200
        assert resp.json()["percent"] == 30

    def test_502_when_traffic_api_fails(self, client: TestClient) -> None:
        from app.services.iett_client import IettApiError

        mock_tc = MagicMock()
        mock_tc.get_traffic_index = AsyncMock(side_effect=IettApiError("timeout"))
        with (
            patch(
                "app.routers.traffic.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.traffic.cache_set", AsyncMock(), create=True),
            patch("app.routers.traffic.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.traffic.TrafficClient", return_value=mock_tc),
        ):
            resp = client.get("/v1/traffic/index")
        assert resp.status_code == 502


class TestTrafficSegments:
    def test_200_returns_segments(self, client: TestClient) -> None:
        seg = TrafficSegment(
            segment_id=1, speed_kmh=40, congestion=3, timestamp="2026-03-02T01:00:00"
        )
        mock_tc = MagicMock()
        mock_tc.get_traffic_segments = AsyncMock(return_value=[seg])
        with (
            patch(
                "app.routers.traffic.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.traffic.cache_set", AsyncMock(), create=True),
            patch("app.routers.traffic.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.traffic.TrafficClient", return_value=mock_tc),
        ):
            resp = client.get("/v1/traffic/segments")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["segment_id"] == 1
        assert body[0]["speed_kmh"] == 40

    def test_200_returns_from_cache(self, client: TestClient) -> None:
        cached = [{"segment_id": 99, "speed_kmh": 60, "congestion": 2, "timestamp": "t"}]
        with patch("app.routers.traffic.cache_get", AsyncMock(return_value=cached)):
            resp = client.get("/v1/traffic/segments")
        assert resp.status_code == 200
        assert resp.json()[0]["segment_id"] == 99

    def test_502_when_traffic_api_fails(self, client: TestClient) -> None:
        from app.services.iett_client import IettApiError

        mock_tc = MagicMock()
        mock_tc.get_traffic_segments = AsyncMock(side_effect=IettApiError("down"))
        with (
            patch(
                "app.routers.traffic.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.traffic.cache_set", AsyncMock(), create=True),
            patch("app.routers.traffic.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.traffic.TrafficClient", return_value=mock_tc),
        ):
            resp = client.get("/v1/traffic/segments")
        assert resp.status_code == 502


# ===========================  extra /v1/routes  =============================


class TestRoutesBusesFallbacks:
    """Test the secondary SOAP and fleet fallbacks for route buses."""

    def test_200_soap_fallback_when_ybs_fails(self, client: TestClient) -> None:
        bus = BusPosition(**_bus())
        mock_client = MagicMock()
        mock_client.get_route_buses = AsyncMock(return_value=[bus])
        with (
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.ntcapi_client.get_route_metadata", _NTCAPI_DOWN),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/buses")
        assert resp.status_code == 200
        assert resp.json()[0]["kapino"] == "A-001"

    def test_200_fleet_fallback_when_all_external_fail(self, client: TestClient) -> None:
        """Both ntcapi and SOAP fail â†’ falls back to in-memory fleet."""
        from app.services.iett_client import IettApiError

        mock_client = MagicMock()
        mock_client.get_route_buses = AsyncMock(side_effect=IettApiError("soap down"))
        with (
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.ntcapi_client.get_route_metadata", _NTCAPI_DOWN),
            patch("app.routers.routes.IettClient", return_value=mock_client),
            patch("app.deps.ensure_fleet_fresh", AsyncMock()),
            patch("app.deps.get_buses_by_route", return_value=[_bus()]),
        ):
            resp = client.get("/v1/routes/500T/buses")
        assert resp.status_code == 200


class TestRoutesStopsFallback:
    """Test SOAP fallback for route stops when ntcapi fails."""

    def test_200_soap_fallback_when_ntcapi_down(self, client: TestClient) -> None:
        rs = RouteStop(
            route_code="500T",
            direction="G",
            sequence=1,
            stop_code="301341",
            stop_name="LEVENT",
            latitude=41.08,
            longitude=29.01,
            district=None,
        )
        mock_client = MagicMock()
        mock_client.get_route_stops = AsyncMock(return_value=[rs])
        with (
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.ntcapi_client.get_route_stops", _NTCAPI_DOWN),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/stops")
        assert resp.status_code == 200
        assert resp.json()[0]["stop_code"] == "301341"

    def test_502_when_both_sources_fail(self, client: TestClient) -> None:
        from app.services.iett_client import IettApiError

        mock_client = MagicMock()
        mock_client.get_route_stops = AsyncMock(side_effect=IettApiError("down"))
        with (
            patch(
                "app.routers.routes.cache_get",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch("app.routers.routes.cache_set", AsyncMock(), create=True),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.routes.ntcapi_client.get_route_stops", _NTCAPI_DOWN),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/stops")
        assert resp.status_code == 502

    def test_200_returns_cached_stops(self, client: TestClient) -> None:
        cached = [
            {
                "route_code": "500T",
                "direction": "G",
                "sequence": 1,
                "stop_code": "301341",
                "stop_name": "LEVENT",
                "latitude": 41.08,
                "longitude": 29.01,
                "district": None,
            }
        ]
        with patch("app.routers.routes.cache_get_or_fetch", AsyncMock(return_value=cached)):
            resp = client.get("/v1/routes/500T/stops")
        assert resp.status_code == 200
        assert resp.json()[0]["stop_code"] == "301341"


# ===========================  extra /v1/stops  ==============================


class TestStopsExtra:
    """Additional stops router coverage: caching, routes endpoint, via filter."""

    def test_nearby_returns_from_ntcapi_when_available(self, client: TestClient) -> None:
        processed = [
            {
                "stop_code": "301341",
                "stop_name": "LEVENT",
                "lat": 41.0842,
                "lon": 29.0073,
                "district": "ÅžiÅŸli",
                "direction": None,
                "distance_m": 120.0,
            }
        ]
        with (
            patch(
                "app.routers.stops.ntcapi_client.get_nearby_stops",
                AsyncMock(return_value=[{"raw": "stop"}]),
            ),
            patch(
                "app.services.normalizers.stops.from_ntcapi_nearby_processed",
                return_value=processed[0],
            ),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
        ):
            resp = client.get("/v1/stops/nearby?lat=41.08&lon=29.01")
        assert resp.status_code == 200
        assert resp.json()[0]["stop_code"] == "301341"
        assert resp.json()[0]["distance_m"] == 120.0

    def test_get_routes_at_stop_200(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client.get_routes_at_stop = AsyncMock(return_value=["500T", "15F"])
        with (
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/301341/routes")
        assert resp.status_code == 200
        body = resp.json()
        assert "500T" in body

    def test_get_routes_at_stop_returns_cached(self, client: TestClient) -> None:
        with patch("app.routers.stops.cache_get_or_fetch", AsyncMock(return_value=["500T"])):
            resp = client.get("/v1/stops/301341/routes")
        assert resp.status_code == 200
        assert resp.json() == ["500T"]

    def test_arrivals_ntcapi_primary_path(self, client: TestClient) -> None:
        """Arrivals served from ntcapi ybs when available (no IETT fallback)."""
        raw = [
            {
                "route_code": "500T",
                "destination": "LEVENT",
                "eta_minutes": 3,
                "eta_raw": "3 dk",
                "plate": None,
                "kapino": "A-001",
                "lat": None,
                "lon": None,
                "speed_kmh": None,
                "last_seen_ts": None,
                "amenities": None,
            }
        ]
        canonical = [
            {
                "route_code": "500T",
                "destination": "LEVENT",
                "eta_minutes": 3,
                "eta_raw": "3 dk",
                "plate": None,
                "kapino": "A-001",
                "lat": None,
                "lon": None,
                "speed_kmh": None,
                "last_seen_ts": None,
                "amenities": None,
            }
        ]
        with (
            patch(
                "app.routers.stops.ntcapi_client.get_stop_arrivals",
                AsyncMock(return_value=raw),
            ),
            patch(
                "app.services.normalizers.arrivals.from_ntcapi_ybs",
                return_value=canonical[0],
            ),
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.get_plate_by_kapino", return_value=None),
        ):
            resp = client.get("/v1/stops/220602/arrivals")
        assert resp.status_code == 200

    def test_arrivals_ntcapi_parses_semicolon_son_konum(self, client: TestClient) -> None:
        raw = [
            {
                "hatkodu": "500T",
                "hattip": "4.LEVENT METRO",
                "dakika": "3",
                "saat": "3 dk",
                "kapino": "A-001",
                "son_konum": "29.0109;41.0819",
                "son_hiz": "25",
                "son_konum_saati": "2026-03-01 14:22:00",
                "usb": "1",
                "wifi": "0",
                "klima": "1",
                "engelli": "1",
            }
        ]
        with (
            patch(
                "app.routers.stops.ntcapi_client.get_stop_arrivals",
                AsyncMock(return_value=raw),
            ),
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.get_plate_by_kapino", return_value="34 HO 1000"),
        ):
            resp = client.get("/v1/stops/220602/arrivals")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["lat"] == pytest.approx(41.0819)
        assert body[0]["lon"] == pytest.approx(29.0109)
        assert body[0]["plate"] == "34 HO 1000"

    def test_arrivals_returns_cached(self, client: TestClient) -> None:
        cached = [
            {
                "route_code": "500T",
                "destination": "LEVENT",
                "eta_minutes": 5,
                "eta_raw": "5 dk",
                "plate": None,
                "kapino": None,
                "lat": None,
                "lon": None,
                "speed_kmh": None,
                "last_seen_ts": None,
                "amenities": None,
            }
        ]
        with (
            patch("app.routers.stops.cache_get_or_fetch", AsyncMock(return_value=cached)),
            patch("app.routers.stops.get_plate_by_kapino", return_value=None),
        ):
            resp = client.get("/v1/stops/220602/arrivals")
        assert resp.status_code == 200
        assert resp.json()[0]["eta_minutes"] == 5


# ===========================  extra /v1/fleet detail  =======================


class TestFleetDetailFallbacks:
    """Cover the ntcapi fetch + IettClient SOAP fallback paths in /detail."""

    def test_detail_fetches_stops_via_ntcapi_when_cache_miss(self, client: TestClient) -> None:
        bus = _bus("A-001", "500T")
        processed = {
            "route_code": "500T",
            "direction": "G",
            "sequence": 1,
            "stop_code": "301341",
            "stop_name": "LEVENT",
            "lat": 41.08,
            "lon": 29.01,
            "district": None,
        }
        with (
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()),
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]),
            patch("app.routers.fleet.get_trail", return_value=[]),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.services.cache._cache_get_internal", AsyncMock(return_value=None)),
            patch("app.services.cache.cache_set", AsyncMock()),
            patch(
                "app.services.ntcapi_client.get_route_stops",
                AsyncMock(return_value=[{"raw": "x"}]),
            ),
            patch(
                "app.services.normalizers.route_stops.from_ntcapi_route_processed",
                return_value=processed,
            ),
        ):
            resp = client.get("/v1/fleet/A-001/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert body["route_is_live"] is True
        # route_stops should be populated from the ntcapi fetch
        assert isinstance(body["route_stops"], list)

    def test_detail_falls_back_to_iett_soap_when_ntcapi_fails(self, client: TestClient) -> None:
        from app.services.ntcapi_client import NtcApiError as NE

        bus = _bus("A-001", "500T")
        rs = RouteStop(
            route_code="500T",
            direction="G",
            sequence=1,
            stop_code="301341",
            stop_name="LEVENT",
            latitude=41.08,
            longitude=29.01,
            district=None,
        )
        mock_iett = MagicMock()
        mock_iett.get_route_stops = AsyncMock(return_value=[rs])
        with (
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()),
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]),
            patch("app.routers.fleet.get_trail", return_value=[]),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.services.cache._cache_get_internal", AsyncMock(return_value=None)),
            patch("app.services.cache.cache_set", AsyncMock()),
            patch(
                "app.services.ntcapi_client.get_route_stops",
                AsyncMock(side_effect=NE("down")),
            ),
            patch("app.routers.routes.IettClient", return_value=mock_iett),
        ):
            resp = client.get("/v1/fleet/A-001/detail")
        assert resp.status_code == 200
        body = resp.json()
        # Fell back to IETT SOAP â†’ should still return route_stops
        assert len(body["route_stops"]) == 1
        assert body["route_stops"][0]["stop_code"] == "301341"

    def test_detail_returns_empty_stops_when_all_sources_fail(self, client: TestClient) -> None:
        """Both ntcapi and IETT SOAP fail â†’ 200 with empty route_stops list."""
        from app.services.iett_client import IettApiError
        from app.services.ntcapi_client import NtcApiError as NE

        bus = _bus("A-001", "500T")
        mock_iett = MagicMock()
        mock_iett.get_route_stops = AsyncMock(side_effect=IettApiError("iett down"))
        with (
            patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()),
            patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]),
            patch("app.routers.fleet.get_trail", return_value=[]),
            patch("app.routers.routes.get_session", return_value=MagicMock(), create=True),
            patch("app.services.cache._cache_get_internal", AsyncMock(return_value=None)),
            patch("app.services.cache.cache_set", AsyncMock()),
            patch(
                "app.services.ntcapi_client.get_route_stops",
                AsyncMock(side_effect=NE("down")),
            ),
            patch("app.routers.routes.IettClient", return_value=mock_iett),
        ):
            resp = client.get("/v1/fleet/A-001/detail")
        assert resp.status_code == 200
        assert resp.json()["route_stops"] == []


# ===========================  via-filter + raw arrivals  ====================


class TestStopsViaFilter:
    """Cover the via-filter logic in /v1/stops/{dcode}/arrivals.

    The ntcapi primary path is used for arrivals so the only IettClient
    instantiation is the via-stop route-code lookup (client2).
    """

    # Two normalised arrival dicts â€” one per route â€” returned by ntcapi mock
    _ARRIVALS_RAW = [{"raw": "a"}, {"raw": "b"}]
    _CANONICAL_500T = {
        "route_code": "500T",
        "destination": "LEVENT",
        "eta_minutes": 3,
        "eta_raw": "3 dk",
        "plate": None,
        "kapino": None,
        "lat": None,
        "lon": None,
        "speed_kmh": None,
        "last_seen_ts": None,
        "amenities": None,
    }
    _CANONICAL_15F = {
        "route_code": "15F",
        "destination": "BAKIRKOY",
        "eta_minutes": 8,
        "eta_raw": "8 dk",
        "plate": None,
        "kapino": None,
        "lat": None,
        "lon": None,
        "speed_kmh": None,
        "last_seen_ts": None,
        "amenities": None,
    }

    def test_via_filter_narrows_arrivals_to_routes_at_via_stop(self, client: TestClient) -> None:
        """?via= supplied and via-stop lookup succeeds â†’ only matching routes returned."""
        mock_via = MagicMock()
        mock_via.get_routes_at_stop = AsyncMock(return_value=["500T"])
        with (
            patch(
                "app.routers.stops.ntcapi_client.get_stop_arrivals",
                AsyncMock(return_value=self._ARRIVALS_RAW),
            ),
            patch(
                "app.services.normalizers.arrivals.from_ntcapi_ybs",
                side_effect=[self._CANONICAL_500T, self._CANONICAL_15F],
            ),
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_via),
            patch("app.routers.stops.get_plate_by_kapino", return_value=None),
        ):
            resp = client.get("/v1/stops/220602/arrivals?via=301341")
        assert resp.status_code == 200
        route_codes = [a["route_code"] for a in resp.json()]
        assert "500T" in route_codes
        assert "15F" not in route_codes

    def test_via_filter_failure_returns_unfiltered_and_logs_warning(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """IettApiError on via-stop lookup â†’ arrivals returned unfiltered; warning logged."""
        import logging

        from app.services.iett_client import IettApiError

        mock_via = MagicMock()
        mock_via.get_routes_at_stop = AsyncMock(side_effect=IettApiError("via down"))
        with (
            patch(
                "app.routers.stops.ntcapi_client.get_stop_arrivals",
                AsyncMock(return_value=self._ARRIVALS_RAW),
            ),
            patch(
                "app.services.normalizers.arrivals.from_ntcapi_ybs",
                side_effect=[self._CANONICAL_500T, self._CANONICAL_15F],
            ),
            patch("app.routers.stops.cache_get", AsyncMock(return_value=None), create=True),
            patch("app.routers.stops.cache_set", AsyncMock(), create=True),
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_via),
            patch("app.routers.stops.get_plate_by_kapino", return_value=None),
            caplog.at_level(logging.WARNING, logger="app.routers.stops"),
        ):
            resp = client.get("/v1/stops/220602/arrivals?via=301341")
        assert resp.status_code == 200
        route_codes = {a["route_code"] for a in resp.json()}
        assert route_codes == {"500T", "15F"}
        assert any("via-filter" in r.message for r in caplog.records)


class TestStopsArrivalsRaw:
    """Cover the /v1/stops/{dcode}/arrivals/raw debug endpoint."""

    def test_200_returns_html_content(self, client: TestClient) -> None:
        mock_client = MagicMock()
        mock_client._get_text = AsyncMock(return_value="<html>arrivals</html>")
        with (
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/220602/arrivals/raw")
        assert resp.status_code == 200
        assert "arrivals" in resp.text

    def test_502_when_iett_api_fails(self, client: TestClient) -> None:
        from app.services.iett_client import IettApiError

        mock_client = MagicMock()
        mock_client._get_text = AsyncMock(side_effect=IettApiError("timeout"))
        with (
            patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/220602/arrivals/raw")
        assert resp.status_code == 502


# ===========================  /v1/arac  ====================================


class TestAracSession:
    def test_captcha_returns_challenge(self, client: TestClient) -> None:
        mock_arac = MagicMock()
        mock_arac.get_captcha = AsyncMock(
            return_value={"captchaId": "cid-1", "captchaImage": "AAA"}
        )
        with patch("app.routers.arac.AracClient", return_value=mock_arac):
            resp = client.post("/v1/arac/session/captcha")
        assert resp.status_code == 200
        body = resp.json()
        assert body["captchaId"] == "cid-1"

    def test_create_returns_session_keys(self, client: TestClient) -> None:
        mock_arac = MagicMock()
        mock_arac.get_vehicle_hash = AsyncMock(return_value="hash_123")
        mock_arac.submit_captcha = AsyncMock(return_value=True)
        with (
            patch("app.routers.arac._captcha_cookies", {"cid-1": {"CookieName": "Val"}}),
            patch("app.routers.arac.AracClient", return_value=mock_arac),
        ):
            resp = client.post(
                "/v1/arac/session/create",
                json={"captchaId": "cid-1", "captchaAnswer": "123456", "kapino": "C-1753"},
            )
        assert resp.status_code == 200
        assert "sessionKey" in resp.json()

    def test_create_returns_400_for_wrong_captcha(self, client: TestClient) -> None:
        mock_arac = MagicMock()
        mock_arac.get_vehicle_hash = AsyncMock(return_value="hash_123")
        mock_arac.submit_captcha = AsyncMock(return_value=False)
        with (
            patch.object(
                __import__("app.routers.arac", fromlist=["_captcha_cookies"])._captcha_cookies,
                "get",
                return_value={"CookieName": "Val"},
            ),
            patch("app.routers.arac.AracClient", return_value=mock_arac),
        ):
            resp = client.post(
                "/v1/arac/session/create",
                json={"captchaId": "cid-1", "captchaAnswer": "WRNG", "kapino": "C-1753"},
            )
        assert resp.status_code in (400, 401)

    def test_create_returns_400_for_expired_captcha_id(self, client: TestClient) -> None:
        resp = client.post(
            "/v1/arac/session/create",
            json={"captchaId": "non-existent-cid", "captchaAnswer": "123456", "kapino": "C-1753"},
        )
        assert resp.status_code == 400
        assert "not found or expired" in resp.json()["detail"].lower()


class TestAracFleet:
    _HEADERS = {"X-Arac-Session-Key": '{"Cookie":"1"}'}

    def test_401_when_session_headers_missing(self, client: TestClient) -> None:
        resp = client.get("/v1/arac/fleet/C-1753/detail")
        assert resp.status_code == 401

    def test_400_when_session_key_invalid_json(self, client: TestClient) -> None:
        resp = client.get(
            "/v1/arac/fleet/C-1753/detail",
            headers={"X-Arac-Session-Key": "invalid_not_json"},
        )
        assert resp.status_code == 400
        assert "not valid json" in resp.json()["detail"].lower()

    def test_200_returns_single_vehicle(self, client: TestClient) -> None:
        from app.services.arac_client import AracClient

        mock_arac = MagicMock()
        mock_arac.get_vehicle_hash = AsyncMock(return_value="hash_123")
        mock_arac.get_detail = AsyncMock(
            return_value={
                "isSuccess": True,
                "dataVehicle": {
                    "vehicleDoorCode": "C-1753",
                    "numberPlate": "34 HO 1753",
                    "latitude": 41.01,
                    "longitude": 28.97,
                },
                "dataTask": [],
            }
        )
        with (
            patch("app.routers.arac.AracClient", return_value=mock_arac),
            patch(
                "app.routers.arac.AracClient.normalize_bus_position",
                side_effect=AracClient.normalize_bus_position,
            ),
            patch(
                "app.routers.arac.AracClient.normalize_missions",
                side_effect=AracClient.normalize_missions,
            ),
        ):
            resp = client.get(
                "/v1/arac/fleet/C-1753/detail",
                headers=self._HEADERS,
            )
        assert resp.status_code == 200
        assert resp.json()["profile"]["kapino"] == "C-1753"

    def test_single_vehicle_error_passthrough(self, client: TestClient) -> None:
        from app.services.arac_client import AracApiError

        mock_arac = MagicMock()
        mock_arac.get_vehicle_hash = AsyncMock(
            side_effect=AracApiError("not found", status_code=404)
        )
        with patch("app.routers.arac.AracClient", return_value=mock_arac):
            resp = client.get("/v1/arac/fleet/A-999/detail", headers=self._HEADERS)
        assert resp.status_code == 404


class TestAracRouterHelpers:
    def test_status_from_arac_error_bounds(self) -> None:
        from app.routers.arac import _status_from_arac_error
        from app.services.arac_client import AracApiError

        assert _status_from_arac_error(AracApiError("x", status_code=401)) == 401
        assert _status_from_arac_error(AracApiError("x", status_code=700), fallback=503) == 503

    def test_coercion_helpers(self) -> None:
        from app.routers.arac import _as_bool, _as_int, _as_str

        assert _as_int("7") == 7
        assert _as_int("bad") is None
        assert _as_bool(None) is None
        assert _as_bool(1) is True
        assert _as_bool("yes") is True
        assert _as_bool("no") is False
        assert _as_bool("unknown") is None
        assert _as_str(None) is None
        assert _as_str("  ") is None
        assert _as_str(123) == "123"

    def test_ms_to_iso_guard_paths(self) -> None:
        from app.routers.arac import _ms_to_iso

        assert _ms_to_iso(None) is None
        assert _ms_to_iso(-1) is None

    def test_require_session_headers_helper(self) -> None:
        from fastapi import HTTPException

        from app.routers.arac import _require_arac_session_headers

        assert _require_arac_session_headers(
            x_arac_session_id="sid",
            x_arac_session_key="key",
            x_session_id=None,
            x_session_key=None,
        ) == ("sid", "key")

        with pytest.raises(HTTPException) as exc_info:
            _require_arac_session_headers(
                x_arac_session_id=None,
                x_arac_session_key=None,
                x_session_id=None,
                x_session_key=None,
            )
        assert exc_info.value.status_code == 401

    def test_invalid_kapino_pattern_rejected(self, client: TestClient) -> None:
        """Path params with invalid chars are rejected with 422."""
        _HEADERS = {"X-Arac-Session-Key": '{"Cookie":"1"}'}
        resp = client.get("/v1/arac/fleet/!bad-kapino/detail", headers=_HEADERS)
        assert resp.status_code == 422
        resp2 = client.get(f"/v1/arac/fleet/{'A' * 41}/detail", headers=_HEADERS)
        assert resp2.status_code == 422


class TestStopAnnouncements:
    def test_stop_announcements_success(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get_routes(*args, **kwargs):
            return ["135T"]

        async def mock_fetch_filtered(*args, **kwargs):
            return [
                {
                    "route_code": "135T",
                    "route_name": "",
                    "type": "Trafik",
                    "updated_at": "",
                    "message": "Global Delay",
                }
            ]

        async def mock_get_stop_anns(*args, **kwargs):
            return [{"HAT": "135T", "BILGI": "Local Delay"}]

        monkeypatch.setattr("app.routers.stops.get_routes_at_stop", mock_get_routes)
        monkeypatch.setattr("app.routers.routes.fetch_filtered_announcements", mock_fetch_filtered)
        monkeypatch.setattr(
            "app.services.mobiett_client.MobiettClient.get_stop_announcements",
            mock_get_stop_anns,
        )
        monkeypatch.setattr("app.routers.stops.get_session", lambda: None)

        from app.services.cache import _store

        _store.clear()

        r = client.get("/v1/stops/260211/announcements")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        messages = [d["message"] for d in data]
        assert "Global Delay" in messages
        assert "Local Delay" in messages

    def test_stop_announcements_deduplication(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get_routes(*args, **kwargs):
            return ["135T"]

        async def mock_fetch_filtered(*args, **kwargs):
            return [
                {
                    "route_code": "135T",
                    "route_name": "",
                    "type": "Trafik",
                    "updated_at": "",
                    "message": "Duplicate Delay",
                }
            ]

        async def mock_get_stop_anns(*args, **kwargs):
            return [{"HAT": "135T", "BILGI": "Duplicate Delay"}]

        monkeypatch.setattr("app.routers.stops.get_routes_at_stop", mock_get_routes)
        monkeypatch.setattr("app.routers.routes.fetch_filtered_announcements", mock_fetch_filtered)
        monkeypatch.setattr(
            "app.services.mobiett_client.MobiettClient.get_stop_announcements",
            mock_get_stop_anns,
        )
        monkeypatch.setattr("app.routers.stops.get_session", lambda: None)

        from app.services.cache import _store

        _store.clear()

        r = client.get("/v1/stops/260211/announcements")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["message"] == "Duplicate Delay"

    def test_stop_announcements_ybs_fallback(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get_routes(*args, **kwargs):
            return ["135T"]

        async def mock_fetch_filtered(*args, **kwargs):
            return [
                {
                    "route_code": "135T",
                    "route_name": "",
                    "type": "Trafik",
                    "updated_at": "",
                    "message": "Global Only",
                }
            ]

        async def mock_get_stop_anns(*args, **kwargs):
            raise Exception("ybs is down")

        monkeypatch.setattr("app.routers.stops.get_routes_at_stop", mock_get_routes)
        monkeypatch.setattr("app.routers.routes.fetch_filtered_announcements", mock_fetch_filtered)
        monkeypatch.setattr(
            "app.services.mobiett_client.MobiettClient.get_stop_announcements",
            mock_get_stop_anns,
        )
        monkeypatch.setattr("app.routers.stops.get_session", lambda: None)

        from app.services.cache import _store

        _store.clear()

        r = client.get("/v1/stops/260211/announcements")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["message"] == "Global Only"

    def test_stop_announcements_routes_fallback(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get_routes(*args, **kwargs):
            raise Exception("soap is down")

        async def mock_fetch_filtered(*args, **kwargs):
            return []

        async def mock_get_stop_anns(*args, **kwargs):
            return [{"HAT": "135T", "BILGI": "Local Only"}]

        monkeypatch.setattr("app.routers.stops.get_routes_at_stop", mock_get_routes)
        monkeypatch.setattr("app.routers.routes.fetch_filtered_announcements", mock_fetch_filtered)
        monkeypatch.setattr(
            "app.services.mobiett_client.MobiettClient.get_stop_announcements",
            mock_get_stop_anns,
        )
        monkeypatch.setattr("app.routers.stops.get_session", lambda: None)

        from app.services.cache import _store

        _store.clear()

        r = client.get("/v1/stops/260211/announcements")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["message"] == "Local Only"

    def test_stop_announcements_deduplicate_same_message_different_routes(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get_routes(*args, **kwargs):
            return ["135T", "136B"]

        async def mock_fetch_filtered(*args, **kwargs):
            return [
                {
                    "route_code": "135T",
                    "route_name": "",
                    "type": "Trafik",
                    "updated_at": "",
                    "message": "Duplicate Delay",
                }
            ]

        async def mock_get_stop_anns(*args, **kwargs):
            return [{"HAT": "136B", "BILGI": "Duplicate Delay"}]

        monkeypatch.setattr("app.routers.stops.get_routes_at_stop", mock_get_routes)
        monkeypatch.setattr("app.routers.routes.fetch_filtered_announcements", mock_fetch_filtered)
        monkeypatch.setattr(
            "app.services.mobiett_client.MobiettClient.get_stop_announcements",
            mock_get_stop_anns,
        )
        monkeypatch.setattr("app.routers.stops.get_session", lambda: None)

        from app.services.cache import _store

        _store.clear()

        r = client.get("/v1/stops/260211/announcements")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        routes = [d["route_code"] for d in data]
        assert "135T" in routes
        assert "136B" in routes

    def test_stop_announcements_ybs_returns_null_fields(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get_routes(*args, **kwargs):
            return ["135T"]

        async def mock_fetch_filtered(*args, **kwargs):
            return []

        async def mock_get_stop_anns(*args, **kwargs):
            return [{"HAT": None, "BILGI": None}]

        monkeypatch.setattr("app.routers.stops.get_routes_at_stop", mock_get_routes)
        monkeypatch.setattr("app.routers.routes.fetch_filtered_announcements", mock_fetch_filtered)
        monkeypatch.setattr(
            "app.services.mobiett_client.MobiettClient.get_stop_announcements",
            mock_get_stop_anns,
        )
        monkeypatch.setattr("app.routers.stops.get_session", lambda: None)

        from app.services.cache import _store

        _store.clear()

        r = client.get("/v1/stops/260211/announcements")
        assert r.status_code == 200
        assert r.json() == []

    def test_stop_announcements_ybs_missing_duyuru_key(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get_routes(*args, **kwargs):
            return ["135T"]

        async def mock_fetch_filtered(*args, **kwargs):
            return []

        async def mock_get_stop_anns(*args, **kwargs):
            return None  # This happens if "duyuru" key is absent and it defaults to None

        monkeypatch.setattr("app.routers.stops.get_routes_at_stop", mock_get_routes)
        monkeypatch.setattr("app.routers.routes.fetch_filtered_announcements", mock_fetch_filtered)
        monkeypatch.setattr(
            "app.services.mobiett_client.MobiettClient.get_stop_announcements",
            mock_get_stop_anns,
        )
        monkeypatch.setattr("app.routers.stops.get_session", lambda: None)

        from app.services.cache import _store

        _store.clear()

        r = client.get("/v1/stops/260211/announcements")
        assert r.status_code == 200
        assert r.json() == []

    def test_stop_announcements_get_routes_fails(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def mock_get_routes(*args, **kwargs):
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Stop not found")

        async def mock_fetch_filtered(*args, **kwargs):
            return [
                {
                    "route_code": "135T",
                    "message": "Global Only",
                    "route_name": "",
                    "type": "Trafik",
                    "updated_at": "2026-06-10T12:00:00Z",
                }
            ]

        async def mock_get_stop_anns(*args, **kwargs):
            return [{"HAT": "135T", "BILGI": "Local Only"}]

        monkeypatch.setattr("app.routers.stops.get_routes_at_stop", mock_get_routes)
        monkeypatch.setattr("app.routers.routes.fetch_filtered_announcements", mock_fetch_filtered)
        monkeypatch.setattr(
            "app.services.mobiett_client.MobiettClient.get_stop_announcements",
            mock_get_stop_anns,
        )
        monkeypatch.setattr("app.routers.stops.get_session", lambda: None)

        from app.services.cache import _store

        _store.clear()

        r = client.get("/v1/stops/260211/announcements")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 2
        assert "Global Only" in [d["message"] for d in data]
        assert "Local Only" in [d["message"] for d in data]
