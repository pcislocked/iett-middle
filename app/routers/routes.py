"""Routes router — /v1/routes"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.deps import get_session
from app.models.bus import BusPosition
from app.models.route import Announcement, RouteMetadata, RouteSearchResult, ScheduledDeparture
from app.models.stop import RouteStop
from app.services.cache import cache_get, cache_set
from app.services.iett_client import IettApiError, IettClient
from app.services import normalizers, ntcapi_client
from app.services.ntcapi_client import NtcApiError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/search", response_model=list[RouteSearchResult])
async def search_routes(q: str = Query(..., min_length=1)):
    """Search routes by name or code (e.g. '14M', 'kadikoy')."""
    key = f"routes:search:{q.lower()}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        results = await client.search_routes(q)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [r.model_dump() for r in results]
    await cache_set(key, data, settings.cache_ttl_search)
    return results


@router.get("/{hat_kodu}", response_model=list[RouteMetadata])
async def get_route_metadata(hat_kodu: str):
    """Route variant/direction metadata.

    ntcapi ``mainGetLine`` is the primary source; IETT SOAP ``GetAllRoute``
    is the fallback.
    """
    key = f"routes:meta:{hat_kodu}"
    cached = await cache_get(key)
    if cached is not None:
        return cached

    session = get_session()
    data: list[dict] = []

    # ── primary: ntcapi mainGetLine ─────────────────────────────────
    try:
        raw_meta = await ntcapi_client.get_route_metadata(hat_kodu, session)
        data = raw_meta  # already in RouteMetadata shape
    except NtcApiError as exc:
        logger.warning("ntcapi metadata failed for %s, falling back to IETT SOAP: %s", hat_kodu, exc)

    # ── fallback: IETT SOAP GetAllRoute ────────────────────────────
    if not data:
        client = IettClient(session)
        try:
            meta = await client.get_route_metadata(hat_kodu)
        except IettApiError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        data = [m.model_dump() for m in meta]

    await cache_set(key, data, settings.cache_ttl_search)
    return data


@router.get("/{hat_kodu}/buses", response_model=list[BusPosition])
async def get_route_buses(hat_kodu: str):
    """GPS positions of buses on a route.

    Primary: ntcapi ybs point-passing/{hat_id} — includes stop_sequence per bus.
    Secondary: IETT GetHatOtoKonum_json SOAP.
    Fallback: filters in-memory fleet store by route_code (stale-while-revalidate).
    """
    from app.deps import ensure_fleet_fresh, get_buses_by_route  # noqa: PLC0415

    session = get_session()

    # ── primary: ntcapi ybs point-passing ──────────────────────────────────
    try:
        # HAT_ID is returned by mainGetLine_basic and cached with route metadata.
        # Check the metadata cache first to avoid an extra round-trip.
        meta_key = f"routes:meta:{hat_kodu}"
        meta_cached = await cache_get(meta_key)
        hat_id: int | None = None
        if meta_cached and isinstance(meta_cached, list):
            hat_id = next((m.get("hat_id") for m in meta_cached if isinstance(m, dict) and m.get("hat_id")), None)
        if hat_id is None:
            raw_meta = await ntcapi_client.get_route_metadata(hat_kodu, session)
            hat_id = next((m.get("hat_id") for m in raw_meta if m.get("hat_id")), None)
            if raw_meta:
                await cache_set(meta_key, raw_meta, settings.cache_ttl_search)
        if hat_id is not None:
            buses = await ntcapi_client.get_route_buses_ybs(hat_id, hat_kodu, session)
            if buses:
                return buses
    except (NtcApiError, Exception) as exc:  # noqa: BLE001
        logger.warning("ybs point-passing failed for %s, falling back to SOAP: %s", hat_kodu, exc)

    # ── secondary: IETT SOAP GetHatOtoKonum ────────────────────────────────
    try:
        client = IettClient(session)
        buses = await client.get_route_buses(hat_kodu)
        if buses:
            return buses
    except (IettApiError, Exception) as exc:  # noqa: BLE001
        logger.warning("get_route_buses SOAP failed for %s, falling back to fleet: %s", hat_kodu, exc)

    # ── last resort: in-memory fleet ───────────────────────────────────────
    await ensure_fleet_fresh()
    return get_buses_by_route(hat_kodu)


@router.get("/{hat_kodu}/stops", response_model=list[RouteStop])
async def get_route_stops(hat_kodu: str):
    """Ordered stop list for a route with coordinates.

    ntcapi ``mainGetRoute`` is the primary source (fetches both directions);
    IETT SOAP ``GetHatDuraklari`` is the fallback.
    """
    key = f"routes:stops:{hat_kodu}"
    cached = await cache_get(key)
    if cached is not None:
        return cached

    session = get_session()
    stops: list[RouteStop] = []

    # ── primary: ntcapi mainGetRoute (both directions) ──────────────
    try:
        raw_g, raw_d = await asyncio.gather(
            ntcapi_client.get_route_stops(hat_kodu, "G", session),
            ntcapi_client.get_route_stops(hat_kodu, "D", session),
        )
        canonical = [
            normalizers.route_stops.from_ntcapi_route_processed(r)
            for r in raw_g + raw_d
        ]
        stops = [
            RouteStop(
                route_code=c.get("route_code") or hat_kodu,
                direction=c.get("direction") or "G",
                sequence=c.get("sequence") or 0,
                stop_code=c.get("stop_code") or "",
                stop_name=c.get("stop_name") or "",
                latitude=c.get("lat"),
                longitude=c.get("lon"),
                district=c.get("district"),
            )
            for c in canonical
        ]
    except NtcApiError as exc:
        logger.warning("ntcapi stops failed for %s, falling back to IETT SOAP: %s", hat_kodu, exc)

    # ── fallback: IETT SOAP ─────────────────────────────────────────
    if not stops:
        client = IettClient(session)
        try:
            stops = await client.get_route_stops(hat_kodu)
        except IettApiError as exc:
            raise HTTPException(502, detail=str(exc)) from exc

    data = [s.model_dump() for s in stops]
    if stops and all(s.latitude is not None for s in stops):
        await cache_set(key, data, settings.cache_ttl_stops)
    return stops


@router.get("/{hat_kodu}/schedule", response_model=list[ScheduledDeparture])
async def get_route_schedule(hat_kodu: str):
    """Planned departure times for a route (all day types).

    ntcapi ``akyolbilGetTimeTable`` is the primary source;
    IETT SOAP is the fallback.
    """
    key = f"routes:schedule:{hat_kodu}"
    cached = await cache_get(key)
    if cached is not None:
        return cached

    session = get_session()
    data: list[dict] = []

    # ── primary: ntcapi akyolbilGetTimeTable ─────────────────────────
    try:
        raw_tt = await ntcapi_client.get_timetable(hat_kodu, session)
        canonical = [normalizers.schedule.from_ntcapi_timetable(r) for r in raw_tt]
        data = [
            {
                "route_code": c.get("route_code"),
                "route_name": c.get("route_name") or c.get("route_code") or "",
                "route_variant": c.get("route_variant") or "",
                "direction": c.get("direction") or "",
                "day_type": c.get("day_type") or "",
                "service_type": c.get("service_type") or "",
                "departure_time": c.get("departure_time"),
            }
            for c in canonical
            if c.get("route_code") and c.get("departure_time")
        ]
    except NtcApiError as exc:
        logger.warning("ntcapi schedule failed for %s, falling back to IETT SOAP: %s", hat_kodu, exc)

    # ── fallback: IETT SOAP ─────────────────────────────────────────
    if not data:
        client = IettClient(session)
        try:
            schedule = await client.get_route_schedule(hat_kodu)
        except IettApiError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        data = [
            normalizers.schedule.from_iett_soap_schedule(d.model_dump())
            for d in schedule
        ]
        # Strip internal _source key before caching
        data = [{k: v for k, v in d.items() if k != "_source"} for d in data]

    await cache_set(key, data, settings.cache_ttl_schedule)
    return data


@router.get("/{hat_kodu}/announcements", response_model=list[Announcement])
async def get_route_announcements(hat_kodu: str):
    """Active disruption announcements for a route."""
    key = f"routes:announcements:{hat_kodu}"
    cached = await cache_get(key)
    if cached is not None:
        return cached
    client = IettClient(get_session())
    try:
        announcements = await client.get_announcements(hat_kodu)
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    data = [a.model_dump() for a in announcements]
    await cache_set(key, data, settings.cache_ttl_announcements)
    return announcements
