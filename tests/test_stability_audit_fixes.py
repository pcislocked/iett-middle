"""Regression tests for codebase stability audit fixes in iett-middle."""

from __future__ import annotations

import asyncio
import concurrent.futures
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient

from app.deps import (
    _clean_fleet,
    clear_fleet_store,
    find_bus_in_fleet,
    limiter,
    update_fleet,
)
from app.main import app
from app.models.bus import BusPosition
from app.models.route import ScheduledDeparture
from app.services import cache as cache_mod
from app.services import mobiett_client as mobiett_mod
from app.services import ntcapi_client as ntc_mod
from app.services import stop_cache, vehicle_cache
from app.services.arac_client import AracApiError, AracClient
from app.services.iett_client import IettApiError
from app.services.iett_parser import parse_mobiett_stop
from app.services.mobiett_client import MobiettClient
from app.services.notice_poller import GLOBAL_NOTICES_CACHE_KEY, notice_poll_loop
from app.services.traffic import TrafficClient


def test_arac_router_null_payloads_and_invalid_cookie_name() -> None:
    client = TestClient(app)

    # Invalid cookie name containing spaces/semicolons must return 400, not 500
    bad_resp = client.get(
        "/v1/arac/fleet/C-100/detail",
        headers={"X-Arac-Session-Key": '{"bad key;": "val"}'},
    )
    assert bad_resp.status_code == 400

    # Valid cookie key with null dataVehicle/dataTask and null plate/lineCode
    with (
        patch.object(AracClient, "get_vehicle_hash", AsyncMock(return_value="h1")),
        patch.object(
            AracClient,
            "get_detail",
            AsyncMock(return_value={"dataVehicle": None, "dataTask": None}),
        ),
    ):
        resp = client.get(
            "/v1/arac/fleet/C-100/detail",
            headers={"X-Arac-Session-Key": '{"ASP.NET_SessionId": "abc"}'},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["profile"]["route_code"] is None
    assert body["missions"]["missions"] == []


@pytest.mark.asyncio
async def test_arac_client_post_json_wraps_client_and_timeout_errors() -> None:
    session = MagicMock(spec=aiohttp.ClientSession)
    session.post.side_effect = asyncio.TimeoutError("timed out")
    client = AracClient(session)

    with pytest.raises(AracApiError) as exc_info:
        await client._post_json("/Home/Test", {"a": 1})
    assert exc_info.value.status_code == 503

    session.post.side_effect = aiohttp.ClientConnectionError("reset")
    with pytest.raises(AracApiError) as exc_info2:
        await client._post_json("/Home/Test", {"a": 1})
    assert exc_info2.value.status_code == 503


def test_routes_schedule_soap_fallback_coalesces_none_fields() -> None:
    cache_mod._store.clear()
    client = TestClient(app)
    soap_rows = [
        ScheduledDeparture(
            route_code="15F",
            route_name="",
            route_variant="15F_G_D0",
            direction="",
            day_type="",
            service_type="",
            departure_time="06:30",
        ),
        ScheduledDeparture(
            route_code="",
            route_name="",
            route_variant="",
            direction="",
            day_type="",
            service_type="",
            departure_time="07:00",
        ),
    ]
    mock_iett = MagicMock()
    mock_iett.get_route_schedule = AsyncMock(return_value=soap_rows)

    with (
        patch("app.routers.routes.get_session", return_value=MagicMock()),
        patch(
            "app.routers.routes.ntcapi_client.get_timetable",
            AsyncMock(side_effect=ntc_mod.NtcApiError("down")),
        ),
        patch("app.routers.routes.IettClient", return_value=mock_iett),
    ):
        resp = client.get("/v1/routes/15F/schedule")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["route_code"] == "15F"
    assert body[0]["service_type"] == ""
    assert body[0]["direction"] == ""


def test_routes_stops_empty_list_skipped_and_null_coords_short_ttl() -> None:
    cache_mod._store.clear()
    client = TestClient(app)
    mock_iett = MagicMock()
    mock_iett.get_route_stops = AsyncMock(return_value=[])

    with (
        patch("app.routers.routes.get_session", return_value=MagicMock()),
        patch("app.routers.routes.ntcapi_client.get_route_stops", AsyncMock(return_value=[])),
        patch("app.routers.routes.IettClient", return_value=mock_iett),
    ):
        resp = client.get("/v1/routes/14M/stops")
    assert resp.status_code == 200
    assert resp.json() == []
    assert "routes:stops:14M" not in cache_mod._store

    raw_g = [
        {
            "route_code": "14M",
            "direction_letter": "G",
            "sequence": 1,
            "stop_code": "100101",
            "stop_name": "TEST",
            "lat": None,
            "lon": None,
            "district": None,
        }
    ]
    with (
        patch("app.routers.routes.get_session", return_value=MagicMock()),
        patch(
            "app.routers.routes.ntcapi_client.get_route_stops",
            AsyncMock(side_effect=[raw_g, []]),
        ),
        patch("app.routers.routes.IettClient", return_value=mock_iett),
    ):
        resp2 = client.get("/v1/routes/14M/stops")
    assert resp2.status_code == 200
    assert "routes:stops:14M" in cache_mod._store
    _, fresh_exp, stale_exp, _ = cache_mod._store["routes:stops:14M"]
    assert (stale_exp - fresh_exp) <= 90


def test_deps_clean_fleet_in_place_and_partial_update_preserves_metadata() -> None:
    clear_fleet_store()
    initial = BusPosition(
        kapino="C-101",
        plate="34 AB 101",
        latitude=41.0,
        longitude=29.0,
        speed=45,
        operator="IETT",
        last_seen="12:00:00",
        route_code="500T",
        nearest_stop="1001",
    )
    update_fleet([initial], is_full_snapshot=True)
    # Imported _clean_fleet reference must see live mutations
    assert "C101" in _clean_fleet

    partial = BusPosition(
        kapino="C-101",
        plate=None,
        latitude=41.01,
        longitude=29.01,
        speed=None,
        operator=None,
        last_seen="12:01:00",
        route_code="500T",
        nearest_stop=None,
    )
    update_fleet([partial], is_full_snapshot=False)
    bus, kap = find_bus_in_fleet("C-101")
    assert kap == "C-101"
    assert bus is not None
    assert bus["plate"] == "34 AB 101"
    assert bus["speed"] == 45
    assert bus["operator"] == "IETT"
    assert bus["nearest_stop"] == "1001"
    clear_fleet_store()


@pytest.mark.asyncio
async def test_ntcapi_401_retries_once_and_handles_null_expires_and_notices() -> None:
    ntc_mod._token = "stale-tok"
    ntc_mod._token_expiry = time.monotonic() + 1000.0

    resp_401 = MagicMock()
    resp_401.status = 401
    resp_401.text = AsyncMock(return_value="unauthorized")

    resp_tok = MagicMock()
    resp_tok.status = 200
    resp_tok.json = AsyncMock(return_value={"access_token": "fresh-tok", "expires_in": None})

    resp_ok = MagicMock()
    resp_ok.status = 200
    resp_ok.json = AsyncMock(return_value={"data": None})

    def _cm(resp):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=resp)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    session = MagicMock(spec=aiohttp.ClientSession)
    session.post.side_effect = [_cm(resp_401), _cm(resp_tok), _cm(resp_ok)]

    notices = await ntc_mod.get_global_notices(session)
    assert notices == []
    assert ntc_mod._token == "fresh-tok"
    assert ntc_mod._token_expiry > time.monotonic() + 3000.0
    ntc_mod._token = None
    ntc_mod._token_expiry = 0.0


