"""Client for the ntcapi.iett.istanbul private API.

All service calls go to POST /service with an {"alias": ..., "data": ...}
body, authenticated with a Bearer token obtained via OAuth2 client_credentials.

Token is cached in-process and refreshed automatically when within 60 s of
expiry.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp

from app.config import settings

logger = logging.getLogger(__name__)

_NTC_BASE = "https://ntcapi.iett.istanbul"
_TOKEN_URL = f"{_NTC_BASE}/oauth2/v2/auth"
_SERVICE_URL = f"{_NTC_BASE}/service"

# ── in-process token cache ─────────────────────────────────────────────────
_token: str | None = None
_token_expiry: float = 0.0          # unix timestamp
_token_lock = asyncio.Lock()


async def _ensure_token(session: aiohttp.ClientSession) -> str:
    """Return a valid Bearer token, refreshing if needed."""
    global _token, _token_expiry
    # Fast path: token valid for at least another 60 s
    if _token and time.time() < _token_expiry - 60:
        return _token

    async with _token_lock:
        # Re-check inside the lock (another coroutine may have refreshed)
        if _token and time.time() < _token_expiry - 60:
            return _token

        payload = {
            "client_id": settings.ntcapi_client_id,
            "client_secret": settings.ntcapi_client_secret,
            "grant_type": "client_credentials",
            "scope": settings.ntcapi_scope,
        }
        async with session.post(
            _TOKEN_URL,
            json=payload,
            headers={"User-Agent": "okhttp/5.0.0-alpha.11"},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise NtcApiError(f"Token fetch failed {resp.status}: {text[:200]}")
            data = await resp.json()

        _token = data["access_token"]
        # expire_date is epoch-ms; also honour expires_in as a fallback
        if "expire_date" in data:
            _token_expiry = data["expire_date"] / 1000.0
        else:
            _token_expiry = time.time() + data.get("expires_in", 3600)
        logger.debug("ntcapi: new token obtained, expires at %s", _token_expiry)
        return _token  # type: ignore[return-value]


async def _call_service(
    session: aiohttp.ClientSession,
    alias: str,
    data: dict[str, Any],
) -> Any:
    """POST /service with the given alias and data, returning parsed JSON."""
    token = await _ensure_token(session)
    body = {"alias": alias, "data": data}
    async with session.post(
        _SERVICE_URL,
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "okhttp/5.0.0-alpha.11",
        },
    ) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise NtcApiError(f"Service call '{alias}' failed {resp.status}: {text[:200]}")
        return await resp.json()


# ── public helpers ─────────────────────────────────────────────────────────

class NtcApiError(Exception):
    """Raised when an ntcapi call fails."""


async def get_stop_arrivals(
    dcode: str,
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    """Fetch real-time arrivals at stop *dcode* via the ybs alias.

    Returns raw list of dicts with ybs field names (hatkodu, dakika, saat,
    kapino, son_konum, son_hiz, usb, wifi, klima, engelli …).
    Callers should pass each item through ``normalizers.arrivals.from_ntcapi_ybs``.
    """
    payload = {
        "data": {
            "password": settings.ntcapi_ybs_password,
            "username": settings.ntcapi_ybs_username,
        },
        "method": "POST",
        "path": ["real-time-information", "stop-arrivals", dcode],
    }
    raw: list[dict] = await _call_service(session, "ybs", payload)
    valid: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not (item.get("hatkodu") or item.get("saat")):
            logger.warning("ntcapi: skipping arrival item with no route/time: %r", item)
            continue
        valid.append(item)
    return valid


async def get_bus_location(
    kapino: str,
    session: aiohttp.ClientSession,
) -> dict[str, Any] | None:
    """Fetch live position + plate for a single bus by kapino.

    Returns a dict with keys: kapino, plate, lat, lon, speed, ts
    or None if the bus is not found.
    """
    payload = {"AKYOLBILYENI.K_ARAC.KAPINUMARASI": kapino}
    raw: list[dict] = await _call_service(session, "mainGetBusLocation_basic", payload)
    if not raw:
        return None
    item = raw[0]
    return {
        "kapino": item.get("K_ARAC_KAPINUMARASI"),
        "plate": item.get("K_ARAC_PLAKA"),
        "lat": item.get("H_OTOBUSKONUM_ENLEM"),
        "lon": item.get("H_OTOBUSKONUM_BOYLAM"),
        "speed": item.get("H_OTOBUSKONUM_HIZ"),
        "ts": item.get("H_OTOBUSKONUM_KAYITZAMANI"),
    }


async def get_route_stops(
    hat_kodu: str,
    direction: str,
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    """Ordered stop list for a route via mainGetRoute.

    `direction` maps to GUZERGAH_YON: pass the numeric value as a string
    (e.g. "119" for outbound, "120" for return) OR a letter ("G"/"D") which
    we translate to the canonical defaults (119/120 respectively).

    Returns list of dicts with keys:
      route_code, stop_code, stop_name, sequence, lat, lon, district, direction_letter
    """
    yon_map = {"G": "119", "D": "120"}
    yon = yon_map.get(direction.upper(), direction)
    payload = {
        "HATYONETIM.GUZERGAH.YON": yon,
        "HATYONETIM.HAT.HAT_KODU": hat_kodu,
    }
    raw: list[dict] = await _call_service(session, "mainGetRoute", payload)

    # Group all stops by their route variant code (e.g. "14M_G_D0", "14M_G_D1991", …).
    # ntcapi returns every service-pattern variant for this direction; we want only the
    # canonical one.  Selection priority:
    #   1. Variant whose code ends with "_D0"  (base/canonical service pattern)
    #   2. Variant with the most stops         (covers edge cases where _D0 is missing)
    from collections import defaultdict  # noqa: PLC0415
    variants: dict[str, list[dict]] = defaultdict(list)
    seen_keys: set[str] = set()
    for item in raw:
        rc = item.get("GUZERGAH_GUZERGAH_KODU") or f"{hat_kodu}_{direction}_D0"
        dcode = str(item.get("DURAK_DURAK_KODU") or "")
        key = f"{rc}:{dcode}:{item.get('GUZERGAH_SEGMENT_SIRA')}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        geoloc = item.get("DURAK_GEOLOC") or {}
        variants[rc].append({
            "route_code": rc,
            "stop_code": dcode,
            "stop_name": item.get("DURAK_ADI") or "",
            "sequence": item.get("GUZERGAH_SEGMENT_SIRA") or 0,
            "lat": geoloc.get("y"),
            "lon": geoloc.get("x"),
            "district": item.get("ILCELER_ILCEADI"),
            "direction_letter": direction.upper(),
        })

    if not variants:
        return []

    # Pick canonical variant: prefer _D0, else the one with the most stops
    canonical_key = next((k for k in variants if k.endswith("_D0")), None)
    if canonical_key is None:
        canonical_key = max(variants, key=lambda k: len(variants[k]))

    stops = sorted(variants[canonical_key], key=lambda s: s["sequence"])
    return stops


async def get_route_metadata(
    hat_kodu: str,
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    """Route variants list via mainGetLine_basic.

    Uses mainGetLine_basic (vs the older mainGetLine) so we also get HAT_ID,
    the numeric internal route identifier required for ybs point-passing calls.
    Returns list of dicts matching RouteMetadata shape.
    """
    payload = {
        "HATYONETIM.GUZERGAH.YON": "119",
        "HATYONETIM.HAT.HAT_KODU": hat_kodu,
    }
    raw: list[dict] = await _call_service(session, "mainGetLine_basic", payload)
    results = []
    seen: set[str] = set()
    for item in raw:
        code = item.get("GUZERGAH_GUZERGAH_KODU") or ""
        if code in seen:
            continue
        seen.add(code)
        yon = item.get("GUZERGAH_YON", 119)
        direction_letter = "D" if yon == 120 else "G"
        direction_name = (item.get("GUZERGAH_GUZERGAH_ADI") or "").strip()
        results.append({
            "direction_name": direction_name,
            "full_name": " ".join(filter(None, [
                str(item.get("GUZERGAH_DEPAR_NO") or ""),
                direction_name,
                "Gidiş" if direction_letter == "G" else "Dönüş",
            ])).strip(),
            "variant_code": code,
            "direction": 0 if direction_letter == "G" else 1,
            "depar_no": item.get("GUZERGAH_DEPAR_NO") or 0,
            "hat_id": item.get("HAT_ID"),
        })
    return results


async def get_route_buses_ybs(
    hat_id: int | str,
    hat_kodu: str,
    session: aiohttp.ClientSession,
) -> list[BusPosition]:
    """Live bus positions for a route via ybs point-passing/{hat_id}.

    Uses the same ybs alias as stop arrivals but with the 'point-passing'
    path and the ntcapi internal HAT_ID (not the public hat_kodu string).
    Returns a list of BusPosition objects.
    """
    from app.models.bus import BusPosition  # noqa: PLC0415 — avoid circular at module level

    payload = {
        "data": {
            "password": settings.ntcapi_ybs_password,
            "username": settings.ntcapi_ybs_username,
        },
        "method": "POST",
        "path": ["real-time-information", "point-passing", str(hat_id)],
    }
    raw: list[dict] = await _call_service(session, "ybs", payload)
    positions: list[BusPosition] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            lat = float(item["ENLEM"])
            lon = float(item["BOYLAM"])
        except (KeyError, TypeError, ValueError):
            continue
        guzergah = item.get("K_GUZERGAH_GUZERGAHKODU") or ""
        direction_letter: str | None = None
        for p in guzergah.split("_"):
            if p in ("G", "D"):
                direction_letter = p
                break
        seq = item.get("H_GOREV_DURAK_GECIS_SIRANO")
        try:
            stop_seq: int | None = int(seq) if seq is not None and str(seq).strip() else None
        except (ValueError, TypeError):
            stop_seq = None
        positions.append(BusPosition(
            kapino=item.get("K_ARAC_KAPINUMARASI") or "",
            latitude=lat,
            longitude=lon,
            last_seen=item.get("SISTEMSAATI") or "",
            route_code=hat_kodu or None,
            direction_letter=direction_letter,
            stop_sequence=stop_seq,
        ))
    return positions


async def get_timetable(
    hat_kodu: str,
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    """Scheduled departures via akyolbilGetTimeTable.

    Returns raw list — callers parse K_ORER_* fields.
    """
    payload = {"HATYONETIM.GUZERGAH.HAT_KODU": hat_kodu}
    return await _call_service(session, "akyolbilGetTimeTable", payload)


async def get_nearby_stops(
    lat: float,
    lon: float,
    radius_km: float,
    session: aiohttp.ClientSession,
) -> list[dict[str, Any]]:
    """Stops within radius_km of (lat, lon) via mainGetBusStopNearby.

    Returns list of dicts with keys: stop_code, stop_name, lat, lon, direction.
    """
    payload = {
        "HATYONETIM.DURAK.GEOLOC": {
            "fromSRID": "7932",
            "lat": str(lat),
            "long": str(lon),
            "r": str(radius_km),
        }
    }
    raw: list[dict] = await _call_service(session, "mainGetBusStopNearby", payload)
    stops = []
    for item in raw:
        geoloc = item.get("DURAK_GEOLOC") or {}
        stops.append({
            "stop_code": str(item.get("DURAK_DURAK_KODU") or ""),
            "stop_name": item.get("DURAK_ADI") or "",
            "lat": geoloc.get("y"),
            "lon": geoloc.get("x"),
            "direction": item.get("DURAK_YON_BILGISI"),
        })
    return stops


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_son_konum(value: Any) -> tuple[float | None, float | None]:
    """Parse 'lon,lat' string from son_konum field into (lat, lon)."""
    if not value:
        return None, None
    try:
        parts = str(value).split(",")
        lon = float(parts[0])
        lat = float(parts[1])
        return lat, lon
    except (IndexError, ValueError):
        return None, None
