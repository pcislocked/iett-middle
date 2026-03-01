"""Schedule normalizers — raw API item → CanonicalScheduledDeparture.

Sources:
  ntcapi  akyolbilGetTimeTable        → ``from_ntcapi_timetable(item)``
  IETT SOAP schedule                  → ``from_iett_soap_schedule(item)``
"""
from __future__ import annotations

from typing import Any

from app.models.canonical import CanonicalScheduledDeparture

# ntcapi day-type codes → canonical day_type
_DAY_TYPE_MAP: dict[str, str] = {
    "C": "C",
    "I": "H",
    "\u0130": "H",  # İ (capital dotted I, Turkish upper-case of i)
    "P": "P",
}


def from_ntcapi_timetable(item: dict[str, Any]) -> CanonicalScheduledDeparture:
    """Normalise one record from ntcapi akyolbilGetTimeTable.

    Field mapping::

        GUZERGAH_HAT_KODU   → route_code  (also used as route_name — no name in response)
        K_ORER_SGUZERGAH    → route_variant
        K_ORER_SYON         → direction
        K_ORER_SGUNTIPI     → day_type  ("I"/"İ" → "H")
        K_ORER_SSERVISTIPI  → service_type
        K_ORER_DTSAATGIDIS  → departure_time  (extract "HH:MM" from "YYYY-MM-DD HH:MM:SS")
    """
    raw_dt: str = item.get("K_ORER_DTSAATGIDIS") or ""
    departure_time: str | None = _extract_hhmm(raw_dt)

    raw_day: str = str(item.get("K_ORER_SGUNTIPI") or "")
    day_type: str | None = _DAY_TYPE_MAP.get(raw_day, raw_day) or None

    route_code: str | None = item.get("GUZERGAH_HAT_KODU") or None

    return CanonicalScheduledDeparture(
        route_code=route_code,
        route_name=route_code,  # ntcapi timetable has no separate name field
        route_variant=item.get("K_ORER_SGUZERGAH") or None,
        direction=item.get("K_ORER_SYON") or None,
        day_type=day_type,
        service_type=item.get("K_ORER_SSERVISTIPI") or None,
        departure_time=departure_time,
        _source="ntcapi_timetable",
    )


def from_iett_soap_schedule(item: dict[str, Any]) -> CanonicalScheduledDeparture:
    """Normalise one ScheduledDeparture.model_dump() record from the IETT SOAP schedule.

    The existing Pydantic model already has clean fields; this is a
    straight pass-through into the canonical shape.
    """
    return CanonicalScheduledDeparture(
        route_code=item.get("route_code") or None,
        route_name=item.get("route_name") or None,
        route_variant=item.get("route_variant") or None,
        direction=item.get("direction") or None,
        day_type=item.get("day_type") or None,
        service_type=item.get("service_type") or None,
        departure_time=item.get("departure_time") or None,
        _source="iett_soap_schedule",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_hhmm(raw: str) -> str | None:
    """Extract HH:MM from a "YYYY-MM-DD HH:MM:SS" datetime string."""
    # Expected format: "2026-03-01 05:45:00"
    try:
        time_part = raw.strip().split(" ", 1)[1]  # "05:45:00"
        parts = time_part.split(":")
        return f"{parts[0]}:{parts[1]}"
    except (IndexError, AttributeError):
        return None
