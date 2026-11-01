"""Unit tests for backend security and memory-hardening fixes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiohttp
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.main import app
from app.models.bus import BusPosition
from app.routers.arac import _isolated_arac_session
from app.utils.http import ResponseTooLargeError, read_bounded_text


@pytest.mark.asyncio
async def test_read_bounded_text_rejects_large_content_length() -> None:
    resp = Mock()
    resp.content_length = 30 * 1024 * 1024  # 30 MB
    with pytest.raises(ResponseTooLargeError, match="Content-Length"):
        await read_bounded_text(resp, max_bytes=1024)


@pytest.mark.asyncio
async def test_read_bounded_text_rejects_streaming_body_exceeding_limit() -> None:
    resp = MagicMock(spec=aiohttp.ClientResponse)
    resp.content_length = None
    resp.charset = "utf-8"
    resp.content.read = AsyncMock(return_value=b"x" * 1025)
    with pytest.raises(ResponseTooLargeError, match="exceeded maximum allowed size"):
        await read_bounded_text(resp, max_bytes=1024)


@pytest.mark.asyncio
async def test_read_bounded_text_accepts_valid_streaming_body() -> None:
    resp = MagicMock(spec=aiohttp.ClientResponse)
    resp.content_length = 5
    resp.charset = "utf-8"
    resp.content.read = AsyncMock(return_value=b"hello")
    text = await read_bounded_text(resp, max_bytes=1024)
    assert text == "hello"


@pytest.mark.asyncio
async def test_isolated_arac_session_reuses_shared_connector_without_closing_it() -> None:
    connector = aiohttp.TCPConnector()
    shared_session = aiohttp.ClientSession(connector=connector)
    deps.set_session(shared_session)
    try:
        async with _isolated_arac_session(cookies={"test_cookie": "val1"}) as s1:
            assert s1.connector is connector
            assert not s1.connector_owner

        # Connector must remain open after isolated session exits
        assert not connector.closed
        assert len(shared_session.cookie_jar) == 0
    finally:
        await deps.close_session()


def test_record_plate_mapping_does_not_corrupt_fleet_with_headless_dict() -> None:
    deps.clear_fleet_store()
    deps.record_plate_mapping("C-1753", "34 HO 1753")

    # Must not insert a coordinate-less dict into _fleet
    assert "C-1753" not in deps._fleet
    # Plate lookup still succeeds via SQLite vehicle_cache
    assert deps.get_plate_by_kapino("C-1753") == "34 HO 1753"

    # Subsequent update_fleet must not raise KeyError: 'latitude'
    deps.update_fleet(
        [
            BusPosition(
                kapino="C-1753",
                plate=None,
                latitude=41.01,
                longitude=28.97,
                last_seen="12:00:00",
            )
        ]
    )
    assert deps._fleet["C-1753"]["latitude"] == 41.01


def test_arrivals_raw_is_cached_and_hidden_from_openapi() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    schema = client.get("/openapi.json").json()
    assert "/v1/stops/{dcode}/arrivals/raw" not in schema.get("paths", {})

    mock_client = MagicMock()
    mock_client._get_text = AsyncMock(return_value="<html>cached-raw</html>")
    with (
        patch("app.routers.stops.get_session", return_value=MagicMock(), create=True),
        patch("app.routers.stops.IettClient", return_value=mock_client),
    ):
        r1 = client.get("/v1/stops/220602/arrivals/raw")
        r2 = client.get("/v1/stops/220602/arrivals/raw")

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.text == "<html>cached-raw</html>"
    assert mock_client._get_text.call_count == 1
