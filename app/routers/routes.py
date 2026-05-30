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
from app.services.cache import cache_get, cache_set, cache_get_or_fetch
from app.services.iett_client import IettApiError, IettClient
from app.services import normalizers, ntcapi_client
from app.services.ntcapi_client import NtcApiError

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/search", response_model=list[RouteSearchResult])
async def search_routes(q: str = Query(..., min_length=1)):
    """Search routes by name or code (e.g. '14M', 'kadikoy')."""
    key = f"routes:search:{q.lower()}"
    
    async def _fetch():
        client = IettClient(get_session())
        try:
            results = await client.search_routes(q)
        except IettApiError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        return [r.model_dump() for r in results]
        
    return await cache_get_or_fetch(key, settings.cache_ttl_search, _fetch)


@router.get("/{hat_kodu}", response_model=list[RouteMetadata])
async def get_route_metadata(hat_kodu: str):
    """Route variant/direction metadata.

    ntcapi ``mainGetLine`` is the primary source; IETT SOAP ``GetAllRoute``
    is the fallback.
    """
    key = f"routes:meta:{hat_kodu}"
    
    async def _fetch():
        session = get_session()
        data: list[dict] = []
        try:
            raw_meta = await ntcapi_client.get_route_metadata(hat_kodu, session)
            data = raw_meta
        except NtcApiError as exc:
            logger.warning("ntcapi metadata failed for %s, falling back to IETT SOAP: %s", hat_kodu, exc)

        if not data:
            client = IettClient(session)
            try:
                meta = await client.get_route_metadata(hat_kodu)
            except IettApiError as exc:
                raise HTTPException(502, detail=str(exc)) from exc
            data = [m.model_dump() for m in meta]
        return data

    return await cache_get_or_fetch(key, settings.cache_ttl_search, _fetch)


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
        try:
            raw_meta = await get_route_metadata(hat_kodu)
            hat_id = next((m.get("hat_id") for m in raw_meta if m.get("hat_id")), None)
        except Exception as exc:
            logger.warning("Failed to get route metadata for hat_id: %s", exc)
            hat_id = None
        if hat_id is not None:
            buses = await ntcapi_client.get_route_buses_ybs(hat_id, hat_kodu, session)
            if buses:
                return buses
    except Exception as exc:  # noqa: BLE001
        logger.warning("ybs point-passing failed for %s, falling back to SOAP: %s", hat_kodu, exc)

    # ── secondary: IETT SOAP GetHatOtoKonum ────────────────────────────────
    try:
        client = IettClient(session)
        buses = await client.get_route_buses(hat_kodu)
        if buses:
            return buses
    except Exception as exc:  # noqa: BLE001
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
    
    async def _fetch():
        session = get_session()
        stops: list[RouteStop] = []
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

        has_null_coords = any(s.latitude is None or s.longitude is None for s in stops)
        if not stops or has_null_coords:
            if has_null_coords:
                logger.warning("ntcapi stops missing coords for %s, trying SOAP fallback", hat_kodu)
            client = IettClient(session)
            try:
                soap_stops = await client.get_route_stops(hat_kodu)
                if soap_stops:
                    stops = soap_stops
            except IettApiError as exc:
                if not stops:
                    raise HTTPException(502, detail=str(exc)) from exc

        return [s.model_dump() for s in stops]

    return await cache_get_or_fetch(key, settings.cache_ttl_stops, _fetch)


@router.get("/{hat_kodu}/schedule", response_model=list[ScheduledDeparture])
async def get_route_schedule(hat_kodu: str):
    """Planned departure times for a route (all day types).

    ntcapi ``akyolbilGetTimeTable`` is the primary source;
    IETT SOAP is the fallback.
    """
    key = f"routes:schedule:{hat_kodu}"
    
    async def _fetch():
        session = get_session()
        data: list[dict] = []
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
            data = [{k: v for k, v in d.items() if k != "_source"} for d in data]
        return data

    return await cache_get_or_fetch(key, settings.cache_ttl_schedule, _fetch)


@router.get("/{hat_kodu}/announcements", response_model=list[Announcement])
async def get_route_announcements(hat_kodu: str):
    """Active disruption announcements for a route."""
    key = f"routes:announcements:{hat_kodu}"
    
    async def _fetch():
        client = IettClient(get_session())
        try:
            announcements = await client.get_announcements(hat_kodu)
        except IettApiError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        return [a.model_dump() for a in announcements]
        
    return await cache_get_or_fetch(key, settings.cache_ttl_announcements, _fetch)
