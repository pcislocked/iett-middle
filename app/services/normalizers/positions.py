"""Position normalizers — raw API item → CanonicalBusPosition.

Sources:
  IETT SOAP GetFiloAracKonum_json   — ``from_iett_soap_fleet(item)``
  IETT SOAP GetHatOtoKonum_json     — ``from_iett_soap_route_fleet(item)``

Key quirk: the two IETT SOAP fleet endpoints return the same logical data
with DIFFERENT key casing:
  All-fleet  → CAPITALISED  (KapiNo, Plaka, Enlem, Boylam, Hiz, Saat)
  Route-fleet → lowercase   (kapino, enlem, boylam, son_konum_zamani)
Both normalise into the same CanonicalBusPosition shape.
"""
from __future__ import annotations

from typing import Any

from app.models.canonical import CanonicalBusPosition


def from_iett_soap_fleet(item: dict[str, Any]) -> CanonicalBusPosition:
    """Normalise one record from GetFiloAracKonum_json (CAPITALISED keys).

    Field mapping::

        KapiNo                       → kapino
        Plaka                        → plate
        Enlem                        → lat
        Boylam                       → lon
        Hiz / Hız / HIZ / hiz / hız  → speed_kmh  (Turkish ı encoding varies)
        Saat                         → last_seen
        HatKodu / HATKODU / hatkodu  → route_code
    """
    speed_raw = next(
        (item[k] for k in ("Hiz", "H\u0131z", "HIZ", "hiz", "h\u0131z") if k in item),
        None,
    )
    return CanonicalBusPosition(
        kapino=str(item.get("KapiNo") or ""),
        plate=item.get("Plaka") or None,
        lat=_safe_float(item.get("Enlem")),
        lon=_safe_float(item.get("Boylam")),
        speed_kmh=_safe_int(speed_raw),
        last_seen=str(item.get("Saat") or ""),
        route_code=(
            item.get("HatKodu") or item.get("HATKODU") or item.get("hatkodu") or None
        ),
        direction=None,
        nearest_stop_code=None,
        _source="iett_soap_fleet",
    )


def from_iett_soap_route_fleet(item: dict[str, Any]) -> CanonicalBusPosition:
    """Normalise one record from GetHatOtoKonum_json (lowercase keys).

    Field mapping::

        kapino               → kapino
        enlem                → lat
        boylam               → lon
        son_konum_zamani     → last_seen
        hatkodu              → route_code
        yon                  → direction
        yakinDurakKodu       → nearest_stop_code
    """
    return CanonicalBusPosition(
        kapino=str(item.get("kapino") or ""),
        plate=None,  # not present in route-fleet endpoint
        lat=_safe_float(item.get("enlem")),
        lon=_safe_float(item.get("boylam")),
        speed_kmh=None,  # not present in route-fleet endpoint
        last_seen=str(item.get("son_konum_zamani") or ""),
        route_code=item.get("hatkodu") or None,
        direction=item.get("yon") or None,
        nearest_stop_code=str(item.get("yakinDurakKodu")) if item.get("yakinDurakKodu") else None,
        _source="iett_soap_route_fleet",
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
