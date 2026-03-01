"""Stop normalizers — raw API item → CanonicalStop.

Sources:
  ntcapi  nearbyStops    → ``from_ntcapi_nearby(item)``
"""
from __future__ import annotations

from typing import Any

from app.models.canonical import CanonicalStop


def from_ntcapi_nearby(item: dict[str, Any]) -> CanonicalStop:
    """Normalise one record from ntcapi nearbyStops.

    Field mapping::

        DURAK_DURAK_KODU     → stop_code
        DURAK_ADI            → stop_name
        DURAK_GEOLOC.y       → lat   (y = latitude)
        DURAK_GEOLOC.x       → lon   (x = longitude)
        DURAK_YON_BILGISI    → direction
        ILCELER_ILCEADI      → district
        DISTANCE             → distance_m  (metres, may be float)
    """
    geoloc: dict[str, Any] = item.get("DURAK_GEOLOC") or {}
    lat = _safe_float(geoloc.get("y"))
    lon = _safe_float(geoloc.get("x"))

    return CanonicalStop(
        stop_code=str(item.get("DURAK_DURAK_KODU") or ""),
        stop_name=item.get("DURAK_ADI") or None,
        lat=lat,
        lon=lon,
        direction=item.get("DURAK_YON_BILGISI") or None,
        district=item.get("ILCELER_ILCEADI") or None,
        distance_m=_safe_float(item.get("DISTANCE")),
        _source="ntcapi_nearby",
    )


def from_ntcapi_nearby_processed(item: dict[str, Any]) -> CanonicalStop:
    """Normalise a pre-processed stop dict from ``ntcapi_client.get_nearby_stops``.

    Expected keys (flat, already mapped by the client)::

        stop_code, stop_name, lat, lon, direction
    """
    return CanonicalStop(
        stop_code=str(item.get("stop_code") or ""),
        stop_name=item.get("stop_name") or None,
        lat=_safe_float(item.get("lat")),
        lon=_safe_float(item.get("lon")),
        direction=item.get("direction") or None,
        district=None,      # not present in pre-processed format
        distance_m=None,    # not returned by this endpoint
        _source="ntcapi_nearby",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
