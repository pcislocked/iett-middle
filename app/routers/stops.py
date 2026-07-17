"""Stops router â€” /v1/stops"""

from __future__ import annotations

import asyncio
import logging
import math as _math

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.deps import get_plate_by_kapino, get_session
from app.models.bus import Arrival
from app.models.route import Announcement
from app.models.stop import NearbyStop, StopDetail, StopSearchResult
from app.services import normalizers, ntcapi_client
from app.services.cache import cache_get_or_fetch
from app.services.iett_client import IettApiError, IettClient
from app.services.ntcapi_client import NtcApiError

logger = logging.getLogger(__name__)

router = APIRouter()


def _haversine_m(
    user_lat: float, user_lon: float, stop_lat: float, stop_lon: float
) -> float:
    """Haversine distance in metres."""
    R = 6_371_000.0
    p1, p2 = _math.radians(user_lat), _math.radians(stop_lat)
    dp = p2 - p1
    dl = _math.radians(stop_lon - user_lon)
    a = _math.sin(dp / 2) ** 2 + _math.cos(p1) * _math.cos(p2) * _math.sin(dl / 2) ** 2
    return R * 2 * _math.atan2(_math.sqrt(a), _math.sqrt(max(0.0, 1 - min(1.0, a))))


@router.get("/search", response_model=list[StopSearchResult])
async def search_stops(q: str = Query(..., min_length=2)):
    """Search stops by name."""
    key = f"stops:search:{q.lower()}"

    async def _fetch():
        client = IettClient(get_session())
        try:
            results = await client.search_stops(q)
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


@router.get("/nearby", response_model=list[NearbyStop])
async def nearby_stops(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius: float = Query(default=500, ge=50, le=3000),
    limit: int = Query(default=15, ge=5, le=50),
):
    """Stops within *radius* metres of (lat, lon), sorted by distance.

    ntcapi ``mainGetBusStopNearby`` is the primary source.  Falls back to
    the in-memory spatial index populated at startup.
    Returns up to *limit* results.
    """
    session = get_session()

    # â”€â”€ primary: ntcapi mainGetBusStopNearby â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    try:
        raw_stops = await ntcapi_client.get_nearby_stops(
            lat, lon, radius / 1000, session
        )
        canonical = [
            normalizers.stops.from_ntcapi_nearby_processed(r) for r in raw_stops
        ]
        nearby_results: list[NearbyStop] = []
        for c in canonical:
            try:
                latitude = float(c["lat"])  # type: ignore[arg-type]
                longitude = float(c["lon"])  # type: ignore[arg-type]
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "Skipping nearby stop with invalid coordinates: %s",
                    c.get("stop_code"),
                )
                continue

            stop_code = str(c.get("stop_code") or "").strip()
            # Ignore invalid stops (e.g. -1523 or non-numeric station codes from Marmaray/Metro)
            if not stop_code.isdigit() or len(stop_code) < 4:
                continue

            nearby_results.append(
                NearbyStop(
                    stop_code=stop_code,
                    stop_name=c.get("stop_name") or "",
                    latitude=latitude,
                    longitude=longitude,
                    district=c.get("district"),
                    direction=c.get("direction"),
                    distance_m=c.get("distance_m")  # type: ignore
                    if c.get("distance_m") is not None
                    else _haversine_m(lat, lon, latitude, longitude),
                )
            )

            if len(nearby_results) >= limit:
                break

        return nearby_results
    except NtcApiError as exc:
        logger.warning(
            "ntcapi nearby stops failed (lat=%s lon=%s), falling back to index: %s",
            lat,
            lon,
            exc,
        )

    # â”€â”€ fallback: in-memory spatial index â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    from app.deps import get_nearby_stops as _get_nearby  # noqa: PLC0415
    from app.deps import get_stop_index_updated_at

    if get_stop_index_updated_at() is None:
        raise HTTPException(
            503, detail="Stop index not ready yet â€” try again in a moment"
        )
    results = _get_nearby(lat, lon, radius)
    return results[:limit]


@router.get("/{dcode}/arrivals/raw")
async def get_arrivals_raw(dcode: str):
    dcode = dcode.strip()
    """Return the raw HTML from IETT GetStationInfo â€” debug only."""
    client = IettClient(get_session())
    try:
        html = await client._get_text(
            f"{settings.iett_rest_base}/tr/RouteStation/GetStationInfo",
            params={"dcode": dcode, "langid": "1"},
        )
    except IettApiError as exc:
        raise HTTPException(502, detail=str(exc)) from exc
    from fastapi.responses import PlainTextResponse  # noqa: PLC0415

    return PlainTextResponse(content=html)


