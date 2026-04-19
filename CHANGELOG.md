# Changelog

All notable changes to iett-middle are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.3.14] - 2026-04-19

### Fixed
- ARAC captcha bootstrap now retries across `/session/captcha` and `/session/getpicture` with POST/GET fallback ordering, reducing upstream 405 breakage.
- ARAC upstream HTML error pages are sanitized before surfacing as API errors.

### Tests
- Expanded ARAC captcha fallback branch coverage (retry policy matrix, non-retry stop path, full fallback exhaustion, and payload-shape continuation checks).

### Release Notes
- Released as `v0.3.14`.

---

## [0.3.13] - 2026-04-19

### Changed
- Version bump for GHCR publish so server-side pull testing can target an explicit tag.

### Release Notes
- Released as `v0.3.13`.

---

## [0.3.12] - 2026-04-19

### Changed
- GHCR Docker publish remains multi-arch (`linux/amd64` and `linux/arm64`).
- OCR-enabled image install path now uses the PyTorch CPU index for `torch` and `torchvision` across both architectures.

### Fixed
- Prevented CUDA/NVIDIA runtime package pull-ins in OCR-enabled GHCR images, reducing image bloat risk while preserving ARM support.

### Release Notes
- Released as `v0.3.12`.

---

## [0.3.11] - 2026-04-19

### Changed
- Docker build now uses a multi-stage layout so compiler/dev packages stay in the builder image, reducing final runtime image size.
- ARAC OCR dependencies were split into `requirements-ocr.txt` and made Docker-optional via `INSTALL_OCR` build arg.
- Outgoing aiohttp trace hooks are now opt-in via `ENABLE_OUTGOING_TRACE=false` by default to reduce request-path logging overhead.

### Fixed
- amd64/no-GPU Docker builds no longer need CUDA/NVIDIA wheel downloads when OCR is disabled (`INSTALL_OCR=0`) and use CPU-only torch wheels when OCR is enabled.

### Release Notes
- Released as `v0.3.11`.

---

## [0.3.10] - 2026-04-19

### Added
- ARAC user-session router and endpoints under `/v1/arac`:
  - `POST /v1/arac/session/captcha`
  - `POST /v1/arac/session/getpicture` (alias)
  - `POST /v1/arac/session/create`
  - `POST /v1/arac/session/response` (alias)
  - `POST /v1/arac/session/auto-solve`
  - `GET /v1/arac/fleet`
  - `GET /v1/arac/fleet/{kapino}`
  - `GET /v1/arac/fleet/{kapino}/missions`
  - `GET /v1/arac/routes/{route_id}/stops`
- New ARAC client service (`app/services/arac_client.py`) with:
  - captcha/session bootstrap
  - encrypted task key exchange (`/task/crypto/pubkey`)
  - RSA OAEP + AES-GCM decryption flow for task endpoints
  - normalization for fleet, missions, and route-stops payloads
- New ARAC captcha solver service (`app/services/arac_captcha_solver.py`) using bounded OCR candidate strategy (masked/original/threshold plus ambiguity expansion).
- New ARAC Pydantic models (`app/models/arac.py`) for session, missions, route-stops, and auto-solve contract payloads.
- Canonical ARAC enrichment fields added to `CanonicalBusPosition` for schema parity with integration docs.
- Dedicated ARAC service test modules:
  - `tests/test_arac_client.py`
  - `tests/test_arac_captcha_solver.py`

### Changed
- `BusPosition` now includes optional ARAC profile fields (`operator_id`, `operator_name`, `vehicle_brand`, `model_year`, `vehicle_type`, `seating_capacity`, `full_capacity`, `accessible`, `has_usb`, `has_wifi`, `has_bicycle_rack`, `is_air_conditioned`, `garage_code`, `garage_name`, `vehicle_software_version`).
- `app/main.py` now registers the ARAC router and exposes the ARAC API surface in OpenAPI.
- Auto-solve default mode is now guess-only (`createSession=false` by default), aligned with user-first captcha flow.
- README endpoint catalog and config docs updated for ARAC routes and no-storage session handling.

### Security
- ARAC session credentials are request-scoped and client-owned.
- No persistence layer was added for ARAC `sessionId` / `sessionKey`.

### Dependencies
- Added encrypted/auth-flow dependencies for ARAC automation:
  - `cryptography>=42.0`
  - `numpy>=1.26`
  - `opencv-python-headless>=4.10`
  - `easyocr>=1.7`

### Tests
- Full middle test suite now includes ARAC endpoint, service, and auto-captcha coverage expansions.
- Current full suite status: `373 passed`.

### Release Notes
- Released as `v0.3.10`.

---

## [0.3.9] - 2026-04-16

### Changed
- Manual fleet refresh now enforces a cooldown guard and returns explicit cooldown metadata for callers.
- Fleet refresh lock handling was tightened to avoid overlapping refresh work when refresh requests arrive close together.
- Periodic fleet refresh is clamped to a minimum 15-minute max-age window to avoid stale FILO snapshots.
- Docker runtime uses a single uvicorn worker so in-memory refresh/cooldown state remains process-consistent.

### Added
- Regression tests for fleet poller scheduling and refresh task cancellation paths.
- Regression tests for manual fleet refresh cooldown behavior.

---

## [0.3.8] – 2026-04-07

### Added
- Test coverage for fleet detail all-sources-fail fallback (`route_stops: []`)
- Test coverage for arrivals `via` filtering (success and IETT lookup failure paths)
- Test coverage for `/v1/stops/{dcode}/arrivals/raw` success and upstream-failure responses

### Changed
- `Arrival.amenities` now typed as `Amenities | None` instead of bare `dict | None`

### Fixed
- Added missing `asyncio` import in `fleet` router module
- Simplified route-bus fallback exception handling (removed redundant tuple members)
- `stops` router now logs a warning when `via` route lookup fails, while still returning unfiltered arrivals
- Removed unused `AsyncMock` import in `tests/test_ntcapi_client.py`

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
