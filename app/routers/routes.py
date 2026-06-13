"""Routes router — /v1/routes"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.deps import get_session
from app.models.bus import BusPosition
from app.models.route import (
    Announcement,
    RouteMetadata,
    RouteSearchResult,
    ScheduledDeparture,
)
from app.models.stop import RouteStop
from app.services.cache import SkipCache, cache_get_or_fetch, cache_set
from app.services.iett_client import IettApiError, IettClient
from app.services.mobiett_client import MobiettClient
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

    return await cache_get_or_fetch(
        key,
        settings.cache_ttl_search,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
        jitter=True,
    )


@router.get("/{hat_kodu}", response_model=list[RouteMetadata])
async def get_route_metadata(hat_kodu: str):
    hat_kodu = hat_kodu.upper().strip()
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
            logger.warning(
                "ntcapi metadata failed for %s, falling back to IETT SOAP: %s",
                hat_kodu,
                exc,
            )

        if not data:
            client = IettClient(session)
            try:
                meta = await client.get_route_metadata(hat_kodu)
            except IettApiError as exc:
                raise HTTPException(502, detail=str(exc)) from exc
            data = [m.model_dump() for m in meta]
        return data

    return await cache_get_or_fetch(
        key,
        settings.cache_ttl_search,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
        jitter=True,
    )


@router.get("/{hat_kodu}/buses", response_model=list[BusPosition])
async def get_route_buses(hat_kodu: str):
    hat_kodu = hat_kodu.upper().strip()
    """GPS positions of buses on a route.

    Primary: ntcapi ybs point-passing/{hat_id} — includes stop_sequence per bus.
    Secondary: IETT GetHatOtoKonum_json SOAP.
    Fallback: filters in-memory fleet store by route_code (stale-while-revalidate).
    """
    from app.deps import ensure_fleet_fresh, get_buses_by_route  # noqa: PLC0415

    session = get_session()

    key = f"routes:buses:{hat_kodu}"

    async def _fetch():
        # ── primary: ntcapi ybs point-passing ──────────────────────────────────
        try:
            # HAT_ID is returned by mainGetLine_basic and cached with route metadata.
            # Check the metadata cache first to avoid an extra round-trip.
            try:
                raw_meta = await get_route_metadata(hat_kodu)
                hat_id = next(
                    (m.get("hat_id") for m in (raw_meta or []) if m.get("hat_id")),
                    None,
                )
            except HTTPException as exc:
                logger.warning(
                    "Upstream metadata API failed (HTTP %s): %s",
                    exc.status_code,
                    exc.detail,
                )
                hat_id = None
            except Exception as exc:
                logger.warning("Failed to get route metadata for hat_id: %s", exc)
                hat_id = None
            if hat_id is not None:
                buses = await ntcapi_client.get_route_buses_ybs(
                    hat_id, hat_kodu, session
                )
                if buses:
                    from app.deps import update_fleet

                    update_fleet(buses, is_full_snapshot=False)
                    return buses
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "ybs point-passing failed for %s, falling back to SOAP: %s",
                hat_kodu,
                exc,
            )

        # ── secondary: IETT SOAP GetHatOtoKonum ────────────────────────────────
        try:
            client = IettClient(session)
            buses = await client.get_route_buses(hat_kodu)
            if buses:
                from app.deps import update_fleet

                update_fleet(buses, is_full_snapshot=False)
                return buses
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_route_buses SOAP failed for %s, falling back to fleet: %s",
                hat_kodu,
                exc,
            )

        # ── last resort: in-memory fleet ───────────────────────────────────────
        await ensure_fleet_fresh()
        return get_buses_by_route(hat_kodu)

    return await cache_get_or_fetch(
        key, 5, _fetch, stale_ttl=settings.cache_stale_ttl, jitter=True
    )


@router.get("/{hat_kodu}/stops", response_model=list[RouteStop])
async def get_route_stops(hat_kodu: str):
    hat_kodu = hat_kodu.upper().strip()
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
            logger.warning(
                "ntcapi stops failed for %s, falling back to IETT SOAP: %s",
                hat_kodu,
                exc,
            )

        has_null_coords = any(s.latitude is None or s.longitude is None for s in stops)
        if not stops or has_null_coords:
            if has_null_coords:
                logger.warning(
                    "ntcapi stops missing coords for %s, trying SOAP fallback", hat_kodu
                )
            client = IettClient(session)
            try:
                soap_stops = await client.get_route_stops(hat_kodu)
                if soap_stops:
                    stops = soap_stops
                    has_null_coords = any(
                        s.latitude is None or s.longitude is None for s in stops
                    )
            except IettApiError as exc:
                if not stops:
                    raise HTTPException(502, detail=str(exc)) from exc

        dumped = [s.model_dump() for s in stops]
        if has_null_coords:
            raise SkipCache(dumped)
        return dumped

    return await cache_get_or_fetch(
        key,
        settings.cache_ttl_stops,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
        jitter=True,
    )


@router.get("/{hat_kodu}/schedule", response_model=list[ScheduledDeparture])
async def get_route_schedule(hat_kodu: str):
    hat_kodu = hat_kodu.upper().strip()
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
            logger.warning(
                "ntcapi schedule failed for %s, falling back to IETT SOAP: %s",
                hat_kodu,
                exc,
            )

        if not data:
            client = IettClient(session)
            try:
                schedule = await client.get_route_schedule(hat_kodu)
            except IettApiError as exc:
                raise HTTPException(502, detail=str(exc)) from exc
            data = [  # type: ignore
                normalizers.schedule.from_iett_soap_schedule(d.model_dump())
                for d in schedule
            ]
            data = [{k: v for k, v in d.items() if k != "_source"} for d in data]
        return data

    return await cache_get_or_fetch(
        key,
        settings.cache_ttl_schedule,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
        jitter=True,
    )


def fix_encoding(text: str | None) -> str | None:
    if not text:
        return text
    try:
        # If it looks like double-encoded UTF-8, decode it
        if "Ã" in text or "Ä" in text or "Å" in text or "Â" in text:
            return text.encode("latin1").decode("utf-8")
    except Exception:
        pass
    return text


async def fetch_filtered_announcements(route_list: set[str]) -> list[dict]:
    route_list = {r.upper().strip() for r in route_list if r.strip()}
    if not route_list:
        return []

    key = "routes:announcements:global"

    async def _fetch():
        client = IettClient(get_session())
        try:
            announcements = await client.get_announcements()
        except IettApiError as exc:
            logger.warning(
                "IETT API failed for global announcements, applying 60s negative cache: %s",
                exc,
            )
            await cache_set(key, [], 60)
            raise SkipCache([])
        return [a.model_dump() for a in announcements]

    all_anns = await cache_get_or_fetch(
        key,
        settings.cache_ttl_announcements,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
        jitter=True,
    )

    combined = []
    for ann in all_anns:  # type: ignore
        rc = ann.get("route_code", "").upper().strip()
        if rc in route_list:
            a_dict = dict(ann)
            a_dict["title"] = fix_encoding(a_dict.get("title"))
            a_dict["message"] = fix_encoding(a_dict.get("message"))
            combined.append(a_dict)

    return combined


@router.get("/announcements/batch", response_model=list[Announcement])
async def get_batch_announcements(
    routes: str = Query(..., description="Comma-separated route codes"),
):
    """Get active disruption announcements for multiple routes at once."""
    route_list = {r.strip().upper() for r in routes.split(",") if r.strip()}
    return await fetch_filtered_announcements(route_list)


@router.get("/{hat_kodu}/announcements", response_model=list[Announcement])
async def get_route_announcements(hat_kodu: str):
    hat_kodu = hat_kodu.upper().strip()
    """Active disruption announcements for a route."""
    key = f"routes:announcements:{hat_kodu}"

    async def _fetch():
        route_list = {hat_kodu}

        # 1. Get global announcements for the route
        global_anns = await fetch_filtered_announcements(route_list)

        # 2. Get stops for the route to pull stop-specific announcements
        stops = []
        try:
            stops = await get_route_stops(hat_kodu)
        except Exception as exc:
            logger.warning(
                "Failed to get route stops for announcements enrichment: %s", exc
            )

        stops_to_check = []
        if stops:
            g_stops = [s for s in stops if s["direction"] == "G"]
            d_stops = [s for s in stops if s["direction"] == "D"]

            # Pick 2nd, 3rd, and 4th stop from each direction
            # Why [1:4]? We want a few stops near the beginning of the route
            # to catch route-wide traffic warnings, skipping the 1st (origin)
            # which might have different properties or be a terminal.
            for stop_list in (g_stops, d_stops):
                if len(stop_list) > 1:
                    stops_to_check.extend([s["stop_code"] for s in stop_list[1:4]])

        # Deduplicate stops
        stops_to_check = list(set(stops_to_check))

        # 3. Pull from mobiett ybs API concurrently
        stop_anns = []
        if stops_to_check:
            session = get_session()
            m_client = MobiettClient(session)

            async def _fetch_stop(dcode: str):
                try:
                    return await m_client.get_stop_announcements(dcode)
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch stop announcements for %s: %s", dcode, exc
                    )
                    return []

            results = await asyncio.gather(
                *[_fetch_stop(code) for code in stops_to_check]
            )
            for res_list in results:
                for item in res_list:
                    if item.get("HAT") == hat_kodu:
                        msg = fix_encoding(item.get("BILGI", ""))
                        if msg:
                            # Split combined messages by ' | ' if present
                            for sub_msg in msg.split(" | "):
                                sub_msg = sub_msg.strip()
                                if sub_msg:
                                    stop_anns.append(
                                        {
                                            "route_code": hat_kodu,
                                            "title": "Güzergah Duyurusu",
                                            "message": sub_msg,
                                        }
                                    )

        # 4. Merge and deduplicate
        seen_messages = set()
        final_anns = []

        for ann in global_anns + stop_anns:
            msg = ann.get("message", "").strip()
            # Fuzzy deduplication could be added here later
            if msg not in seen_messages:
                seen_messages.add(msg)
                final_anns.append(ann)

        return final_anns

    return await cache_get_or_fetch(
        key,
        settings.cache_ttl_announcements,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
        jitter=True,
    )