@pytest.mark.asyncio
async def test_traffic_client_wraps_timeout_and_bad_index_payload() -> None:
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get.side_effect = asyncio.TimeoutError("timeout")
    tc = TrafficClient(session)
    with pytest.raises(IettApiError):
        await tc.get_traffic_index()

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = AsyncMock(return_value="not-a-number")
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    session.get.side_effect = None
    session.get.return_value = cm
    with pytest.raises(IettApiError):
        await tc.get_traffic_index()


def test_sqlite_caches_close_worker_thread_connections_cleanly(tmp_path) -> None:
    stop_db = str(tmp_path / "multi_stops.db")
    veh_db = str(tmp_path / "multi_veh.db")

    orig_stop = stop_cache.settings.stop_cache_path
    orig_veh = vehicle_cache.settings.vehicle_cache_path
    try:
        stop_cache.settings.stop_cache_path = stop_db
        vehicle_cache.settings.vehicle_cache_path = veh_db
        stop_cache.close_connection()
        vehicle_cache.close_connection()

        def _worker():
            sc = stop_cache.get_db_connection()
            vc = vehicle_cache.get_db_connection()
            assert sc.execute("PRAGMA busy_timeout;").fetchone()[0] == 30000
            assert vc.execute("PRAGMA busy_timeout;").fetchone()[0] == 30000

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(_worker) for _ in range(8)]
            for f in futs:
                f.result()

        assert len(stop_cache._all_conns) >= 1
        assert len(vehicle_cache._all_conns) >= 1
    finally:
        stop_cache.close_connection()
        vehicle_cache.close_connection()
        assert len(stop_cache._all_conns) == 0
        assert len(vehicle_cache._all_conns) == 0
        stop_cache.settings.stop_cache_path = orig_stop
        vehicle_cache.settings.vehicle_cache_path = orig_veh


