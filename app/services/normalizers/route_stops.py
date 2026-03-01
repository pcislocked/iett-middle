"""Route-stop normalizers — raw API item → CanonicalRouteStop.

Sources:
  ntcapi  mainGetRoute                → ``from_ntcapi_route(item)``
  IETT HTML route-stop scrape         → ``from_iett_html_route_stops(item)``
"""
from __future__ import annotations

from typing import Any

from app.models.canonical import CanonicalRouteStop


def from_ntcapi_route(item: dict[str, Any]) -> CanonicalRouteStop:
    """Normalise one stop record from ntcapi mainGetRoute.

    Field mapping::

        GUZERGAH_GUZERGAH_KODU              → route_code
        GUZERGAH_YON  "119" → "G" (outbound)
                      "120" → "D" (return)   → direction
        GUZERGAH_SEGMENT_SIRA               → sequence
        DURAK_DURAK_KODU                    → stop_code
        DURAK_ADI                           → stop_name
        DURAK_GEOLOC.x                      → lon
        DURAK_GEOLOC.y                      → lat
        ILCELER_ILCEADI                     → district
    """
    yon_raw = str(item.get("GUZERGAH_YON") or "")
    direction: str | None
    if yon_raw == "119":
        direction = "G"
    elif yon_raw == "120":
        direction = "D"
    else:
        direction = yon_raw or None

    geoloc: dict[str, Any] = item.get("DURAK_GEOLOC") or {}
    lat = _safe_float(geoloc.get("y"))
    lon = _safe_float(geoloc.get("x"))

    return CanonicalRouteStop(
        route_code=item.get("GUZERGAH_GUZERGAH_KODU") or None,
        direction=direction,
        sequence=_safe_int(item.get("GUZERGAH_SEGMENT_SIRA")),
        stop_code=str(item.get("DURAK_DURAK_KODU") or ""),
        stop_name=item.get("DURAK_ADI") or None,
        lat=lat,
        lon=lon,
        district=item.get("ILCELER_ILCEADI") or None,
        _source="ntcapi_route",
    )


def from_ntcapi_route_processed(item: dict[str, Any]) -> CanonicalRouteStop:
    """Normalise one dict already pre-processed by ``ntcapi_client.get_route_stops``.

    Expected keys (flat, already mapped)::

        route_code, stop_code, stop_name, sequence,
        lat, lon, district, direction_letter ("G"/"D")
    """
    return CanonicalRouteStop(
        route_code=item.get("route_code") or None,
        direction=item.get("direction_letter") or None,
        sequence=_safe_int(item.get("sequence")),
        stop_code=str(item.get("stop_code") or ""),
        stop_name=item.get("stop_name") or None,
        lat=_safe_float(item.get("lat")),
        lon=_safe_float(item.get("lon")),
        district=item.get("district") or None,
        _source="ntcapi_route",
    )


def from_iett_html_route_stops(item: dict[str, Any]) -> CanonicalRouteStop:
    """Normalise one RouteStop.model_dump() record from the IETT HTML scrape.

    The existing RouteStop Pydantic model already contains clean fields,
    so this is a direct pass-through into the canonical shape.
    """
    return CanonicalRouteStop(
        route_code=item.get("route_code") or None,
        direction=item.get("direction") or None,
        sequence=_safe_int(item.get("sequence")),
        stop_code=str(item.get("stop_code") or ""),
        stop_name=item.get("stop_name") or None,
        lat=_safe_float(item.get("lat")),
        lon=_safe_float(item.get("lon")),
        district=item.get("district") or None,
        _source="iett_html_route",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
