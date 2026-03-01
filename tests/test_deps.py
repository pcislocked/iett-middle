"""Tests for shared in-memory stores and helper functions in app.deps."""
from __future__ import annotations

import pytest

import app.deps as deps
from app.models.bus import BusPosition
from app.models.stop import NearbyStop


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bus(kapino: str, lat: float, lon: float, route_code: str = "500T", nearest: str = "301341") -> BusPosition:
    return BusPosition(
        kapino=kapino,
        plate="34 HO 1000",
        route_code=route_code,
        latitude=lat,
        longitude=lon,
        nearest_stop=nearest,
        direction="D",
        last_seen="00:00:00",
    )


def _stop(code: str, lat: float, lon: float) -> NearbyStop:
    return NearbyStop(
        stop_code=code,
        stop_name=f"Stop {code}",
        latitude=lat,
        longitude=lon,
        district="Test",
        distance_m=0.0,
    )


# ---------------------------------------------------------------------------
# Fleet store
# ---------------------------------------------------------------------------

class TestFleetStore:
    def setup_method(self) -> None:
        """Reset fleet state before each test."""
        deps._fleet.clear()
        deps._trail.clear()
        deps._fleet_updated_at = None
        deps._kapino_last_route.clear()

    def test_empty_snapshot(self) -> None:
        assert deps.get_fleet_snapshot() == []

    def test_update_fleet_populates_snapshot(self) -> None:
        buses = [_bus("A-001", 41.0, 29.0)]
        deps.update_fleet(buses)
        snapshot = deps.get_fleet_snapshot()
        assert len(snapshot) == 1
        assert snapshot[0]["kapino"] == "A-001"

    def test_update_fleet_sets_updated_at(self) -> None:
        deps.update_fleet([_bus("A-001", 41.0, 29.0)])
        assert deps.get_fleet_updated_at() is not None

    def test_fleet_updated_at_is_none_initially(self) -> None:
        assert deps.get_fleet_updated_at() is None

    def test_snapshot_reflects_latest_position(self) -> None:
        deps.update_fleet([_bus("A-001", 41.0, 29.0)])
        deps.update_fleet([_bus("A-001", 41.1, 29.1)])
        snapshot = deps.get_fleet_snapshot()
        assert len(snapshot) == 1
        assert abs(snapshot[0]["latitude"] - 41.1) < 0.001

    def test_trail_grows_on_movement(self) -> None:
        deps.update_fleet([_bus("A-001", 41.0, 29.0)])
        deps.update_fleet([_bus("A-001", 41.1, 29.1)])  # bus moves → trail entry added
        trail = deps.get_trail("A-001")
        assert len(trail) >= 1

    def test_trail_empty_for_unknown_kapino(self) -> None:
        assert deps.get_trail("NOBODY") == []

    def test_get_buses_near_stop_filters_by_nearest(self) -> None:
        buses = [
            _bus("A-001", 41.0, 29.0, nearest="301341"),
            _bus("B-002", 41.1, 29.1, nearest="220602"),
        ]
        deps.update_fleet(buses)
        nearby = deps.get_buses_near_stop("301341")
        assert len(nearby) == 1
        assert nearby[0]["kapino"] == "A-001"

    def test_get_buses_near_stop_empty_when_none_match(self) -> None:
        deps.update_fleet([_bus("A-001", 41.0, 29.0, nearest="301341")])
        assert deps.get_buses_near_stop("000000") == []

    def test_multiple_buses_on_same_stop(self) -> None:
        buses = [
            _bus("A-001", 41.0, 29.0, nearest="301341"),
            _bus("B-002", 41.0, 29.0, nearest="301341"),
        ]
        deps.update_fleet(buses)
        nearby = deps.get_buses_near_stop("301341")
        assert len(nearby) == 2

    def test_get_plate_by_kapino_known_returns_plate(self) -> None:
        deps.update_fleet([_bus("K-999", 41.0, 29.0)])
        plate = deps.get_plate_by_kapino("K-999")
        assert plate == "34 HO 1000"

    def test_get_plate_by_kapino_unknown_returns_none(self) -> None:
        plate = deps.get_plate_by_kapino("NOTEXIST")
        assert plate is None

    def test_last_route_persisted_when_route_code_present(self) -> None:
        deps.update_fleet([_bus("A-001", 41.0, 29.0, route_code="15F")])
        assert deps.get_last_route_by_kapino("A-001") == "15F"

    def test_last_route_not_overwritten_when_route_code_null(self) -> None:
        deps.update_fleet([_bus("A-001", 41.0, 29.0, route_code="15F")])
        # Simulate bus going offline / parked — route_code becomes None
        deps.update_fleet([_bus("A-001", 41.1, 29.1, route_code=None)])
        assert deps.get_last_route_by_kapino("A-001") == "15F"

    def test_get_last_route_unknown_kapino_returns_none(self) -> None:
        assert deps.get_last_route_by_kapino("NOBODY") is None


