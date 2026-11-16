"""Tests for SoapRouteManager (adaptive direct-first routing, multi-proxy pool, per-proxy rate limiting)."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
import pytest

from app.config import settings
from app.services.iett_client import IettApiError
from app.services.soap_router import SoapRouteManager

DUMMY_PROXY_1 = "http://proxy1.test:8080"
DUMMY_PROXY_2 = "http://proxy2.test:8080"


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

    with (
        patch.object(settings, "soap_proxy_urls", None),
        patch.object(settings, "soap_proxy_url", None),
    ):
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
async def test_soap_router_direct_preferred_when_proxies_configured(
    router: SoapRouteManager,
) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = _mock_response("<soap>direct ok</soap>")
    session.post.return_value.__aenter__.return_value = mock_resp

    with patch.object(settings, "soap_proxy_urls", f"{DUMMY_PROXY_1},{DUMMY_PROXY_2}"):
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
        assert "via proxy" not in kwargs["headers"]["User-Agent"]
        assert "PROXY_NOTICE.md" in kwargs["headers"]["User-Agent"]


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
        patch.object(settings, "soap_proxy_urls", DUMMY_PROXY_1),
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

        # Second call must have used the proxy with via proxy UA
        _, proxy_kwargs = session.post.call_args_list[1]
        assert proxy_kwargs["proxy"] == DUMMY_PROXY_1
        assert "via proxy" in proxy_kwargs["headers"]["User-Agent"]
        assert "PROXY_NOTICE.md" in proxy_kwargs["headers"]["User-Agent"]


@pytest.mark.asyncio()
async def test_soap_router_multi_proxy_rotation(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)
    router._direct_healthy = False
    router._last_direct_failure = time.monotonic()

    with (
        patch.object(settings, "soap_proxy_urls", f"{DUMMY_PROXY_1},{DUMMY_PROXY_2}"),
        patch.object(settings, "soap_proxy_min_interval", 0.0),
    ):
        p1_resp = _mock_response("<soap>p1 ok</soap>")
        p2_resp = _mock_response("<soap>p2 ok</soap>")
        session.post.side_effect = [
            AsyncMock(__aenter__=AsyncMock(return_value=p1_resp), __aexit__=AsyncMock()),
            AsyncMock(__aenter__=AsyncMock(return_value=p2_resp), __aexit__=AsyncMock()),
        ]

        # Call 1 -> uses proxy 1
        res1 = await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())
        assert res1 == "<soap>p1 ok</soap>"
        assert session.post.call_args_list[0][1]["proxy"] == DUMMY_PROXY_1

        # Call 2 -> rotates to proxy 2 (least recently used)
        res2 = await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())
        assert res2 == "<soap>p2 ok</soap>"
        assert session.post.call_args_list[1][1]["proxy"] == DUMMY_PROXY_2


@pytest.mark.asyncio()
async def test_soap_router_multi_proxy_failover(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)
    router._direct_healthy = False
    router._last_direct_failure = time.monotonic()

    with (
        patch.object(settings, "soap_proxy_urls", f"{DUMMY_PROXY_1},{DUMMY_PROXY_2}"),
        patch.object(settings, "soap_proxy_min_interval", 0.0),
    ):
        p2_resp = _mock_response("<soap>p2 ok</soap>")
        # Proxy 1 fails, proxy 2 succeeds
        session.post.side_effect = [
            aiohttp.ClientConnectorError(
                connection_key=Mock(), os_error=OSError("Proxy 1 connection refused")
            ),
            AsyncMock(__aenter__=AsyncMock(return_value=p2_resp), __aexit__=AsyncMock()),
        ]

        res = await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())
        assert res == "<soap>p2 ok</soap>"
        assert session.post.call_count == 2
        assert session.post.call_args_list[0][1]["proxy"] == DUMMY_PROXY_1
        assert session.post.call_args_list[1][1]["proxy"] == DUMMY_PROXY_2

        # Proxy 1 must be marked degraded
        ep1 = router.get_endpoint(DUMMY_PROXY_1)
        assert ep1.is_healthy is False
        assert ep1.is_available() is False

        # Proxy 2 must remain healthy
        ep2 = router.get_endpoint(DUMMY_PROXY_2)
        assert ep2.is_healthy is True


@pytest.mark.asyncio()
async def test_soap_router_per_proxy_rate_limit(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)
    router._direct_healthy = False
    router._last_direct_failure = time.monotonic()

    with (
        patch.object(settings, "soap_proxy_urls", f"{DUMMY_PROXY_1},{DUMMY_PROXY_2}"),
        patch.object(settings, "soap_proxy_max_per_window", 1),
        patch.object(settings, "soap_proxy_window_seconds", 120.0),
        patch.object(settings, "soap_proxy_min_interval", 0.0),
    ):
        resp = _mock_response("<soap>ok</soap>")
        session.post.return_value.__aenter__.return_value = resp

        # 1st call -> uses P1 (quota now 1/1)
        await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())
        assert session.post.call_args_list[0][1]["proxy"] == DUMMY_PROXY_1

        # 2nd call -> uses P2 because P1 reached quota! (P2 quota now 1/1)
        await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())
        assert session.post.call_args_list[1][1]["proxy"] == DUMMY_PROXY_2

        # 3rd call -> both P1 and P2 exhausted their 1 req / window limit!
        with pytest.raises(IettApiError, match="SOAP proxy pool rate limit exceeded"):
            await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())


@pytest.mark.asyncio()
async def test_soap_router_probe_restores_direct(router: SoapRouteManager) -> None:
    session = AsyncMock(spec=aiohttp.ClientSession)
    mock_resp = _mock_response("<soap>direct restored</soap>")
    session.post.return_value.__aenter__.return_value = mock_resp

    router._direct_healthy = False
    router._last_direct_failure = time.monotonic() - (
        settings.soap_direct_probe_interval + 10.0
    )  # probe due

    with patch.object(settings, "soap_proxy_urls", DUMMY_PROXY_1):
        res = await router.post(
            session=session,
            url="https://api.ibb.gov.tr/test",
            data=b"test",
            headers={},
            timeout=aiohttp.ClientTimeout(total=5.0),
        )
        assert res == "<soap>direct restored</soap>"
        assert router.is_direct_healthy is True


def test_soap_router_cli_arg_override() -> None:
    import sys

    with (
        patch.object(settings, "soap_proxy_urls", None),
        patch.object(settings, "soap_proxy_url", None),
    ):
        with patch.object(
            sys, "argv", ["app.main", "--soap-proxy-urls=http://p1.test:8080, http://p2.test:8080"]
        ):
            assert settings.effective_soap_proxy_urls == [
                "http://p1.test:8080",
                "http://p2.test:8080",
            ]

        with patch.object(sys, "argv", ["app.main", "--soap-proxy", "http://p3.test:8080"]):
            assert settings.effective_soap_proxy_urls == ["http://p3.test:8080"]


@pytest.mark.asyncio()
async def test_soap_router_upstream_503_does_not_cascade_proxy_pool(
    router: SoapRouteManager,
) -> None:
    """Upstream 500/503 over TLS must NOT mark proxies degraded or stampede remaining proxies."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    router._direct_healthy = False
    router._last_direct_failure = time.monotonic()

    err_503 = aiohttp.ClientResponseError(
        request_info=Mock(),
        history=(),
        status=503,
        message="Service Unavailable",
    )
    resp = Mock()
    resp.raise_for_status = Mock(side_effect=err_503)
    session.post.return_value.__aenter__.return_value = resp

    with (
        patch.object(settings, "soap_proxy_urls", f"{DUMMY_PROXY_1},{DUMMY_PROXY_2}"),
        patch.object(settings, "soap_proxy_min_interval", 0.0),
        patch.object(settings, "soap_direct_probe_interval", 3600.0),
    ):
        with pytest.raises(aiohttp.ClientResponseError) as exc_info:
            await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())
        assert exc_info.value.status == 503

    # Only 1 proxy attempted; pool stays healthy
    assert session.post.call_count == 1
    assert router.get_endpoint(DUMMY_PROXY_1).is_healthy is True
    assert router.get_endpoint(DUMMY_PROXY_2).is_healthy is True


