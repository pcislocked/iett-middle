"""Tests for OSRM helper and haversine utility."""
from __future__ import annotations

from app.services.osrm import haversine, haversine_eta


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
