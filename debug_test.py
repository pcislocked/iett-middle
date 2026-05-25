from fastapi.testclient import TestClient
from app.main import app
from unittest.mock import patch, AsyncMock, MagicMock

cached_stops=[{'direction': 'G', 'district': None, 'latitude': 41.08, 'longitude': 29.01, 'route_code': '500T', 'sequence': 1, 'stop_code': 'ST001', 'stop_name': 'Test Stop'}]
bus={'direction': 'D', 'kapino': 'A-001', 'last_seen': '2024-01-01T00:00:00Z', 'latitude': 41.05, 'longitude': 29.0, 'route_code': '500T', 'stop_sequence': 0}

with patch("app.routers.fleet.ensure_fleet_fresh", AsyncMock()), \
     patch("app.routers.fleet.get_fleet_snapshot", return_value=[bus]), \
     patch("app.routers.fleet.get_trail", return_value=[]), \
     patch("app.routers.fleet.get_session", return_value=MagicMock()), \
     patch("app.services.ntcapi_client.get_stop_arrivals", AsyncMock(return_value=[])), \
     patch("app.services.cache.cache_get", AsyncMock(return_value=cached_stops)), \
     patch("app.services.cache.cache_set", AsyncMock()):

    client = TestClient(app, raise_server_exceptions=True)
    resp = client.get('/v1/fleet/A-001/detail')
    print(resp.status_code)
