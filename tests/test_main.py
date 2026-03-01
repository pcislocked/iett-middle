"""Tests for app.main — health/root endpoints and aiohttp trace hooks."""
from __future__ import annotations

import time
from unittest.mock import MagicMock

from starlette.testclient import TestClient

from app.main import app


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Endpoint tests
# ---------------------------------------------------------------------------

class TestEndpoints:
    def test_health_returns_ok(self) -> None:
        resp = _client().get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "cache" in body

    def test_root_returns_service_name(self) -> None:
        resp = _client().get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "iett-middle"
        assert "docs" in body


# ---------------------------------------------------------------------------
# _make_trace_config
# ---------------------------------------------------------------------------

class TestMakeTraceConfig:
    def test_returns_aiohttp_trace_config(self) -> None:
        import aiohttp
        from app.main import _make_trace_config

        tc = _make_trace_config()
        assert isinstance(tc, aiohttp.TraceConfig)
        assert len(tc.on_request_start) == 1
        assert len(tc.on_request_end) == 1
        assert len(tc.on_request_exception) == 1


# ---------------------------------------------------------------------------
# Trace hook functions
# ---------------------------------------------------------------------------

class TestOnRequestStart:
    async def test_sets_start_ts_on_ctx(self) -> None:
        from app.main import _on_request_start

        ctx = MagicMock(spec=[])  # no pre-existing attributes
        params = MagicMock()
        params.method = "GET"
        params.url = "https://ws.iett.gov.tr"
        params.headers = {}

        await _on_request_start(MagicMock(), ctx, params)
        assert hasattr(ctx, "start_ts")

    async def test_logs_soap_action_when_present(self) -> None:
        from app.main import _on_request_start

        ctx = MagicMock(spec=[])
        params = MagicMock()
        params.method = "POST"
        params.url = "https://ws.iett.gov.tr"
        params.headers = {"SOAPAction": "GetFiloAracKonum_json"}

        # Should not raise; SOAPAction path is exercised
        await _on_request_start(MagicMock(), ctx, params)
        assert hasattr(ctx, "start_ts")


class TestOnRequestEnd:
    async def test_success_response_with_content_length(self) -> None:
        from app.main import _on_request_end

        ctx = MagicMock()
        ctx.start_ts = time.monotonic() - 0.05
        params = MagicMock()
        params.response.status = 200
        params.response.content_length = 4096
        params.url = "https://ws.iett.gov.tr"

        await _on_request_end(MagicMock(), ctx, params)  # should not raise

    async def test_error_response_no_content_length(self) -> None:
        from app.main import _on_request_end

        ctx = MagicMock()
        ctx.start_ts = time.monotonic()
        params = MagicMock()
        params.response.status = 500
        params.response.content_length = 0   # falsy — exercises the no-size branch
        params.url = "https://ws.iett.gov.tr"

        await _on_request_end(MagicMock(), ctx, params)


class TestOnRequestException:
    async def test_without_start_ts(self) -> None:
        from app.main import _on_request_exception

        ctx = MagicMock(spec=[])  # no start_ts — exercises getattr fallback
        params = MagicMock()
        params.method = "GET"
        params.url = "https://ws.iett.gov.tr"
        params.exception = ConnectionError("timeout")

        await _on_request_exception(MagicMock(), ctx, params)

    async def test_with_start_ts(self) -> None:
        from app.main import _on_request_exception

        ctx = MagicMock()
        ctx.start_ts = time.monotonic() - 0.1
        params = MagicMock()
        params.method = "POST"
        params.url = "https://ws.iett.gov.tr"
        params.exception = TimeoutError("read timeout")

        await _on_request_exception(MagicMock(), ctx, params)
