"""iett-middle — main FastAPI application entry point."""

from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.deps import cancel_fleet_refresh_task, close_session, set_session
from app.routers import arac, fleet, garages, routes, stops, traffic, announcements
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from app.deps import limiter

logger = logging.getLogger(__name__)
_outgoing = logging.getLogger("iett.outgoing")
_outgoing.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# aiohttp trace hooks — prints every outgoing IETT request in the terminal
# ---------------------------------------------------------------------------


async def _on_request_start(
    session: aiohttp.ClientSession,
    ctx: aiohttp.TraceRequestStartParams,  # type: ignore[name-defined]
    params: aiohttp.TraceRequestStartParams,
) -> None:
    ctx.start_ts = time.monotonic()  # type: ignore[attr-defined]
    action = params.headers.get("SOAPAction", "")
    suffix = f"  {action}" if action else ""
    _outgoing.info("\033[36m→\033[0m %s %s%s", params.method, params.url, suffix)


async def _on_request_end(
    session: aiohttp.ClientSession,
    ctx: aiohttp.TraceRequestEndParams,  # type: ignore[name-defined]
    params: aiohttp.TraceRequestEndParams,
) -> None:
    elapsed_ms = (time.monotonic() - ctx.start_ts) * 1000  # type: ignore[attr-defined]
    status = params.response.status
    size_kb = params.response.content_length
    size_str = f" {size_kb / 1024:.1f}kb" if size_kb else ""
    color = "\033[32m" if status < 400 else "\033[31m"
    _outgoing.info(
        "%s←\033[0m %s %s%s  \033[2m%.0fms\033[0m",
        color,
        status,
        params.url,
        size_str,
        elapsed_ms,
    )


async def _on_request_exception(
    session: aiohttp.ClientSession,
    ctx: aiohttp.TraceRequestExceptionParams,  # type: ignore[name-defined]
    params: aiohttp.TraceRequestExceptionParams,
) -> None:
    elapsed_ms = (time.monotonic() - getattr(ctx, "start_ts", time.monotonic())) * 1000
    _outgoing.warning(
        "\033[31m✗\033[0m %s %s  \033[2m%.0fms\033[0m  %s",
        params.method,
        params.url,
        elapsed_ms,
        params.exception,
    )


def _make_trace_config() -> aiohttp.TraceConfig:
    tc = aiohttp.TraceConfig()
    tc.on_request_start.append(_on_request_start)  # type: ignore[arg-type]
    tc.on_request_end.append(_on_request_end)  # type: ignore[arg-type]
    tc.on_request_exception.append(_on_request_exception)  # type: ignore[arg-type]
    return tc


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    import asyncio  # noqa: PLC0415

    from app.config import settings  # noqa: PLC0415
    from app.services.cache import sweep_forever  # noqa: PLC0415
    from app.services.fleet_poller import refresh_fleet_forever  # noqa: PLC0415
    from app.services.stop_indexer import index_stops_forever  # noqa: PLC0415
    from app.services.notice_poller import notice_poll_loop  # noqa: PLC0415

    connector = aiohttp.TCPConnector(
        resolver=aiohttp.ThreadedResolver() if sys.platform == "win32" else None,
        limit=50,
    )
    trace_configs = [_make_trace_config()] if settings.enable_outgoing_trace else []
    set_session(
        aiohttp.ClientSession(
            connector=connector,
            trace_configs=trace_configs,
            headers={"User-Agent": "iett-middle/1.0 (+https://github.com/pcislocked)"},
        )
    )
    logger.info("HTTP session started")

    # Keep fleet refreshed in the background. The cadence is derived from the
    # maximum tolerated staleness so the coupling stays explicit.
    fleet_refresh_interval = settings.fleet_cache_max_age
    fleet_refresher = asyncio.create_task(refresh_fleet_forever(fleet_refresh_interval))
    stop_indexer = asyncio.create_task(index_stops_forever())
    cache_sweeper = asyncio.create_task(sweep_forever())
    notice_poller = asyncio.create_task(notice_poll_loop())

    yield

    cache_sweeper.cancel()
    try:
        await cache_sweeper
    except asyncio.CancelledError:
        pass

    notice_poller.cancel()
    try:
        await notice_poller
    except asyncio.CancelledError:
        pass

    fleet_refresher.cancel()
    try:
        await fleet_refresher
    except asyncio.CancelledError:
        pass

    stop_indexer.cancel()
    try:
        await stop_indexer
    except asyncio.CancelledError:
        pass

    await cancel_fleet_refresh_task()
    await close_session()
    logger.info("HTTP session closed")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="iett-middle",
    description="Smart caching proxy for IETT Istanbul public transit APIs.",
    version="0.4.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-IETT-Updated-At"],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


@app.middleware("http")
async def add_cache_timestamp_header(request: Request, call_next):
    from app.services.cache import cache_hit_time
    import datetime

    container = {}
    token = cache_hit_time.set(container)
    try:
        response = await call_next(request)
        hit_time = container.get("hit_time")
        if hit_time is None:
            val = cache_hit_time.get()
            if isinstance(val, (int, float)):
                hit_time = val
        if hit_time is not None:
            iso_time = datetime.datetime.fromtimestamp(
                hit_time, tz=datetime.timezone.utc
            ).isoformat()
            response.headers["X-IETT-Updated-At"] = iso_time
        return response
    finally:
        cache_hit_time.reset(token)


app.include_router(stops.router, prefix="/v1/stops", tags=["stops"])
app.include_router(routes.router, prefix="/v1/routes", tags=["routes"])
app.include_router(fleet.router, prefix="/v1/fleet", tags=["fleet"])
app.include_router(arac.router, prefix="/v1/arac", tags=["arac"])
app.include_router(garages.router, prefix="/v1/garages", tags=["garages"])
app.include_router(traffic.router, prefix="/v1/traffic", tags=["traffic"])
app.include_router(
    announcements.router, prefix="/v1/announcements", tags=["announcements"]
)


@app.get("/health", tags=["health"])
async def health():
    from app.services.cache import get_cache_stats

    return {"status": "ok", "cache": get_cache_stats()}


@app.get("/", include_in_schema=False)
async def root():
    return {"service": "iett-middle", "docs": "/docs"}
