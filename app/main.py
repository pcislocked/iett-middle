"""iett-middle — main FastAPI application entry point."""
from __future__ import annotations

import logging
import sys
import time
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.deps import close_session, set_session
from app.routers import fleet, garages, routes, stops, traffic

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
        color, status, params.url, size_str, elapsed_ms,
    )


async def _on_request_exception(
    session: aiohttp.ClientSession,
    ctx: aiohttp.TraceRequestExceptionParams,  # type: ignore[name-defined]
    params: aiohttp.TraceRequestExceptionParams,
) -> None:
    elapsed_ms = (time.monotonic() - getattr(ctx, "start_ts", time.monotonic())) * 1000
    _outgoing.warning(
        "\033[31m✗\033[0m %s %s  \033[2m%.0fms\033[0m  %s",
        params.method, params.url, elapsed_ms, params.exception,
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

    from app.services.fleet_poller import poll_fleet_forever  # noqa: PLC0415
    from app.services.stop_indexer import index_stops_forever  # noqa: PLC0415

    connector = aiohttp.TCPConnector(
        resolver=aiohttp.ThreadedResolver() if sys.platform == "win32" else None,
        limit=50,
    )
    set_session(aiohttp.ClientSession(
        connector=connector,
        trace_configs=[_make_trace_config()],
        headers={"User-Agent": "iett-middle/1.0 (+https://github.com/pcislocked)"},
    ))
    logger.info("HTTP session started")

    poller = asyncio.create_task(poll_fleet_forever())
    stop_indexer = asyncio.create_task(index_stops_forever())

    yield

    poller.cancel()
    stop_indexer.cancel()
    for task in (poller, stop_indexer):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await close_session()
    logger.info("HTTP session closed")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="iett-middle",
    description="Smart caching proxy for IETT Istanbul public transit APIs.",
    version="0.1.4",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(stops.router, prefix="/v1/stops", tags=["stops"])
app.include_router(routes.router, prefix="/v1/routes", tags=["routes"])
app.include_router(fleet.router, prefix="/v1/fleet", tags=["fleet"])
app.include_router(garages.router, prefix="/v1/garages", tags=["garages"])
app.include_router(traffic.router, prefix="/v1/traffic", tags=["traffic"])


@app.get("/health", tags=["health"])
async def health():
    from app.services.cache import get_cache_stats

    return {"status": "ok", "cache": get_cache_stats()}


@app.get("/", include_in_schema=False)
async def root():
    return {"service": "iett-middle", "docs": "/docs"}
