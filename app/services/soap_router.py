"""Adaptive SOAP request router with direct-first priority, multi-proxy pool, and per-proxy rate limiting.

Manages routing between direct upstream connections and an optional pool of forward proxies
provided via environment variables / configuration (e.g. SOAP_PROXY_URLS or SOAP_PROXY_URL).

Guiding Principles:
1. Direct connection is ALWAYS preferred whenever reachable.
2. Proxy pool is NEVER hardwired; it is only invoked when direct connection fails.
3. If no proxies are configured, or if all proxies are down/failing, fails gracefully
   so downstream fallbacks (NTCAPI, in-memory cache) can take over.
4. Periodically probes direct connectivity (every 5 min) to switch back to direct as soon as reachable.
5. Strictly throttles requests PER PROXY (minimum 2.0s interval + 45 req / 120s sliding window)
   to protect each OpenWrt/residential gateway IP from upstream rate-limits or bans.
6. Automatically balances load across available proxies (least-recently-used rotation)
   and isolates failing proxies with a 60-second cooldown without affecting the rest of the pool.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from app.config import settings
from app.utils.lock import LazyLock

logger = logging.getLogger(__name__)


class ProxyEndpoint:
    """Tracks health, spacing, and sliding-window quota for an individual proxy gateway."""

    def __init__(self, url: str) -> None:
        self.url: str = url
        self.is_healthy: bool = True
        self.last_failure: float = 0.0
        self.last_call: float = 0.0
        self.history: list[float] = []

    def is_available(self, cooldown: float = 60.0) -> bool:
        """Check if endpoint is healthy or past its failure cooldown."""
        if not self.is_healthy:
            if (time.monotonic() - self.last_failure) < cooldown:
                return False
        return True

    def evaluate_rate_limit(
        self,
        min_interval: float,
        max_per_window: int,
        window_seconds: float,
    ) -> tuple[bool, float]:
        """Check rate limits for this endpoint.

        Returns (can_proceed, wait_seconds).
        - If quota is exceeded, returns (False, wait_seconds_until_slot_freed).
        - If quota is available but spacing requires wait, returns (True, spacing_wait).
        - If ready immediately, returns (True, 0.0).
        """
        now = time.monotonic()
        # 1. Purge entries outside window
        self.history = [t for t in self.history if now - t < window_seconds]

        # 2. Check window capacity
        if len(self.history) >= max_per_window:
            oldest = self.history[0]
            slot_wait = max(0.0, window_seconds - (now - oldest))
            return False, slot_wait

        # 3. Check minimum spacing
        elapsed = now - self.last_call
        if elapsed < min_interval:
            return True, min_interval - elapsed

        return True, 0.0

    def record_call(self) -> None:
        """Record successful invocation timestamp."""
        now = time.monotonic()
        self.last_call = now
        self.history.append(now)


class SoapRouteManager:
    """Manages adaptive direct-first routing and resilient proxy pool for IBB SOAP."""

    def __init__(self) -> None:
        self._direct_healthy: bool = True
        self._last_direct_failure: float = 0.0
        self._endpoints: dict[str, ProxyEndpoint] = {}
        self._lock = LazyLock()

    @property
    def is_direct_healthy(self) -> bool:
        """Return True if direct upstream is currently assumed healthy."""
        return self._direct_healthy

    def get_endpoint(self, url: str) -> ProxyEndpoint:
        """Get or initialize state tracker for a proxy URL."""
        if url not in self._endpoints:
            self._endpoints[url] = ProxyEndpoint(url)
        return self._endpoints[url]

    def should_probe_direct(self) -> bool:
        """Check if direct connection should be attempted."""
        if self._direct_healthy:
            return True
        now = time.monotonic()
        return (now - self._last_direct_failure) >= settings.soap_direct_probe_interval

    async def _select_available_proxy(
        self,
        candidate_urls: list[str],
        excluded_urls: set[str],
    ) -> tuple[ProxyEndpoint, float]:
        """Select the least-recently-used available proxy with per-proxy quota enforcement."""
        async with self._lock:
            available_eps: list[ProxyEndpoint] = []
            for url in candidate_urls:
                if url in excluded_urls:
                    continue
                ep = self.get_endpoint(url)
                if ep.is_available():
                    available_eps.append(ep)

            if not available_eps:
                from app.services.iett_client import IettApiError

                raise IettApiError(
                    "No healthy SOAP proxy available in pool (all degraded or cooling down)."
                )

            min_interval = max(0.0, settings.soap_proxy_min_interval)
            max_per_window = max(1, settings.soap_proxy_max_per_window)
            window_seconds = max(1.0, settings.soap_proxy_window_seconds)

            # Check which endpoints have capacity in their sliding window
            ready_candidates: list[tuple[ProxyEndpoint, float]] = []
            for ep in available_eps:
                can_proceed, wait_sec = ep.evaluate_rate_limit(
                    min_interval=min_interval,
                    max_per_window=max_per_window,
                    window_seconds=window_seconds,
                )
                if can_proceed:
                    ready_candidates.append((ep, wait_sec))

            if not ready_candidates:
                from app.services.iett_client import IettApiError

                logger.warning(
                    "All SOAP proxies in pool exceeded rate limit (%s req / %ss). Throttling proxy calls to protect gateways.",
                    max_per_window,
                    window_seconds,
                )
                raise IettApiError(
                    f"SOAP proxy pool rate limit exceeded ({max_per_window} req / {window_seconds:.0f}s per gateway). "
                    "Protecting residential gateways."
                )

            # Among candidates with quota, pick the least-recently-used (coldest gateway)
            # Break ties with smallest required spacing wait
            best_ep, best_wait = min(
                ready_candidates, key=lambda pair: (pair[1], pair[0].last_call)
            )
            return best_ep, best_wait

    async def post(
        self,
        session: aiohttp.ClientSession,
        url: str,
        data: bytes,
        headers: dict[str, str],
        timeout: aiohttp.ClientTimeout,
    ) -> str:
        """Execute SOAP POST request with direct-first priority and resilient multi-proxy fallback."""
        proxy_urls = settings.effective_soap_proxy_urls

        # Mode 1: No proxies configured — pure direct call
        if not proxy_urls:
            direct_headers = dict(headers)
            direct_headers.setdefault("User-Agent", settings.user_agent(via_proxy=False))
            async with session.post(
                url, data=data, headers=direct_headers, timeout=timeout
            ) as resp:
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
            direct_headers = dict(headers)
            direct_headers["User-Agent"] = settings.user_agent(via_proxy=False)
            try:
                async with session.post(
                    url,
                    data=data,
                    headers=direct_headers,
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
                logger.warning("SOAP direct failed (%s). Attempting proxy pool fallback.", exc)

        # Mode 3: Proxy pool fallback
        excluded_urls: set[str] = set()
        last_proxy_exc: Exception | None = None

        while len(excluded_urls) < len(proxy_urls):
            try:
                ep, wait_sec = await self._select_available_proxy(proxy_urls, excluded_urls)
            except Exception as select_exc:
                last_proxy_exc = select_exc
                break

            if wait_sec > 0:
                logger.debug("Spacing requests for proxy %s by %.2fs", ep.url, wait_sec)
                await asyncio.sleep(wait_sec)

            proxy_headers = dict(headers)
            proxy_headers["User-Agent"] = settings.user_agent(via_proxy=True)

            try:
                async with session.post(
                    url,
                    data=data,
                    headers=proxy_headers,
                    timeout=timeout,
                    proxy=ep.url,
                ) as resp:
                    resp.raise_for_status()
                    text = await resp.text()
                    ep.is_healthy = True
                    ep.record_call()
                    return text
            except Exception as proxy_exc:
                ep.is_healthy = False
                ep.last_failure = time.monotonic()
                excluded_urls.add(ep.url)
                last_proxy_exc = proxy_exc
                logger.warning(
                    "SOAP proxy %s failed (%s). Marked degraded for 60s; trying next proxy in pool.",
                    ep.url,
                    proxy_exc,
                )

        from app.services.iett_client import IettApiError

        err_msg = (
            f"SOAP upstream unavailable (direct: {direct_exc}, proxies: {last_proxy_exc})"
            if direct_exc
            else f"SOAP proxy call failed: {last_proxy_exc}"
        )
        raise IettApiError(err_msg)


# Global singleton instance
soap_router = SoapRouteManager()
