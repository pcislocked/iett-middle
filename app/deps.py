"""Shared FastAPI dependencies and in-memory stores.

Import from here, never from main.py, to avoid circular imports.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import deque
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from app.models.bus import BusPosition
from fastapi import Request
from slowapi import Limiter

from app.models.stop import NearbyStop
from app.services.normalizers.plate import normalize_plate

logger = logging.getLogger(__name__)


def get_real_ip(request: Request) -> str:
    """Extract real client IP from headers.

    WARNING: This assumes the app is fronted by a trusted proxy (like Cloudflare or Nginx)
    that sanitizes and overrides CF-Connecting-IP/X-Forwarded-For headers. If exposed
    directly to the public internet, this is vulnerable to client-side IP spoofing.
    """
    if cf_ip := request.headers.get("CF-Connecting-IP"):
        return cf_ip.split(",")[0].strip()
    if xfwd := request.headers.get("X-Forwarded-For"):
        return xfwd.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


limiter = Limiter(key_func=get_real_ip, default_limits=["120/minute"])

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    if _session is None:
        raise RuntimeError("HTTP session not initialised")
    return _session


def set_session(s: aiohttp.ClientSession) -> None:
    global _session  # noqa: PLW0603
    _session = s


async def close_session() -> None:
    global _session  # noqa: PLW0603
    if _session is not None:
        await _session.close()
        _session = None


# ---------------------------------------------------------------------------
# Fleet in-memory store
# ---------------------------------------------------------------------------
# kapino → latest BusPosition (as dict for JSON-serialisability)
_fleet: dict[str, dict[str, Any]] = {}
# Normalized clean kapino / plate -> latest BusPosition (O(1) dash/space agnostic lookup)
_clean_fleet: dict[str, dict[str, Any]] = {}
_plate_fleet: dict[str, dict[str, Any]] = {}
# kapino → deque of TrailPoint dicts  {lat, lon, ts}
_trail: dict[str, deque[dict[str, Any]]] = {}
_fleet_updated_at: datetime | None = None
_fleet_updated_at_mono: float | None = None
_fleet_refresh_task: asyncio.Task | None = None
# kapino (upper) → last known route_code (survives when bus parks / goes offline)
_kapino_last_route: dict[str, str] = {}
_kapino_updated_at: dict[str, float] = {}


_known_plate_mappings: dict[str, str] = {}


def clear_fleet_store() -> None:
    """Clear all in-memory fleet state (used for test teardown)."""
    _fleet.clear()
    _clean_fleet.clear()
    _plate_fleet.clear()
    _trail.clear()
    _kapino_last_route.clear()
    _kapino_updated_at.clear()
    _known_plate_mappings.clear()


def get_fleet_snapshot() -> list[dict[str, Any]]:
    """Return current fleet as a list of dicts (thread-safe read)."""
    return list(_fleet.values())


def find_bus_in_fleet(
    query: str,
    snapshot: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Find bus in active fleet by door number or plate in O(1) time.

    Returns (bus_dict, canonical_kapino) or (None, None).
    """
    clean_q = re.sub(r"[^A-Z0-9]", "", query.upper())
    if not clean_q:
        return None, None

    # If caller provided an explicit snapshot (e.g. test mocks), scan it
    if snapshot is not None:
        for b in snapshot:
            k = str(b.get("kapino") or "")
            pl = str(b.get("plate") or "")
            if re.sub(r"[^A-Z0-9]", "", k.upper()) == clean_q or (
                pl and re.sub(r"[^A-Z0-9]", "", pl.upper()) == clean_q
            ):
                return b, b.get("kapino")

        # Check persistent cache for alternate plate or door code mapping
        try:
            from app.services import vehicle_cache

            veh = vehicle_cache.get_vehicle(clean_q)
            if veh:
                canonical_clean = veh.get("clean_kapino")
                if canonical_clean:
                    for b in snapshot:
                        k = str(b.get("kapino") or "")
                        if re.sub(r"[^A-Z0-9]", "", k.upper()) == canonical_clean:
                            return b, b.get("kapino")
        except Exception:
            pass

        return None, None

    # O(1) fast path: check clean kapino and plate stores
    bus = _clean_fleet.get(clean_q) or _plate_fleet.get(clean_q)
    if bus:
        return bus, bus.get("kapino")

    # Check persistent cache for alternate plate or door code mapping
    try:
        from app.services import vehicle_cache

        veh = vehicle_cache.get_vehicle(clean_q)
        if veh:
            canonical_clean = veh.get("clean_kapino")
            if canonical_clean:
                bus = _clean_fleet.get(canonical_clean)
                if bus:
                    return bus, bus.get("kapino")
    except Exception:
        pass

    return None, None