# ---------------------------------------------------------------------------
# Stop spatial index
# ---------------------------------------------------------------------------

class TestStopIndex:
    def setup_method(self) -> None:
        """Reset stop index before each test."""
        deps._stop_index.clear()
        deps._stop_index_updated_at = None

    def test_index_empty_initially(self) -> None:
        assert deps.get_stop_index_updated_at() is None

    def test_update_sets_timestamp(self) -> None:
        deps.update_stop_index([_stop("A", 41.0, 29.0)])
        assert deps.get_stop_index_updated_at() is not None

    def test_update_populates_index(self) -> None:
        deps.update_stop_index([_stop("A", 41.0, 29.0), _stop("B", 42.0, 30.0)])
        assert len(deps._stop_index) == 2

    def test_nearby_returns_stop_within_radius(self) -> None:
        lat, lon = 41.06, 28.99
        deps.update_stop_index([_stop("CLOSE", lat + 0.001, lon + 0.001)])
        results = deps.get_nearby_stops(lat, lon, radius_m=500.0)
        assert any(r["stop_code"] == "CLOSE" for r in results)

    def test_nearby_excludes_stop_outside_radius(self) -> None:
        lat, lon = 41.0, 29.0
        # ~111 km away
        deps.update_stop_index([_stop("FAR", lat + 1.0, lon + 1.0)])
        results = deps.get_nearby_stops(lat, lon, radius_m=500.0)
        assert results == []

    def test_nearby_results_sorted_by_distance(self) -> None:
        lat, lon = 41.0, 29.0
        deps.update_stop_index([
            _stop("MID", lat + 0.003, lon),   # ~333 m
            _stop("NEAR", lat + 0.001, lon),  # ~111 m
            _stop("FAR", lat + 0.004, lon),   # ~444 m
        ])
        results = deps.get_nearby_stops(lat, lon, radius_m=600.0)
        codes = [r["stop_code"] for r in results]
        assert codes == ["NEAR", "MID", "FAR"]

    def test_nearby_empty_when_index_empty(self) -> None:
        results = deps.get_nearby_stops(41.0, 29.0, radius_m=1000.0)
        assert results == []

    def test_nearby_distance_m_field_populated(self) -> None:
        lat, lon = 41.0, 29.0
        deps.update_stop_index([_stop("A", lat, lon)])  # same coords → ~0 m
        results = deps.get_nearby_stops(lat, lon, radius_m=100.0)
        assert results[0]["distance_m"] == pytest.approx(0.0, abs=5.0)  # type: ignore[arg-type]

    def test_haversine_accuracy(self) -> None:
        """Known distance: Istanbul lat/lon offset of 0.005° ≈ ~500 m."""
        lat, lon = 41.0, 29.0
        dlat = 0.004_5  # ≈ 500 m north
        deps.update_stop_index([_stop("TEST", lat + dlat, lon)])
        results = deps.get_nearby_stops(lat, lon, radius_m=600.0)
        d = results[0]["distance_m"]
        # R_EARTH * dlat_rad ≈ 6_371_000 * 0.00007854 ≈ 500 m
        assert 400 < d < 600, f"Expected ~500m, got {d:.1f}m"