@pytest.mark.asyncio()
async def test_soap_router_proxy_tunnel_error_and_429_failover(
    router: SoapRouteManager,
) -> None:
    """ClientHttpProxyError (CONNECT failure) and HTTP 429 trigger proxy degradation & failover."""
    session = AsyncMock(spec=aiohttp.ClientSession)
    router._direct_healthy = False
    router._last_direct_failure = time.monotonic()

    tunnel_err = aiohttp.ClientHttpProxyError(
        request_info=Mock(), history=(), status=502, message="Bad Gateway"
    )
    resp_ok = _mock_response("<soap>ok</soap>")
    session.post.side_effect = [
        tunnel_err,
        AsyncMock(__aenter__=AsyncMock(return_value=resp_ok), __aexit__=AsyncMock()),
    ]

    with (
        patch.object(settings, "soap_proxy_urls", f"{DUMMY_PROXY_1},{DUMMY_PROXY_2}"),
        patch.object(settings, "soap_proxy_min_interval", 0.0),
        patch.object(settings, "soap_direct_probe_interval", 3600.0),
    ):
        res = await router.post(session, "https://test", b"", {}, aiohttp.ClientTimeout())
        assert res == "<soap>ok</soap>"

    assert router.get_endpoint(DUMMY_PROXY_1).is_healthy is False
    assert router.get_endpoint(DUMMY_PROXY_2).is_healthy is True

    # Also test HTTP 429 per-IP throttling rotates proxy
    session2 = AsyncMock(spec=aiohttp.ClientSession)
    err_429 = aiohttp.ClientResponseError(
        request_info=Mock(), history=(), status=429, message="Too Many Requests"
    )
    resp_429 = Mock()
    resp_429.raise_for_status = Mock(side_effect=err_429)
    session2.post.return_value.__aenter__.return_value = resp_429

    with (
        patch.object(settings, "soap_proxy_urls", DUMMY_PROXY_2),
        patch.object(settings, "soap_proxy_min_interval", 0.0),
        patch.object(settings, "soap_direct_probe_interval", 3600.0),
    ):
        with pytest.raises(IettApiError, match="SOAP proxy call failed"):
            await router.post(session2, "https://test", b"", {}, aiohttp.ClientTimeout())
    assert router.get_endpoint(DUMMY_PROXY_2).is_healthy is False


def test_soap_router_exponential_cooldown_and_disabled_probe(router: SoapRouteManager) -> None:
    ep = router.get_endpoint(DUMMY_PROXY_1)
    assert ep.mark_failure(base_cooldown=60.0) == 60.0
    assert ep.mark_failure(base_cooldown=60.0) == 180.0
    assert ep.mark_failure(base_cooldown=60.0) == 540.0
    assert ep.mark_failure(base_cooldown=60.0) == 600.0
    ep.mark_success()
    assert ep.consecutive_failures == 0
    assert ep.is_healthy is True

    router._direct_healthy = False
    router._last_direct_failure = 0.0
    with patch.object(settings, "soap_direct_probe_interval", 0.0):
        assert router.should_probe_direct() is False