def get_trail(kapino: str) -> list[dict[str, Any]]:
    return list(_trail.get(kapino, deque()))


def get_fleet_updated_at() -> datetime | None:
    return _fleet_updated_at


def get_buses_by_route(hat_kodu: str) -> list[dict[str, Any]]:
    """Return all fleet buses whose route_code matches hat_kodu (case-insensitive, prefix-tolerant)."""
    upper = hat_kodu.upper()
    result = []
    for b in get_fleet_snapshot():
        rc = (b.get("route_code") or "").upper().strip()
        if not rc:
            continue
        # exact match OR variant suffixes like "14M_D", "14M_G", "14M D"
        if rc == upper or rc.startswith(upper + "_") or rc.startswith(upper + " "):
            result.append(b)
    return result


def get_buses_near_stop(dcode: str) -> list[dict[str, Any]]:
    """Return all fleet buses currently with nearest_stop == dcode."""
    return [b for b in get_fleet_snapshot() if b.get("nearest_stop") == dcode]


async def ensure_fleet_fresh(max_age_seconds: int = 30) -> None:
    """Trigger a background fleet refresh if data is absent or older than max_age_seconds.

    Returns immediately — caller gets the current (possibly stale) snapshot;
    the next request after the refresh completes will see fresh data.
    Concurrent calls are deduplicated: at most one refresh task runs at a time.
    """
    global _fleet_refresh_task  # noqa: PLW0603

    if _fleet_updated_at_mono is not None:
        age = time.monotonic() - _fleet_updated_at_mono
        if age < max_age_seconds:
            return  # fresh enough

    # Debounce: don't start a second refresh while one is already in flight
    if _fleet_refresh_task is not None and not _fleet_refresh_task.done():
        return

    from app.services.fleet_poller import refresh_fleet_once  # noqa: PLC0415

    _fleet_refresh_task = asyncio.create_task(refresh_fleet_once())


async def cancel_fleet_refresh_task() -> None:
    """Cancel and await the in-flight refresh task, if one exists."""
    global _fleet_refresh_task  # noqa: PLW0603

    task = _fleet_refresh_task
    if task is None:
        return
    if task.done():
        _fleet_refresh_task = None
        return

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        _fleet_refresh_task = None


def get_plate_by_kapino(kapino: str) -> str | None:
    """Look up the plate for a given kapino from in-memory fleet store or persistent cache."""
    if not kapino:
        return None
    p = _fleet.get(kapino, {}).get("plate")
    if p:
        return p

    clean_k = re.sub(r"[^A-Z0-9]", "", kapino.upper())
    if clean_k:
        bus = _clean_fleet.get(clean_k)
        if bus and bus.get("plate"):
            return bus["plate"]

        from app.services import vehicle_cache  # noqa: PLC0415

        veh = vehicle_cache.get_vehicle(clean_k)
        if veh and veh.get("plate"):
            return veh["plate"]

    return None


def record_plate_mapping(kapino: str, plate: str) -> None:
    """Record a known kapino <-> plate pair into in-memory fleet and persistent cache."""
    if not kapino or not plate:
        return
    norm_p = normalize_plate(plate) or plate.strip()
    clean_k = re.sub(r"[^A-Z0-9]", "", kapino.upper())
    clean_p = re.sub(r"[^A-Z0-9]", "", norm_p.upper())
    if not clean_k or not clean_p:
        return

    _known_plate_mappings[clean_k] = clean_p

    if clean_k in _clean_fleet:
        _clean_fleet[clean_k]["plate"] = norm_p
    else:
        _clean_fleet[clean_k] = {"kapino": kapino, "plate": norm_p}

    if kapino in _fleet:
        _fleet[kapino]["plate"] = norm_p
    else:
        _fleet[kapino] = {"kapino": kapino, "plate": norm_p}

    _plate_fleet[clean_p] = _clean_fleet[clean_k]

    try:
        from app.services import vehicle_cache  # noqa: PLC0415

        vehicle_cache.upsert_vehicle(door_number=kapino, plate=norm_p)
    except Exception as exc:
        logger.debug("Failed to persist plate mapping to sqlite: %s", exc)


def get_last_route_by_kapino(kapino: str) -> str | None:
    """Return the last known route_code for a kapino (set whenever fleet poll sees a non-null route_code)."""
    return _kapino_last_route.get(kapino.upper())


