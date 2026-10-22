"""Tests for SoapRouteManager (adaptive direct-first routing, proxy fallback, rate limiting)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest

from app.config import settings
from app.services.iett_client import IettApiError
from app.services.soap_router import SoapRouteManager

DUMMY_PROXY = "http://proxy.test:8080"


@pytest.fixture()
def router() -> SoapRouteManager:
    return SoapRouteManager()


def _mock_response(text: str) -> Mock:
    resp = Mock()
    resp.status = 200
    resp.raise_for_status = Mock()
    resp.text = AsyncMock(return_value=text)
    return resp


@pytest.mark.asyncio()
async def test_soap_router_direct_no_proxy(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = _mock_response("<soap>ok</soap>")
    session.post.return_value.__aenter__.return_value = mock_resp

    with patch.object(settings, "soap_proxy_url", None):
        res = await router.post(
            session=session,
            url="https://api.ibb.gov.tr/test",
            data=b"test",
            headers={},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        assert res == "<soap>ok</soap>"
        _, kwargs = session.post.call_args
        assert "proxy" not in kwargs


@pytest.mark.asyncio()
async def test_soap_router_direct_preferred_when_proxy_configured(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = _mock_response("<soap>direct ok</soap>")
    session.post.return_value.__aenter__.return_value = mock_resp

    with patch.object(settings, "soap_proxy_url", DUMMY_PROXY):
        res = await router.post(
            session=session,
            url="https://api.ibb.gov.tr/test",
            data=b"test",
            headers={},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        assert res == "<soap>direct ok</soap>"
        assert router.is_direct_healthy is True
        assert session.post.call_count == 1
        _, kwargs = session.post.call_args
        assert "proxy" not in kwargs


@pytest.mark.asyncio()
async def test_soap_router_fallback_to_proxy_on_direct_failure(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)

    proxy_resp = _mock_response("<soap>proxy ok</soap>")

    # First call (direct) raises Timeout, second call (proxy) succeeds
    session.post.side_effect = [
        TimeoutError("Connection timed out"),
        AsyncMock(__aenter__=AsyncMock(return_value=proxy_resp), __aexit__=AsyncMock()),
    ]

    with (
        patch.object(settings, "soap_proxy_url", DUMMY_PROXY),
        patch.object(settings, "soap_proxy_min_interval", 0.0),
    ):
        res = await router.post(
            session=session,
            url="https://api.ibb.gov.tr/test",
            data=b"test",
            headers={},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        assert res == "<soap>proxy ok</soap>"
        assert router.is_direct_healthy is False
        assert session.post.call_count == 2

        # Second call must have used the proxy
        _, proxy_kwargs = session.post.call_args_list[1]
        assert proxy_kwargs["proxy"] == DUMMY_PROXY


@pytest.mark.asyncio()
async def test_soap_router_proxy_failure_marks_degraded(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)

    # First call (direct) fails, second call (proxy) also fails
    session.post.side_effect = [
        TimeoutError("Direct timed out"),
        aiohttp.ClientConnectorError(
            connection_key=Mock(),
            os_error=OSError("Proxy Connection refused"),
        ),
    ]

    with (
        patch.object(settings, "soap_proxy_url", DUMMY_PROXY),
        patch.object(settings, "soap_proxy_min_interval", 0.0),
    ):
        with pytest.raises(IettApiError, match="SOAP proxy call failed"):
            await router.post(
                session=session,
                url="https://api.ibb.gov.tr/test",
                data=b"test",
                headers={},
                timeout=aiohttp.ClientTimeout(total=5.0),
            )

        assert router.is_direct_healthy is False
        assert router.is_proxy_healthy is False
        assert router.is_proxy_available(DUMMY_PROXY) is False


@pytest.mark.asyncio()
async def test_soap_router_rate_limit_exceeded(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)
    router._direct_healthy = False
    router._last_direct_failure = time.monotonic()  # just failed, do not probe direct

    with (
        patch.object(settings, "soap_proxy_url", DUMMY_PROXY),
        patch.object(settings, "soap_proxy_max_per_minute", 2),
        patch.object(settings, "soap_proxy_min_interval", 0.0),
    ):
        proxy_resp = _mock_response("<soap>ok</soap>")
        session.post.return_value.__aenter__.return_value = proxy_resp

        # 1st call ok
        await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())
        # 2nd call ok
        await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())

        # 3rd call must hit rate limit to protect gateway IP
        with pytest.raises(IettApiError, match="SOAP proxy rate limit exceeded"):
            await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())

        # Throttling must not mark proxy degraded
        assert router.is_proxy_healthy is True


def test_soap_router_cli_arg_override() -> None:
    import sys

    with patch.object(settings, "soap_proxy_url", None):
        with patch.object(sys, "argv", ["app.main", "--soap-proxy-url=http://cli-proxy.test:8080"]):
            assert settings.effective_soap_proxy_url == "http://cli-proxy.test:8080"

        with patch.object(sys, "argv", ["app.main", "--soap-proxy", "http://cli-proxy2.test:8080"]):
            assert settings.effective_soap_proxy_url == "http://cli-proxy2.test:8080"


@pytest.mark.asyncio()
async def test_soap_router_probe_restores_direct(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = _mock_response("<soap>direct restored</soap>")
    session.post.return_value.__aenter__.return_value = mock_resp

    router._direct_healthy = False
    router._last_direct_failure = 0.0  # probe due

    with patch.object(settings, "soap_proxy_url", DUMMY_PROXY):
        res = await router.post(
            session=session,
            url="https://api.ibb.gov.tr/test",
            data=b"test",
            headers={},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        assert res == "<soap>direct restored</soap>"
        assert router.is_direct_healthy is True
