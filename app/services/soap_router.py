"""Adaptive SOAP request router with direct-first priority, fallback proxy, and rate limiting.

Manages routing between direct upstream connections and an optional forward proxy
provided via environment variables / configuration (e.g. SOAP_PROXY_URL).

Guiding Principles:
1. Direct connection is ALWAYS preferred whenever reachable.
2. Proxy is NEVER hardwired; it is only invoked when direct connection fails.
3. If proxy is not configured, or if proxy itself is down/failing, fails gracefully
   so downstream fallbacks (NTCAPI, in-memory cache) can take over.
4. Periodically probes direct connectivity to switch back as soon as direct recovers.
5. Strictly throttles proxy requests (minimum interval + sliding per-minute window)
   to protect the proxy gateway IP from upstream rate-limits or bans.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from app.config import settings
from app.utils.lock import LazyLock

logger = logging.getLogger(__name__)


class SoapRouteManager:
    """Manages adaptive direct-first routing and resilient proxy fallback for IBB SOAP."""

    def __init__(self) -> None:
        self._direct_healthy: bool = True
        self._proxy_healthy: bool = True
        self._last_direct_failure: float = 0.0
        self._last_proxy_failure: float = 0.0
        self._last_proxy_call: float = 0.0
        self._proxy_history: list[float] = []
        self._lock = LazyLock()

    @property
    def is_direct_healthy(self) -> bool:
        """Return True if direct upstream is currently assumed healthy."""
        return self._direct_healthy

    @property
    def is_proxy_healthy(self) -> bool:
        """Return True if proxy is currently assumed healthy."""
        return self._proxy_healthy

    def should_probe_direct(self) -> bool:
        """Check if direct connection should be attempted."""
        if self._direct_healthy:
            return True
        now = time.monotonic()
        return (now - self._last_direct_failure) >= settings.soap_direct_probe_interval

    def is_proxy_available(self, proxy_url: str | None) -> bool:
        """Check if a proxy URL is configured and not in cooldown after recent failure."""
        if not proxy_url or not proxy_url.strip():
            return False
        if not self._proxy_healthy:
            # 60s cooldown before retrying a broken proxy
            if (time.monotonic() - self._last_proxy_failure) < 60.0:
                return False
        return True

    async def _throttle_proxy(self) -> None:
        """Enforce strict spacing and quota limits on proxy calls to prevent gateway bans."""
        async with self._lock:
            now = time.monotonic()
            # 1. Purge entries older than 60 seconds
            self._proxy_history = [t for t in self._proxy_history if now - t < 60.0]

            # 2. Enforce per-minute limit
            max_per_min = max(1, settings.soap_proxy_max_per_minute)
            if len(self._proxy_history) >= max_per_min:
                logger.warning(
                    "SOAP proxy quota reached (%s/min). Throttling proxy calls to protect gateway IP.",
                    max_per_min,
                )
                from app.services.iett_client import IettApiError

                raise IettApiError(
                    f"SOAP proxy rate limit exceeded ({max_per_min}/min). Protecting proxy gateway."
                )

            # 3. Enforce minimum spacing between consecutive calls
            min_interval = max(0.0, settings.soap_proxy_min_interval)
            elapsed = now - self._last_proxy_call
            if elapsed < min_interval:
                wait_time = min_interval - elapsed
                logger.debug("Spacing proxy requests by %.2fs", wait_time)
                await asyncio.sleep(wait_time)

            self._last_proxy_call = time.monotonic()
            self._proxy_history.append(self._last_proxy_call)

    async def post(
        self,
        session: aiohttp.ClientSession,
        url: str,
        data: bytes,
        headers: dict[str, str],
        timeout: aiohttp.ClientTimeout,
    ) -> str:
        """Execute SOAP POST request with direct-first priority and resilient fallback."""
        raw_proxy = settings.effective_soap_proxy_url
        proxy_url = raw_proxy.strip() if raw_proxy and raw_proxy.strip() else None

        # Mode 1: No proxy configured — pure direct call
        if not proxy_url:
            async with session.post(url, data=data, headers=headers, timeout=timeout) as resp:
                resp.raise_for_status()
                return await resp.text()

        # Mode 2: Direct connection attempt (preferred if healthy or due for periodic probe)
        direct_exc: Exception | None = None
        if self.should_probe_direct():
            is_probe = not self._direct_healthy
            conn_timeout = 2.5 if timeout.connect is None else min(2.5, timeout.connect)
            sock_timeout = 2.5 if timeout.sock_connect is None else min(2.5, timeout.sock_connect)
            direct_timeout = aiohttp.ClientTimeout(
                total=4.0 if is_probe else timeout.total,
                connect=conn_timeout,
                sock_connect=sock_timeout,
            )
            try:
                async with session.post(
                    url,
                    data=data,
                    headers=headers,
                    timeout=direct_timeout,
                ) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
                    if is_probe:
                        logger.info("SOAP direct connection restored! Resuming direct routing.")
                        self._direct_healthy = True
                    return text
            except Exception as exc:
                self._direct_healthy = False
                self._last_direct_failure = time.monotonic()
                direct_exc = exc
                logger.warning("SOAP direct failed (%s). Attempting proxy fallback.", exc)

        # Mode 3: Proxy fallback
        if self.is_proxy_available(proxy_url):
            await self._throttle_proxy()
            try:
                async with session.post(
                    url,
                    data=data,
                    headers=headers,
                    timeout=timeout,
                    proxy=proxy_url,
                ) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
                    self._proxy_healthy = True
                    return text
            except Exception as proxy_exc:
                self._proxy_healthy = False
                self._last_proxy_failure = time.monotonic()
                logger.warning("SOAP proxy call failed (%s). Proxy marked degraded.", proxy_exc)
                from app.services.iett_client import IettApiError

                raise IettApiError(f"SOAP proxy call failed: {proxy_exc}") from proxy_exc

        # If both direct and proxy are unavailable, raise direct failure or generic error
        from app.services.iett_client import IettApiError

        err_msg = (
            f"SOAP upstream unavailable: {direct_exc}"
            if direct_exc
            else "SOAP upstream unavailable"
        )
        raise IettApiError(err_msg)


# Global singleton instance
soap_router = SoapRouteManager()