def update_fleet(buses: list[BusPosition], is_full_snapshot: bool = True) -> None:  # noqa: C901
    """Called by the background poller.  Updates fleet dict and trail deques."""
    global _fleet_updated_at, _fleet_updated_at_mono  # noqa: PLW0603
    from app.config import settings  # noqa: PLC0415

    # Max trail entries = (trail_minutes * 60) / poll_interval, rounded up
    max_entries = max(
        2,
        int(settings.fleet_trail_minutes * 60 / settings.fleet_poll_interval) + 1,
    )

    for b in buses:
        k = b.kapino
        prev = _fleet.get(k)
        # Append previous position to trail when bus actually moved
        if prev is not None and (
            prev["latitude"] != b.latitude or prev["longitude"] != b.longitude
        ):
            if k not in _trail:
                _trail[k] = deque(maxlen=max_entries)
            _trail[k].append(
                {
                    "lat": prev["latitude"],
                    "lon": prev["longitude"],
                    "ts": prev["last_seen"],
                }
            )
        elif k not in _trail:
            _trail[k] = deque(maxlen=max_entries)
        _fleet[k] = b.model_dump()
        # Persist last known route even when bus later goes offline / parks
        if b.route_code:
            _kapino_last_route[k.upper()] = b.route_code.strip().upper()

        _kapino_updated_at[k] = time.monotonic()

    # Evict buses not seen in 24 hours (86400 seconds)
    now = time.monotonic()
    expired = [k for k, ts in _kapino_updated_at.items() if now - ts > 86400]
    for k in expired:
        _kapino_updated_at.pop(k, None)
        _fleet.pop(k, None)
        _trail.pop(k, None)
        _kapino_last_route.pop(k.upper(), None)

    if is_full_snapshot:
        current_kapinos = {b.kapino for b in buses}
        for k in list(_fleet.keys()):
            if k not in current_kapinos:
                _fleet.pop(k, None)

        _fleet_updated_at = datetime.now(UTC)
        _fleet_updated_at_mono = now

    # Rebuild O(1) clean lookup dictionaries
    new_clean_fleet: dict[str, dict[str, Any]] = {}
    new_plate_fleet: dict[str, dict[str, Any]] = {}
    for k, b_dict in _fleet.items():
        clean_k = re.sub(r"[^A-Z0-9]", "", k.upper())
        if clean_k:
            new_clean_fleet[clean_k] = b_dict
        pl = b_dict.get("plate")
        if pl:
            clean_pl = re.sub(r"[^A-Z0-9]", "", str(pl).upper())
            if clean_pl:
                new_plate_fleet[clean_pl] = b_dict

    global _clean_fleet, _plate_fleet  # noqa: PLW0603
    _clean_fleet = new_clean_fleet
    _plate_fleet = new_plate_fleet

    # Populate persistent vehicle cache only with new or changed kapino <-> plate pairs
    new_pairs: list[dict[str, Any]] = []
    for b in buses:
        if not b.plate:
            continue
        clean_k = re.sub(r"[^A-Z0-9]", "", b.kapino.upper())
        clean_p = re.sub(r"[^A-Z0-9]", "", b.plate.upper())
        if clean_k and clean_p and _known_plate_mappings.get(clean_k) != clean_p:
            _known_plate_mappings[clean_k] = clean_p
            norm_p = normalize_plate(b.plate) or b.plate
            new_pairs.append({"door_number": b.kapino, "plate": norm_p})

    if new_pairs:
        from app.services import vehicle_cache  # noqa: PLC0415

        try:
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, vehicle_cache.upsert_vehicles_batch, new_pairs)
        except RuntimeError:
            vehicle_cache.upsert_vehicles_batch(new_pairs)


# ---------------------------------------------------------------------------
# Stop spatial index
# ---------------------------------------------------------------------------
# Holds the full 15 k stop catalogue fetched at startup; keyed by list index.
# Each element is a NearbyStop model_dump dict with all fields populated.
_stop_index: list[dict[str, Any]] = []
_stop_by_code: dict[str, dict[str, Any]] = {}  # stop_code → stop dict
_stop_index_updated_at: datetime | None = None

_R_EARTH = 6_371_000.0  # metres


def update_stop_index(stops: list[NearbyStop]) -> None:
    global _stop_index, _stop_by_code, _stop_index_updated_at  # noqa: PLW0603
    _stop_index = [s.model_dump() for s in stops]
    new_by_code: dict[str, dict[str, Any]] = {}
    for s in _stop_index:
        code = s["stop_code"]
        existing = _stop_by_code.get(code)
        if existing:
            if not s.get("district") and existing.get("district"):
                s["district"] = existing["district"]
            if not s.get("direction") and existing.get("direction"):
                s["direction"] = existing["direction"]
        new_by_code[code] = s
    _stop_by_code = new_by_code
    _stop_index_updated_at = datetime.now(UTC)


