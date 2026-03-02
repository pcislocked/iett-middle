# Changelog

All notable changes to iett-middle are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.3.1] – 2026-03-02

### Added
- `GET /v1/fleet/{kapino}/detail` — resolves route code (live or last-known
  fallback), returns `route_is_live` flag and ordered `route_stops` list for
  route polyline rendering without a second round-trip
- `BusDetail.route_stops` typed as `list[RouteStop]` for proper OpenAPI schema
  (was `list[dict]`)

### Fixed
- `route_code` normalization (`.strip().upper()`) in `_kapino_last_route` and
  live fleet lookup to prevent cache misses from whitespace or case differences
- Cache guard now checks both `latitude` and `longitude` not `None` before
  writing to the route-stops cache
- Exception handling narrowed: `NtcApiError|IettApiError` separated from
  unexpected `Exception` with `logger.exception` on unexpected errors
- IETT SOAP fallback deduplicated into a single path via `needs_fallback` flag
  to avoid divergence between the two exception branches
- Nearby stops router: `distance_m` uses `is not None` check instead of
  truthiness to correctly preserve a computed distance of `0.0`
- `TestStopArrivals` class nesting corrected in `tests/test_routers.py`

---

## [0.3.0] – 2026-06-03

### Added
- `GET /routes/{route_code}/buses` — live bus positions via YBS point-passing API
  (`ntcapi_client.get_route_buses_ybs`), with fallback handling
- `direction_letter` field on bus positions (`D` / `G` / `?`)
- `stop_sequence` field on bus positions from `H_GOREV_DURAK_GECIS_SIRANO`

### Fixed
- `route_code` on YBS bus positions now returns public code (e.g. `500T`) instead of
  internal variant string (e.g. `500T_D_D0`)
- `int(seq)` on malformed `H_GOREV_DURAK_GECIS_SIRANO` values now returns `None`
  instead of raising `ValueError`
- Return type annotation on `get_route_buses_ybs` corrected to `list[BusPosition]`

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