@router.get("/{dcode}/arrivals", response_model=list[Arrival])
async def get_arrivals(dcode: str, via: str | None = Query(default=None)):
    dcode = dcode.strip()
    """Live ETAs at a stop, sourced from ntcapi ybs (has kapino + live location).

    Falls back to the legacy IETT HTML endpoint if ntcapi is unavailable.
    All sources are normalised to :class:`~app.models.bus.Arrival` via the
    canonical data layer.
    """
    key = f"stops:arrivals:{dcode}" + (f":via:{via}" if via else "")

    async def _fetch():
        session = get_session()
        arrivals_data = []

        # â”€â”€ primary: ntcapi ybs (has kapino + live bus location) â”€â”€â”€â”€â”€â”€
        try:
            raw_items = await ntcapi_client.get_stop_arrivals(dcode, session)
            canonical = [normalizers.arrivals.from_ntcapi_ybs(r) for r in raw_items]

            # Enrich empty destinations via route search fallback
            # Resolve missing destinations per-route to avoid cache stampede
            missing_routes = list(
                {
                    a["route_code"]  # type: ignore
                    for a in canonical
                    if not a.get("destination") and a.get("route_code")
                }
            )
            route_names: dict[str, str] = {}
            client = IettClient(session)

            async def _resolve_route(rc: str):
                async def _fetch_name() -> str:
                    res = await client.search_routes(rc)
                    for r in res:
                        if r.hat_kodu.upper() == rc.upper():
                            return r.name
                    return ""

                try:
                    name = await cache_get_or_fetch(
                        f"routes:name:{rc}",
                        86400,
                        _fetch_name,
                        stale_ttl=settings.cache_stale_ttl,
                        jitter=True,
                    )
                    route_names[rc] = name  # type: ignore
                except Exception as e:
                    logger.warning("Failed to fetch route name for %s: %s", rc, e)

            if missing_routes:
                await asyncio.gather(*[_resolve_route(rc) for rc in missing_routes])
                for a in canonical:
                    if not a.get("destination") and a.get("route_code") in route_names:
                        a["destination"] = route_names[a["route_code"]]  # type: ignore

            canonical.sort(
                key=lambda a: (  # type: ignore
                    a.get("eta_minutes") if a.get("eta_minutes") is not None else 9999
                )
            )
            arrivals_data = list(canonical)
        except NtcApiError as exc:
            logger.warning(
                "ntcapi arrivals failed for %s, falling back to HTML: %s", dcode, exc
            )

        # â”€â”€ fallback: legacy IETT HTML (no kapino, no location) â”€â”€â”€â”€â”€â”€â”€
        if not arrivals_data:
            client = IettClient(session)
            try:
                if via:
                    iett_arrivals = await client.get_stop_arrivals_via(dcode, via)
                else:
                    iett_arrivals = await client.get_stop_arrivals(dcode)
            except IettApiError as exc:
                raise HTTPException(502, detail=str(exc)) from exc
            arrivals_data = [
                normalizers.arrivals.from_iett_html(a.model_dump())
                for a in iett_arrivals
            ]

        # â”€â”€ via filter (applied after ntcapi fetch if needed) â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if via and arrivals_data:
            try:
                routes_via = await get_routes_at_stop(via)
                routes_via_upper = {r.upper() for r in routes_via}  # type: ignore
                arrivals_data = [
                    a
                    for a in arrivals_data
                    if a.get("route_code", "").upper() in routes_via_upper
                ]
            except Exception as exc:
                logger.warning(
                    "via-filter lookup failed for stop %s via %s â€” returning unfiltered arrivals: %s",
                    dcode,
                    via,
                    exc,
                )

        return arrivals_data

    arrivals_data = await cache_get_or_fetch(
        key,
        settings.cache_ttl_arrivals,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
        jitter=True,
    )

    # Enrich with plate from in-memory fleet store (free, O(1) by kapino).
    result = []
    for a in arrivals_data:  # type: ignore
        kapino = a.get("kapino")
        plate = a.get("plate") or (get_plate_by_kapino(kapino) if kapino else None)
        result.append(
            Arrival(
                **{k: v for k, v in a.items() if k not in ("_source", "plate")},
                plate=plate,
            )
        )
    return result


