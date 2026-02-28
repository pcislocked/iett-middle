"""Shared FastAPI dependencies and in-memory stores.

Import from here, never from main.py, to avoid circular imports.
"""
from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any

import aiohttp

if TYPE_CHECKING:
    from app.models.bus import BusPosition
    from app.models.stop import NearbyStop

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
# kapino → deque of TrailPoint dicts  {lat, lon, ts}
_trail: dict[str, deque[dict[str, Any]]] = {}
_fleet_updated_at: datetime | None = None


def get_fleet_snapshot() -> list[dict[str, Any]]:
    """Return current fleet as a list of dicts (thread-safe read)."""
    return list(_fleet.values())


def get_trail(kapino: str) -> list[dict[str, Any]]:
    return list(_trail.get(kapino, deque()))


def get_fleet_updated_at() -> datetime | None:
    return _fleet_updated_at


def get_buses_near_stop(dcode: str) -> list[dict[str, Any]]:
    """Return all fleet buses currently with nearest_stop == dcode."""
    return [b for b in _fleet.values() if b.get("nearest_stop") == dcode]


def get_plate_by_kapino(kapino: str) -> str | None:
    """Look up the plate for a given kapino from the in-memory fleet store."""
    return _fleet.get(kapino, {}).get("plate")


def update_fleet(buses: list[BusPosition]) -> None:  # noqa: C901
    """Called by the background poller.  Updates fleet dict and trail deques."""
    global _fleet_updated_at  # noqa: PLW0603
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
                {"lat": prev["latitude"], "lon": prev["longitude"], "ts": prev["last_seen"]}
            )
        elif k not in _trail:
            _trail[k] = deque(maxlen=max_entries)
        _fleet[k] = b.model_dump()

    _fleet_updated_at = datetime.now()


# ---------------------------------------------------------------------------
# Stop spatial index
# ---------------------------------------------------------------------------
# Holds the full 15 k stop catalogue fetched at startup; keyed by list index.
# Each element is a NearbyStop model_dump dict with all fields populated.
_stop_index: list[dict[str, Any]] = []
_stop_by_code: dict[str, dict[str, Any]] = {}  # stop_code → stop dict
_stop_index_updated_at: datetime | None = None

_R_EARTH = 6_371_000.0  # metres


def update_stop_index(stops: list[NearbyStop]) -> None:  # type: ignore[name-defined]
    global _stop_index, _stop_by_code, _stop_index_updated_at  # noqa: PLW0603
    _stop_index = [s.model_dump() for s in stops]
    _stop_by_code = {s["stop_code"]: s for s in _stop_index}
    _stop_index_updated_at = datetime.now()


def get_stop_index_updated_at() -> datetime | None:
    return _stop_index_updated_at


def get_stop_coords(stop_code: str) -> tuple[float, float] | None:
    """Return (latitude, longitude) for a stop code, or None if not in index."""
    s = _stop_by_code.get(stop_code)
    if s is None:
        return None
    return s["latitude"], s["longitude"]


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
        d = _R_EARTH * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        if d <= radius_m:
            out.append({**s, "distance_m": round(d, 1)})
    out.sort(key=lambda x: x["distance_m"])
    return out
