"""Tests for fleet amenities probing logic in fleet router."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.cache import cache_clear, cache_set

@pytest.fixture(autouse=True)
def wipe_cache():
    asyncio.run(cache_clear())
    yield
    asyncio.run(cache_clear())

def test_bus_detail_returns_cached_amenities():
    # Pre-populate cache with amenities
    asyncio.run(cache_set("amenities:kapino:C-123", {"ac": True, "usb": False, "wifi": True, "accessible": True}, 3600))
    
    with patch("app.routers.fleet.get_fleet_snapshot", return_value=[
        {"kapino": "C-123", "route_code": "500T", "latitude": 41.0, "longitude": 29.0, "last_seen": "2024-01-01T00:00:00Z"}
    ]), patch("app.routers.fleet.get_trail", return_value=[]), patch("app.routers.fleet.ensure_fleet_fresh", new_callable=AsyncMock):
        
        with TestClient(app) as client:
            response = client.get("/v1/fleet/C-123/detail")
        assert response.status_code == 200
        data = response.json()
        assert data["kapino"] == "C-123"
        assert data["is_air_conditioned"] is True
        assert data["has_usb"] is False
        assert data["has_wifi"] is True
        assert data["accessible"] is True

def test_bus_detail_probes_amenities_on_cache_miss():
    # Cache is empty. We simulate fleet returning bus on 500T.
    # Route stops cache needs to be set so we have upcoming stops to probe.
    asyncio.run(cache_set("routes:stops:500T", [
        {"route_code": "500T", "direction": "G", "stop_name": "Stop 1", "stop_code": "S1", "sequence": 1, "latitude": 41.1, "longitude": 29.1},
        {"route_code": "500T", "direction": "G", "stop_name": "Stop 2", "stop_code": "S2", "sequence": 2, "latitude": 41.2, "longitude": 29.2},
    ], 3600))
    
    class MockArrival:
        def __init__(self):
            self.kapino = "C-123"
            class MockAmenities:
                def model_dump(self):
                    return {"ac": False, "usb": True, "wifi": False, "accessible": True}
            self.amenities = MockAmenities()
            
        def get(self, key):
            if key == "kapino":
                return self.kapino
            if key == "amenities":
                return {"ac": False, "usb": True, "wifi": False, "accessible": True}
            return None
            
    mock_ntcapi = AsyncMock()
    # Return our mock arrival with dict format like from_ntcapi_ybs does
    mock_ntcapi.return_value = [{"kapino": "C-123", "amenities": {"ac": False, "usb": True, "wifi": False, "accessible": True}}]

    with patch("app.routers.fleet.get_fleet_snapshot", return_value=[
        {"kapino": "C-123", "route_code": "500T", "latitude": 41.0, "longitude": 29.0, "stop_sequence": 0, "last_seen": "2024-01-01T00:00:00Z"}
    ]), patch("app.routers.fleet.get_trail", return_value=[]), \
        patch("app.routers.fleet.ensure_fleet_fresh", new_callable=AsyncMock), \
        patch("app.services.ntcapi_client.get_stop_arrivals", new=mock_ntcapi), \
        patch("app.services.normalizers.arrivals.from_ntcapi_ybs", lambda r: r):
        
        with TestClient(app) as client:
            response = client.get("/v1/fleet/C-123/detail")
        assert response.status_code == 200
        data = response.json()
        assert data["is_air_conditioned"] is False
        assert data["has_usb"] is True
        assert data["has_wifi"] is False

def test_bus_detail_negative_cache_on_probe_failure():
    asyncio.run(cache_set("routes:stops:500T", [
        {"route_code": "500T", "direction": "G", "stop_name": "Stop 1", "stop_code": "S1", "sequence": 1, "latitude": 41.1, "longitude": 29.1},
    ], 3600))
    
    mock_ntcapi = AsyncMock(return_value=[])  # no arrivals found
    
    with patch("app.routers.fleet.get_fleet_snapshot", return_value=[
        {"kapino": "C-123", "route_code": "500T", "latitude": 41.0, "longitude": 29.0, "stop_sequence": 0, "last_seen": "2024-01-01T00:00:00Z"}
    ]), patch("app.routers.fleet.get_trail", return_value=[]), \
        patch("app.routers.fleet.ensure_fleet_fresh", new_callable=AsyncMock), \
        patch("app.services.ntcapi_client.get_stop_arrivals", new=mock_ntcapi):
        
        with TestClient(app) as client:
            response = client.get("/v1/fleet/C-123/detail")
        assert response.status_code == 200
        data = response.json()
        # Should be None because it's not present
        assert data.get("is_air_conditioned") is None
        assert data.get("has_usb") is None
        
        # Verify negative cache was set (empty dict)
        from app.services.cache import _store
        assert "amenities:kapino:C-123" in _store
        assert _store["amenities:kapino:C-123"][0] == {}
