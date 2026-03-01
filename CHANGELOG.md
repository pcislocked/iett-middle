# Changelog

All notable changes to iett-middle are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.2.1] – 2026-03-01

### Fixed
- `CanonicalBusPosition.lat/lon` typed as `float | None` — normalizers no longer emit bogus `(0.0, 0.0)` for unparseable coords
- `CanonicalScheduledDeparture.departure_time` typed as `str | None` to match normalizer output
- Nearby stops router: invalid/missing coordinates are skipped with a warning rather than defaulting to `0.0`
- Schedule router: rows with missing `route_code` or `departure_time` are filtered out instead of being cached with empty strings
- `normalizers/positions`: removed `_safe_float(...) or 0.0` fallback (returns `None`)
- `routers/routes`: direction stop-list fetches now run concurrently via `asyncio.gather`
- Docstring corrections in `normalizers/__init__` and `normalizers/arrivals` (Copilot review PR #6)

---

## [0.2.0] – 2026-03-01

### Added
- `app/models/canonical.py` — internal TypedDict types: `CanonicalArrival`,
  `CanonicalBusPosition`, `CanonicalRouteStop`, `CanonicalScheduledDeparture`,
  `CanonicalStop`, `Amenities`
- `app/services/normalizers/` package — 5 modules (`arrivals`, `positions`,
  `route_stops`, `schedule`, `stops`) that map raw API dicts from any source
  to the canonical shape.  Pure functions — no async, no I/O.
- 42 unit tests for all normalizer functions in `tests/test_normalizers.py`

### Changed
- `app/models/bus.Arrival`: `speed` renamed to `speed_kmh`; flat amenity fields
  `usb / wifi / klima / engelli` replaced by `amenities: dict | None`
- `ntcapi_client.get_stop_arrivals()` now returns raw ybs dicts; callers apply
  the normalizer (breaking change for direct callers)
- `stops` router arrivals + nearby: ntcapi primary → normalizer → Pydantic
- `routes` router stops / metadata / schedule: ntcapi primary → normalizer → Pydantic;
  IETT SOAP fallback retained for all endpoints

---

## [0.1.9] – 2026-03-01

### Added
- `app/services/ntcapi_client.py` — full client for `ntcapi.iett.istanbul` private API
  - OAuth2 `client_credentials` token fetch with in-process cache (refreshes 60 s before expiry)
  - `get_stop_arrivals()` via `ybs` alias: returns kapino, live lat/lon, speed, amenity flags
  - `get_bus_location()` via `mainGetBusLocation_basic`: plate + live position by kapino
  - `get_route_stops()` via `mainGetRoute`: ordered stop list with coordinates
  - `get_route_metadata()` via `mainGetLine`: route variant metadata
  - `get_timetable()` via `akyolbilGetTimeTable`: all day types (H/C/P)
  - `get_nearby_stops()` via `mainGetBusStopNearby`: geo-radius stop search
  - `NtcApiError` for clean fallback handling

### Changed
- `Arrival` model gains `lat`, `lon`, `speed`, `last_seen_ts`, `usb`, `wifi`, `klima`, `engelli`
- `GET /{dcode}/arrivals` uses ntcapi `ybs` as primary source, IETT HTML as automatic fallback
- ntcapi credentials configurable via `Settings` class / environment variables

---

## [0.1.4] – 2026-02-28

### Fixed
- **BUG-14** – `GET /v1/routes/{hat_kodu}/stops` no longer returns 502.
  The `DurakDetay_GYY` SOAP endpoint on `api.ibb.gov.tr` returns HTTP 500 for
  all routes globally; replaced with an HTML scrape of
  `iett.istanbul/tr/RouteStation/GetStationForRoute`.  Stop coordinates are
  enriched from the in-memory stop index populated at startup.  Responses that
  arrive before the index is ready (some coords `null`) are intentionally not
  written to the long-lived `routes:stops:*` cache to prevent poisoning.

### Changed
- `RouteStop.latitude` / `longitude` changed from `float` to `float | None`
  to correctly represent stops whose coordinates are not yet available.
- `GET /v1/routes/{hat_kodu}/stops` only writes to cache when every stop in
  the response carries a valid coordinate pair.

---

## [0.1.3] – 2026-02-28

_Initial public versioning — baseline release._
