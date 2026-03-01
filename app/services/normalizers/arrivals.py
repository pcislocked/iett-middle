"""Arrival normalizers — raw API item → CanonicalArrival.

Sources:
  ntcapi ybs          — ``from_ntcapi_ybs(item)``
  IETT HTML scrape    — ``from_iett_html(item)``  (item is Arrival.model_dump())
"""
from __future__ import annotations

from typing import Any

from app.models.canonical import Amenities, CanonicalArrival


def from_ntcapi_ybs(item: dict[str, Any]) -> CanonicalArrival:
    """Normalise one record from the ntcapi ``ybs`` stop-arrivals response.

    Field mapping::

        hatkodu          → route_code
        hattip / hatadi  → destination
        dakika           → eta_minutes  (int or None)
        saat             → eta_raw
        kapino           → kapino
        son_konum        → "lon,lat" string  →  lat, lon  (NOTE: swapped!)
        son_hiz          → speed_kmh
        son_konum_saati  → last_seen_ts
        usb/wifi/klima/engelli → amenities
    """
    lat, lon = _parse_son_konum(item.get("son_konum"))
    return CanonicalArrival(
        route_code=str(item.get("hatkodu") or ""),
        destination=str(item.get("hattip") or item.get("hatadi") or ""),
        eta_minutes=_safe_int(item.get("dakika")),
        eta_raw=str(item.get("saat") or ""),
        kapino=item.get("kapino") or None,
        plate=None,  # enriched by caller from fleet store
        lat=lat,
        lon=lon,
        speed_kmh=_safe_int(item.get("son_hiz")),
        last_seen_ts=item.get("son_konum_saati") or None,
        amenities=Amenities(
            usb=_safe_bool(item.get("usb")),
            wifi=_safe_bool(item.get("wifi")),
            ac=_safe_bool(item.get("klima")),
            accessible=_safe_bool(item.get("engelli")),
        ),
        _source="ntcapi_ybs",
    )


def from_iett_html(item: dict[str, Any]) -> CanonicalArrival:
    """Normalise one Arrival model_dump() from the IETT HTML parser.

    The HTML source never provides live position or amenity data —
    positional fields are set to None and ``amenities`` is an ``Amenities``
    instance with all flags set to None.
    """
    return CanonicalArrival(
        route_code=str(item.get("route_code") or ""),
        destination=str(item.get("destination") or ""),
        eta_minutes=item.get("eta_minutes"),
        eta_raw=str(item.get("eta_raw") or ""),
        kapino=item.get("kapino") or None,
        plate=None,
        lat=None,
        lon=None,
        speed_kmh=None,
        last_seen_ts=None,
        amenities=Amenities(usb=None, wifi=None, ac=None, accessible=None),
        _source="iett_html",
    )


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------

def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_bool(value: Any) -> bool | None:
    """Convert 0/1 int flag to bool, or None if absent."""
    if value is None:
        return None
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None


def _parse_son_konum(value: Any) -> tuple[float | None, float | None]:
    """Parse ntcapi ``son_konum`` string "lon,lat" → (lat, lon).

    IMPORTANT: the string is longitude-first, latitude second.
    We return (lat, lon) in the conventional order.
    """
    if not value:
        return None, None
    try:
        parts = str(value).split(",")
        lon = float(parts[0])
        lat = float(parts[1])
        return lat, lon
    except (IndexError, ValueError):
        return None, None