def test_parse_mobiett_stop_returns_none_for_zero_or_missing_coords() -> None:
    res = parse_mobiett_stop(
        {"DURAK_DURAK_KODU": "1001", "DURAK_ADI": "TEST", "DURAK_GEOLOC": {"x": 0, "y": None}}
    )
    assert res is not None
    assert res.latitude is None
    assert res.longitude is None


@pytest.mark.asyncio
async def test_mobiett_client_hat_id_no_negative_cache_and_fallback_dir() -> None:
    mobiett_mod.MobiettClient._hat_id_cache.clear()
    session = MagicMock(spec=aiohttp.ClientSession)
    client = MobiettClient(session)

    # First call fails to resolve (both 119 and 120 empty); must NOT cache None permanently
    with patch.object(
        client, "_post_service", AsyncMock(side_effect=[[], [], [], [{"HAT_ID": 777}]])
    ):
        assert await client.get_hat_id("TEST1") is None
        assert "TEST1" not in MobiettClient._hat_id_cache
        assert await client.get_hat_id("TEST1") == 777

    # get_stop_announcements handles null value cleanly
    with patch.object(client, "_post_service", AsyncMock(return_value={"1234": None})):
        assert await client.get_stop_announcements("1234") == []

    mobiett_mod.MobiettClient._hat_id_cache.clear()


@pytest.mark.asyncio
async def test_announcements_global_serves_stale_notices_and_poller_retries_quickly() -> None:
    cache_mod._store.clear()
    await cache_mod.cache_set(
        GLOBAL_NOTICES_CACHE_KEY, [{"title": "Stale Notice"}], ttl=10, stale_ttl=300
    )
    val, fresh_exp, stale_exp, created_at = cache_mod._store[GLOBAL_NOTICES_CACHE_KEY]
    cache_mod._store[GLOBAL_NOTICES_CACHE_KEY] = (val, time.monotonic() - 1, stale_exp, created_at)

    client = TestClient(app)
    resp = client.get("/v1/announcements/global")
    assert resp.status_code == 200
    assert resp.json() == [{"title": "Stale Notice"}]

    sleep_durations: list[float] = []

    async def fake_sleep(sec: float) -> None:
        sleep_durations.append(sec)
        if len(sleep_durations) >= 1:
            raise asyncio.CancelledError

    with (
        patch("app.services.notice_poller.get_session", return_value=SimpleNamespace()),
        patch(
            "app.services.notice_poller.get_global_notices",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch("asyncio.sleep", side_effect=fake_sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await notice_poll_loop()
    assert sleep_durations == [60]


def test_cors_headers_present_on_429_rate_limit_responses() -> None:
    limiter.reset()
    client = TestClient(app)
    # /v1/fleet/refresh is 5/minute
    last_resp = None
    for _ in range(7):
        last_resp = client.post("/v1/fleet/refresh", headers={"Origin": "https://example.test"})
        if last_resp.status_code == 429:
            break
    assert last_resp is not None
    assert last_resp.status_code == 429
    assert last_resp.headers.get("access-control-allow-origin") == "*"
    limiter.reset()
