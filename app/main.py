"""iett-middle — main FastAPI application entry point."""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import fleet, routes, stops, traffic

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# aiohttp session — Windows-safe (avoids aiodns ProactorEventLoop crash)
# ---------------------------------------------------------------------------
_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    if _session is None:
        raise RuntimeError("HTTP session not initialised")
    return _session


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    global _session  # noqa: PLW0603
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.ThreadedResolver() if sys.platform == "win32" else None,
        limit=50,
    )
    _session = aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": "iett-middle/1.0 (+https://github.com/pcislocked)"},
    )
    logger.info("HTTP session started")
    yield
    await _session.close()
    logger.info("HTTP session closed")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="iett-middle",
    description="Smart caching proxy for IETT Istanbul public transit APIs.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(stops.router, prefix="/v1/stops", tags=["stops"])
app.include_router(routes.router, prefix="/v1/routes", tags=["routes"])
app.include_router(fleet.router, prefix="/v1/fleet", tags=["fleet"])
app.include_router(traffic.router, prefix="/v1/traffic", tags=["traffic"])


@app.get("/health", tags=["health"])
async def health():
    from app.services.cache import get_cache_stats

    return {"status": "ok", "cache": get_cache_stats()}


@app.get("/", include_in_schema=False)
async def root():
    return {"service": "iett-middle", "docs": "/docs"}