def clear_stop_store() -> None:
    """Clear in-memory stop state (used for test teardown)."""
    _stop_index.clear()
    _stop_by_code.clear()


def record_stops_batch(items: list[dict[str, Any]], persist: bool = True) -> None:
    """Record discovered stops into in-memory store and persist asynchronously to SQLite stop cache."""
    if not items:
        return

    cleaned_items: list[dict[str, Any]] = []
    for item in items:
        code = str(item.get("stop_code") or item.get("dcode") or "").strip()
        if not code:
            continue
        name = str(item.get("stop_name") or item.get("name") or "").strip()
        lat = item.get("latitude")
        if lat is None:
            lat = item.get("lat")
        lon = item.get("longitude")
        if lon is None:
            lon = item.get("lon")

        district = item.get("district") or item.get("ilce")
        direction = item.get("direction")
        if direction and str(direction).strip().upper() in ("G", "D"):
            direction = None

        cleaned = {
            "stop_code": code,
            "stop_name": name,
            "latitude": float(lat) if lat is not None else None,
            "longitude": float(lon) if lon is not None else None,
            "district": str(district).strip() if district else None,
            "direction": str(direction).strip() if direction else None,
        }

        existing = _stop_by_code.get(code)
        if existing:
            had_coords = (
                existing.get("latitude") is not None and existing.get("longitude") is not None
            )
            if not existing.get("stop_name") and name:
                existing["stop_name"] = name
            elif not name and existing.get("stop_name"):
                cleaned["stop_name"] = existing["stop_name"]

            if existing.get("latitude") is None and cleaned["latitude"] is not None:
                existing["latitude"] = cleaned["latitude"]
            if existing.get("longitude") is None and cleaned["longitude"] is not None:
                existing["longitude"] = cleaned["longitude"]
            if not existing.get("district") and cleaned["district"]:
                existing["district"] = cleaned["district"]
            if not existing.get("direction") and cleaned["direction"]:
                existing["direction"] = cleaned["direction"]

            if (
                not had_coords
                and existing.get("latitude") is not None
                and existing.get("longitude") is not None
            ):
                _stop_index.append(existing)
        else:
            _stop_by_code[code] = cleaned.copy()
            if cleaned["latitude"] is not None and cleaned["longitude"] is not None:
                _stop_index.append(cleaned.copy())

        cleaned_items.append(cleaned)

    if persist and cleaned_items:
        items_to_persist = [item for item in cleaned_items if item.get("stop_name")]
        if items_to_persist:
            from app.services import stop_cache  # noqa: PLC0415

            try:
                loop = asyncio.get_running_loop()
                loop.run_in_executor(None, stop_cache.upsert_stops_batch, items_to_persist)
            except RuntimeError:
                stop_cache.upsert_stops_batch(items_to_persist)


def get_stop_index_updated_at() -> datetime | None:
    return _stop_index_updated_at


def get_stop_coords(stop_code: str) -> tuple[float, float] | None:
    """Return (latitude, longitude) for a stop code, or None if not in index."""
    s = _stop_by_code.get(stop_code)
    if s is None:
        return None
    return s["latitude"], s["longitude"]


def get_stop_info_by_code(stop_code: str) -> dict[str, Any] | None:
    """Return full stop dict for a stop code from in-memory catalogue, or None."""
    return _stop_by_code.get(stop_code)


def get_nearby_stops(lat: float, lon: float, radius_m: float = 500.0) -> list[dict[str, Any]]:
    """Return stops within *radius_m* metres sorted by ascending distance.

    Uses full haversine — accurate enough for Istanbul-scale distances.
    """
    phi1 = math.radians(lat)
    cos_phi1 = math.cos(phi1)
    out: list[dict[str, Any]] = []
    for s in _stop_index:
        phi2 = math.radians(s["latitude"])
        dphi = phi2 - phi1
        dlam = math.radians(s["longitude"] - lon)
        a = math.sin(dphi / 2) ** 2 + cos_phi1 * math.cos(phi2) * math.sin(dlam / 2) ** 2
        d = _R_EARTH * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - min(1.0, a))))
        if d <= radius_m:
            out.append({**s, "distance_m": round(d, 1)})
    out.sort(key=lambda x: x["distance_m"])
    return out
