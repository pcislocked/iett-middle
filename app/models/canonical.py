"""Canonical typed data structures shared across all upstream sources.

Every router normalises raw API responses into these types before caching,
enriching, or serving.  The ``_source`` field carries the origin tag for
observability; it is stripped before HTTP serialisation.

Sources:
  ntcapi_ybs              — ntcapi.iett.istanbul  ``ybs`` alias (stop arrivals)
  ntcapi_location         — ntcapi  ``mainGetBusLocation_basic``
  ntcapi_route            — ntcapi  ``mainGetRoute``
  ntcapi_line             — ntcapi  ``mainGetLine``
  ntcapi_timetable        — ntcapi  ``akyolbilGetTimeTable``
  ntcapi_nearby           — ntcapi  ``mainGetBusStopNearby``
  iett_html               — iett.istanbul HTML scrape (GetStationInfo)
  iett_soap_fleet         — api.ibb.gov.tr  GetFiloAracKonum_json
  iett_soap_route_fleet   — api.ibb.gov.tr  GetHatOtoKonum_json
  iett_soap_schedule      — api.ibb.gov.tr  GetPlanlananSeferSaati_json
  iett_soap_route_stops   — iett.istanbul   GetStationForRoute (HTML)
"""
from __future__ import annotations

from typing import Literal

# Python 3.12 ships TypedDict in typing; total=False means all keys optional
# by default so we can build them incrementally from sparse sources.
# Required fields use Literal / non-None typehints to document intent.
from typing import TypedDict


# ---------------------------------------------------------------------------
# Amenity flags — nested inside CanonicalArrival
# ---------------------------------------------------------------------------

class Amenities(TypedDict, total=False):
    usb: bool | None           # USB charging port on board
    wifi: bool | None          # Wi-Fi on board
    ac: bool | None            # Air conditioning (klima)
    accessible: bool | None    # Wheelchair accessible (engelli)


# ---------------------------------------------------------------------------
# CanonicalArrival — one vehicle approaching a stop
# ---------------------------------------------------------------------------

class CanonicalArrival(TypedDict, total=False):
    route_code: str            # "15TY"
    destination: str           # "TOKATKÖY"
    eta_minutes: int | None    # 10  (None = at stop / unknown)
    eta_raw: str               # "15:13"  or "(00:10) 4 dk"
    kapino: str | None         # "C-1080"  — internal bus bay/door ID
    plate: str | None          # "34 HO 3524"  — enriched from fleet store
    lat: float | None          # 41.093  — live bus latitude
    lon: float | None          # 29.089  — live bus longitude
    # NOTE: ybs son_konum string is "lon,lat" — normaliser swaps the order
    speed_kmh: int | None      # 13
    last_seen_ts: str | None   # "15:03:29"
    amenities: Amenities       # all None when source doesn't provide
    _source: str               # e.g. "ntcapi_ybs" | "iett_html"


# ---------------------------------------------------------------------------
# CanonicalBusPosition — live GPS snapshot of one vehicle
# ---------------------------------------------------------------------------

class CanonicalBusPosition(TypedDict, total=False):
    kapino: str                # "C-325"
    plate: str | None          # "34 HO 1000"
    lat: float | None          # 41.083  — None when coords unparseable
    lon: float | None          # 29.050  — None when coords unparseable
    speed_kmh: int | None      # km/h, 0 when stationary
    last_seen: str             # timestamp string (format varies per source)
    route_code: str | None     # "14M"
    direction: str | None      # "G" (outbound) | "D" (return)
    nearest_stop_code: str | None
    # NOTE: iett_soap_fleet uses CAPITALISED keys (KapiNo, Plaka, Enlem, Boylam)
    #       iett_soap_route_fleet uses lowercase (kapino, enlem, boylam)
    #       Both normalise to this canonical shape.
    _source: str               # "iett_soap_fleet" | "iett_soap_route_fleet" | "ntcapi_location"


# ---------------------------------------------------------------------------
# CanonicalRouteStop — one stop in a route's ordered stop list
# ---------------------------------------------------------------------------

class CanonicalRouteStop(TypedDict, total=False):
    route_code: str            # "14M_G_D0"  (variant code)
    direction: str             # "G" | "D"
    sequence: int              # 1-based position in the route
    stop_code: str             # "220731"
    stop_name: str             # "YENİ CAMİİ"
    lat: float | None
    lon: float | None
    district: str | None       # "Beykoz"
    # NOTE: ntcapi mainGetRoute uses GUZERGAH_YON "119" (G) / "120" (D)
    #       normaliser maps these to direction letters
    _source: str               # "ntcapi_route" | "iett_soap_route_stops"


# ---------------------------------------------------------------------------
# CanonicalScheduledDeparture — one planned departure row
# ---------------------------------------------------------------------------

_DayType = Literal["H", "C", "P"]   # H=weekday, C=Saturday, P=Sunday (Pazar)
_Direction = Literal["G", "D"]       # G=outbound (gidiş), D=return (dönüş)


class CanonicalScheduledDeparture(TypedDict, total=False):
    route_code: str            # "500T"
    route_name: str            # "TUZLA ŞİFA MAHALLESİ - 4. LEVENT METRO"
    route_variant: str         # "500T_D_D0"
    direction: str             # "G" | "D"
    day_type: str              # "H" | "C" | "P"
    # IETT SOAP uses "I"/"İ" for weekday — normaliser converts to "H"
    service_type: str          # "ÖHO"
    departure_time: str | None # "HH:MM" — None when source row is malformed
    _source: str               # "ntcapi_timetable" | "iett_soap_schedule"


# ---------------------------------------------------------------------------
# CanonicalStop — stop identity + coordinates
# ---------------------------------------------------------------------------

class CanonicalStop(TypedDict, total=False):
    stop_code: str             # "220731"
    stop_name: str             # "YENİ CAMİİ"
    lat: float | None
    lon: float | None
    direction: str | None      # "G" | "D" | None (stops can be bidirectional)
    district: str | None       # "Beykoz"
    distance_m: float | None   # populated by nearby-stop queries
    _source: str               # "ntcapi_nearby" | "iett_soap_search" | ...
