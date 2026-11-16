"""Adaptive SOAP request router with direct-first priority, multi-proxy pool, and per-proxy rate limiting.

Manages routing between direct upstream connections and an optional pool of forward proxies
provided via environment variables / configuration (e.g. SOAP_PROXY_URLS or SOAP_PROXY_URL).

Guiding Principles:
1. Direct connection is ALWAYS preferred whenever reachable.
2. Proxy pool is NEVER hardwired; it is only invoked when direct connection fails.
3. If no proxies are configured, or if all proxies are down/failing, fails gracefully
   so downstream fallbacks (NTCAPI, in-memory cache) can take over.
4. Periodically probes direct connectivity (default 1h; configurable via
   SOAP_DIRECT_PROBE_INTERVAL) to switch back to direct as soon as reachable.
5. Strictly throttles requests PER PROXY (minimum 2.0s interval + 45 req / 120s sliding window)
   to protect each OpenWrt/residential gateway IP from upstream rate-limits or bans.
6. Automatically balances load across available proxies (least-recently-used rotation)
   and isolates failing proxies with exponential cooldown (60s -> 180s -> 600s) without
   penalizing the pool on upstream 5xx responses over established TLS tunnels.
"""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp

from app.config import settings
from app.utils.http import read_bounded_text
from app.utils.lock import LazyLock

logger = logging.getLogger(__name__)


class ProxyEndpoint:
    """Tracks health, spacing, and sliding-window quota for an individual proxy gateway."""

    MAX_COOLDOWN_SECONDS = 600.0

    def __init__(self, url: str) -> None:
        self.url: str = url
        self.is_healthy: bool = True
        self.last_failure: float = 0.0
        self.consecutive_failures: int = 0
        self.last_call: float = 0.0
        self.history: list[float] = []

    def effective_cooldown(self, base_cooldown: float = 60.0) -> float:
        """Compute exponential backoff cooldown for repeated proxy connection failures."""
        exponent = max(0, self.consecutive_failures - 1)
        return min(self.MAX_COOLDOWN_SECONDS, base_cooldown * (3**exponent))

    def mark_success(self) -> None:
        """Mark proxy healthy after an upstream TLS session succeeded."""
        self.is_healthy = True
        self.consecutive_failures = 0
        self.record_call()

    def mark_failure(self, base_cooldown: float = 60.0) -> float:
        """Mark proxy degraded and return applied cooldown seconds."""
        self.is_healthy = False
        self.last_failure = time.monotonic()
        self.consecutive_failures += 1
        return self.effective_cooldown(base_cooldown)

    def is_available(self, cooldown: float | None = None) -> bool:
        """Check if endpoint is healthy or past its failure cooldown."""
        if not self.is_healthy:
            wait = self.effective_cooldown() if cooldown is None else cooldown
            if (time.monotonic() - self.last_failure) < wait:
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
        if settings.soap_direct_probe_interval <= 0:
            return False
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
                return await read_bounded_text(resp)

        # Mode 2: Direct connection attempt (preferred if healthy or due for periodic probe)
        direct_exc: Exception | None = None
        if self.should_probe_direct():
            is_probe = not self._direct_healthy
            conn_cap = 1.5 if is_probe else 2.5
            conn_timeout = conn_cap if timeout.connect is None else min(conn_cap, timeout.connect)
            sock_timeout = (
                conn_cap if timeout.sock_connect is None else min(conn_cap, timeout.sock_connect)
            )
            probe_limit = 3.0 if is_probe else 10.0
            probe_total = (
                min(timeout.total, probe_limit) if timeout.total is not None else probe_limit
            )
            direct_timeout = aiohttp.ClientTimeout(
                total=probe_total if is_probe else timeout.total,
                connect=conn_timeout,
                sock_connect=sock_timeout,
                sock_read=timeout.sock_read,
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
                    text = await read_bounded_text(resp)
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
        proxy_conn_cap = 2.5 if timeout.connect is None else min(2.5, timeout.connect)
        proxy_sock_cap = 2.5 if timeout.sock_connect is None else min(2.5, timeout.sock_connect)
        proxy_timeout = aiohttp.ClientTimeout(
            total=timeout.total,
            connect=proxy_conn_cap,
            sock_connect=proxy_sock_cap,
            sock_read=timeout.sock_read,
        )

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
                    timeout=proxy_timeout,
                    proxy=ep.url,
                ) as resp:
                    resp.raise_for_status()
                    text = await read_bounded_text(resp)
                    ep.mark_success()
                    return text
            except aiohttp.ClientHttpProxyError as proxy_exc:
                cooldown = ep.mark_failure()
                excluded_urls.add(ep.url)
                last_proxy_exc = proxy_exc
                logger.warning(
                    "SOAP proxy %s tunnel failed (%s). Marked degraded for %.0fs; trying next proxy in pool.",
                    ep.url,
                    proxy_exc,
                    cooldown,
                )
            except aiohttp.ClientResponseError as resp_exc:
                # Tunnel succeeded and upstream responded over TLS; only rotate on per-IP 403/429 blocks.
                if resp_exc.status in (403, 429):
                    cooldown = ep.mark_failure()
                    excluded_urls.add(ep.url)
                    last_proxy_exc = resp_exc
                    logger.warning(
                        "SOAP proxy %s rate-limited/blocked by upstream (HTTP %s). Marked degraded for %.0fs; trying next proxy in pool.",
                        ep.url,
                        resp_exc.status,
                        cooldown,
                    )
                else:
                    ep.mark_success()
                    logger.warning(
                        "SOAP upstream returned HTTP %s via proxy %s; not retrying pool.",
                        resp_exc.status,
                        ep.url,
                    )
                    raise
            except Exception as proxy_exc:
                cooldown = ep.mark_failure()
                excluded_urls.add(ep.url)
                last_proxy_exc = proxy_exc
                logger.warning(
                    "SOAP proxy %s failed (%s). Marked degraded for %.0fs; trying next proxy in pool.",
                    ep.url,
                    proxy_exc,
                    cooldown,
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