@router.get("/{dcode}/routes", response_model=list[str])
async def get_routes_at_stop(dcode: str):
    dcode = dcode.strip()
    """All route codes that pass through a stop."""
    key = f"stops:routes:{dcode}"

    async def _fetch():
        client = IettClient(get_session())
        try:
            route_codes = await client.get_routes_at_stop(dcode)
        except IettApiError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        return sorted(route_codes)

    return await cache_get_or_fetch(
        key,
        settings.cache_ttl_search,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
        jitter=True,
    )


@router.get("/{dcode}", response_model=StopDetail)
async def get_stop_detail(dcode: str):
    dcode = dcode.strip()
    """Stop name and coordinates (from search + route stop lookup). Long-cached."""
    key = f"stops:detail:{dcode}"

    async def _fetch():
        client = IettClient(get_session())
        try:
            detail = await client.get_stop_detail(dcode)
        except IettApiError as exc:
            raise HTTPException(502, detail=str(exc)) from exc
        if detail is None:
            raise HTTPException(404, detail=f"Stop {dcode!r} not found")
        return detail.model_dump()

    detail_data = await cache_get_or_fetch(
        key,
        settings.cache_ttl_stops,
        _fetch,
        stale_ttl=settings.cache_stale_ttl,
        jitter=True,
    )
    return StopDetail(**detail_data)  # type: ignore


@router.get("/{dcode}/announcements", response_model=list[Announcement])
async def get_stop_announcements(dcode: str):
    dcode = dcode.strip()
    """Live traffic and route announcements for a specific stop.
    
    This endpoint gives you all alerts that might affect a passenger waiting at this stop.

    **Merged Sources:**
      1) **Route Disruptions:** Global route disruptions for **all routes** passing through this stop.
      2) **Stop Traffic:** Stop-specific real-time traffic/congestion notices (e.g., "TRAFİK YOĞUNLUĞU").
    """
    key = f"stops:announcements:{dcode}"

    async def _fetch():
        from app.routers.routes import fetch_filtered_announcements
        from app.services.mobiett_client import MobiettClient

        session = get_session()

        # 1. Fetch all routes passing through this stop
        try:
            route_codes = await get_routes_at_stop(dcode)
        except HTTPException as exc:
            logger.warning(f"Failed to fetch routes for stop {dcode}: {exc.detail}")
            route_codes = []
        except Exception as exc:
            logger.warning(f"Unexpected error fetching routes for stop {dcode}: {exc}")
            route_codes = []

        # 2. Fetch global route announcements concurrently with stop-specific announcements
        mobiett_client = MobiettClient(session)
        try:
            global_task = fetch_filtered_announcements(set(route_codes))  # type: ignore
            stop_status_task = mobiett_client.get_stop_announcements(dcode)

            global_anns, stop_anns = await asyncio.gather(
                global_task, stop_status_task, return_exceptions=True
            )

            if isinstance(global_anns, Exception):
                logger.warning(
                    f"Global announcements failed for stop {dcode}: {global_anns}"
                )
                global_anns = []
            elif not global_anns:
                global_anns = []

            if isinstance(stop_anns, Exception):
                logger.warning(
                    f"Stop announcements failed for stop {dcode}: {stop_anns}"
                )
                stop_anns = []
            elif not stop_anns:
                stop_anns = []

        except Exception as e:
            logger.error(f"Error fetching combined announcements for stop {dcode}: {e}")
            global_anns, stop_anns = [], []

        # 3. Merge and deduplicate
        combined: list[dict] = []
        seen = set()

        # Add global announcements
        for ann in global_anns:  # type: ignore
            msg = (ann.get("message") or "").strip()
            route_code = (ann.get("route_code") or "").strip().upper()
            key = (route_code, msg)
            if msg and key not in seen:
                ann_copy = dict(ann)
                ann_copy["route_code"] = route_code
                combined.append(ann_copy)
                seen.add(key)

        # Add stop-specific announcements
        for ann in stop_anns:  # type: ignore
            if not isinstance(ann, dict):
                continue
            msg = (ann.get("BILGI") or "").strip()
            route_code = (ann.get("HAT") or "").strip().upper()
            key = (route_code, msg)
            if msg and key not in seen:
                combined.append(
                    {
                        "route_code": route_code,
                        "route_name": "",
                        "type": "Trafik",
                        "updated_at": "",
                        "message": msg,
                    }
                )
                seen.add(key)

        return combined

    announcements_data = await cache_get_or_fetch(
        key, 300, _fetch, stale_ttl=settings.cache_stale_ttl, jitter=True
    )
    return [Announcement(**a) for a in announcements_data]  # type: ignore
