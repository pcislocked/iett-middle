# Changelog

All notable changes to iett-middle are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.4.9.2] - 2026-10-18
### Fixed & Hardened
- **CI/CD Action Versions & Caching:** Upgraded GitHub Actions to valid stable versions (`checkout@v4`, `setup-python@v5`), scoped concurrency cancellation to preserve tag builds, and enabled `cache-to: type=gha,mode=max` in Docker workflows.
- **Async Event Loop Offloading:** Offloaded all CPU-bound BeautifulSoup HTML tree parsing routines to worker threads via `asyncio.to_thread`.
- **Session Lifecycle:** Reused FastAPI shared `aiohttp.ClientSession` in `arac.py` with transient session fallback for isolated tests.
- **Cache Thread Safety:** Added bounded retry loop to `get_cache_stats()` for dictionary mutation resilience.
- **Test Coverage:** Added direct unit tests for route/stop scraping and vehicle details (overall coverage increased to 89%).

---

## [0.4.9.1] - 2026-10-18
### Fixed & Resilient Fallbacks
- **Stop Arrivals SOAP Failover:** Implemented automatic fallback to SOAP `GetHatOtoKonum_json` in `app/routers/stops.py` and `app/services/iett_client.py` when NTCAPI fails or returns empty/invalid arrivals, preventing empty stop arrival screens during upstream NTCAPI outages.
- **Bounded In-Memory Vehicle Cache (LRU):** Converted in-memory dictionaries in `app/services/vehicle_cache.py` to `collections.OrderedDict` with bounded size (`max_size=500`, configurable via `app/config.py`) and LRU eviction, eliminating potential memory leaks under high-volume traffic.
- **Synchronized 401 Token Refresh:** Added `asyncio.Lock` synchronization to `iett_client.py` and `arac_client.py` token invalidation and refresh logic to prevent concurrent request stampedes and 401 cascading retry storms.
- **Pre-computed Normalizations:** Optimized arrival dataclasses and traffic speed normalizations across `app/routers/traffic.py` and `app/services/normalizers/arrivals.py`.

---

## [0.4.9] - 2026-10-18
### Added
- **Official IETT Announcements Endpoint (`GET /v1/announcements/official`):** Scrapes and normalizes official corporate announcements from `iett.istanbul/announcement/Index` with pagination (`page=0..100`), 1-hour in-memory TTL caching, 24-hour stale fallback resilience, URL scheme hardening, and `20/minute` rate limiting.
- **Turkish License Plate Normalization (`app/services/normalizers/plate.py`):** Added canonical `normalize_plate` formatting (`34 HO 1900`) across all SOAP, NTCAPI, ARAC, and SQLite persistence layers while maintaining dash/space-agnostic $O(1)$ lookup in `_plate_fleet`.
- **ARAC Freshness & Cache Metadata:** Added `server_time` (ISO-8601 UTC) and `cached` boolean flags to `/v1/arac/fleet/{kapino}/auto-detail` and `/v1/arac/fleet/{kapino}/detail` responses.
- **Health Endpoint Version Exposure:** Exposed `"version": app.version` in `GET /health` and `GET /healthz`.

### Security & Hardening
- **Global & Endpoint Rate Limiting:** Added a baseline `120/minute` global default rate limit on SlowAPI `Limiter` in addition to strict per-endpoint rate limits (`10/minute` on `/v1/stops/{dcode}/arrivals/raw` and `/v1/arac/fleet/{kapino}/auto-detail`, `20/minute` on `/v1/announcements/official`, `30/minute` on `/v1/arac/suggest` and `/v1/arac/fleet/{kapino}/detail`, `60/minute` on ARAC captcha/session creation).
- **Strict Path & Query Validation:** Enforced regex and length bounds across all route/stop/fleet/batch endpoints (`dcode` `^\d{4,10}$`, `hat_kodu` and `kapino` `^[A-Za-z0-9_ -]+$`, search `q` `max_length=50`, batch `routes` `max_length=500`).
- **Error Shielding & Redaction:** Wrapped `scrape_route_info` and `scrape_stop_info` upstream errors to return clean HTTP `502` responses instead of `500`, and redacted raw background task exception strings from `/health`.
- **Thread-Safe SQLite Lifecycle:** Added double-checked locking (`_lock`) to `vehicle_cache.get_db_connection()`, clean shutdown teardown via `vehicle_cache.close_connection()`, and pre-created `/app/data` with `appuser` ownership in `Dockerfile`.

### Dependencies & Tests
- Bumped `pydantic>=2.13.5`, `pydantic-settings>=2.15.0`, `lxml>=6.1.3`, `ruff>=0.16.6`, and `pyright>=1.1.413`.
- Expanded test suite to **401/401 passing tests** (0 Ruff lint/format issues, 0 Pyright errors).

---

## [0.4.8.1] - 2026-10-11
### Fixed
- **Dynamic ARAC Challenge Hashes:** Removed single-use hash caching in `arac_client.py` and `vehicle_cache.py`. Upstream ARAC challenge hashes expire or fail upon reuse, so fresh hashes are generated per query while durable doorNumber <-> plate mappings remain persistently cached.
- **SQLite Write Deduplication & Async Offloading:** Added in-memory deduplication `_known_plate_mappings` to prevent redundant SQLite writes for recurring fleet vehicles, and offloaded fleet batch writes to worker threads via `run_in_executor` to keep the event loop non-blocking.
- **O(1) `find_bus_in_fleet`:** Replaced full fleet list identity comparison (`list(_fleet.values())`) with direct dictionary lookups.
- **Configurable DB Path:** Added `vehicle_cache_path` to `app/config.py` (default: `data/vehicles.db`, `:memory:` during testing) eliminating environment inspection.
- **Docker Persistence:** Mounted `./data:/app/data` volume in `docker-compose.yml` to preserve the persistent vehicle database across container restarts.

### Tests
- Verified 389/389 tests passing (100% green).

---

## [0.4.8] - 2026-10-11
### Added
- **Persistent SQLite Vehicle & Plate Cache:** Built a thread-safe, WAL-mode persistent SQLite cache (`data/vehicles.db`) caching canonical `clean_kapino <-> clean_plate` pairs and doorNumber/plate mappings, plus upstream ARAC hashes with TTL freshness.
- **Stop & Fleet Plate Enrichment:** Connected `get_plate_by_kapino` directly to the persistent vehicle cache. Buses offline or absent from the live 30s snapshot now reliably resolve license plates on StopPage and Fleet detail endpoints.
- **Smart Dash/Space/Case Agnostic Matching:** Added $O(1)$ lookup via `_clean_fleet` and `_plate_fleet` in `deps.py` and `fleet.py`.
- **Enriched Stop Search:** Added `direction` and `district` properties to `StopSearchResult` models and enriched `/v1/stops/search` responses from the in-memory stop catalog.
- **Cold-Start Stop Search Protection:** Bypassed 300s search caching when stop index is still initializing to prevent cache poisoning with un-enriched records.

### Fixed
- **License Plate Search Flooding:** Fixed plate searches to send full plate strings instead of numeric extraction (avoiding arbitrary "34" query flooding on Istanbul transit).
- **Search Hit Harvesting:** Every upstream query to `GetAllVehicleSelectList` now automatically batch-upserts all 50-100 returned vehicles into the persistent cache.
- **Arac Auto-Detail Captcha Image:** Fixed `captchaImage` key lookup in `arac.py` auto-detail flow.

### Tests
- Expanded test coverage across `vehicle_cache`, `arac_client`, `stops`, and `deps` with 384 passing tests (100% green).

---

## [0.4.6] - 2026-09-12
### Added
- Injected 676767 and 31AMK with extreme edge-case Mock Data (Stale GPS and Math Mismatches) to assist frontend UI testing.
### Fixed
- Preserved missing lat and lon fields in IETT HTML fallback normalizer, allowing fallback positions to successfully reach the client.
- Fixed scrape_route_info to adapt to IETT website DOM and param changes (hkod instead of hatKodu, extracting single string array).

## [0.4.5] - 2026-09-12
- Exclude internal/hidden stops from search results entirely (BUG-01).
- Added RouteInfo and StopInfo endpoints for detailed physical properties (BUG-04).

## [0.4.4] - 2026-09-07

### Dependencies & Maintenance
- **Dependency Upgrades:**
  - Upgraded `fastapi` to `>=0.141.1`.
  - Upgraded `lxml` to `>=6.1.2`.
  - Upgraded `slowapi` to `>=0.1.10`.
  - Upgraded `ruff` dev dependency to `>=0.16.4`.
- **CI/CD Action Updates:**
  - Updated `actions/checkout` to `v7`.
  - Updated `actions/setup-python` to `v7`.
- **Quality & Verification:**
  - Maintained 100% green test suite (370/370 pytest tests passing).
  - 0 Pyright type errors, 0 Ruff lint errors.

---

## [0.4.3] - 2026-08-27

### Added & Improved
- **Backend Test Coverage Expansion:**
  - Added dedicated test suite for `iett_scraper` (timetable HTML scraping, footnote mapping, HTTP and network error paths, 24h caching).
  - Added dedicated test suite for `notice_poller` background worker.
  - Reached 370 passing tests (100% green).
  - Backend test coverage increased to **87%**.
- **Dependencies & CI:**
  - Pinned `aiohttp>=3.10.11,<=3.11.12` for reliable `aioresponses` mock compatibility.
  - Bumped `uvicorn[standard]` to `>=0.52.4`.
  - Updated CI workflow to `actions/setup-python@v5`.

---

## [0.4.2] - 2026-08-27

### Dependencies & Maintenance
- **Dependabot Updates:** Bumped `cachetools` (>=7.1.6), `ddddocr` (>=1.6.1), `aiohttp` (>=3.14.3,<3.15), `actions/setup-python` (v7).
- **App Version Bump:** Updated FastAPI application version to `0.4.2`.
- **Test Suite:** Maintained 100% green test suite (363/363 pytest tests passing).

---

## [0.4.1] - 2026-07-27

### Dependencies & Maintenance
- **Dependabot Resolution:** Bumped and consolidated backend dependencies (`httpx>=0.28.1`, `cryptography>=49.0.0`, `pydantic>=2.13.4`, `pydantic-settings>=2.14.2`, `ruff>=0.16.0`, `pyright>=1.1.411`).
- **Aiohttp & Aioresponses Compatibility:** Pinned `aiohttp>=3.11.0,<3.13` to resolve breaking `ClientResponse` initializer mismatch with `aioresponses`.
- **Code Quality & Linter Compliance:** Configured `pyproject.toml` for `ruff 0.16.0` and `pyright 1.1.411`, fixing all import formatting and typecheck annotations across `app/` and `tests/`.
- **Test Suite:** Maintained 100% green test suite (363/363 pytest tests passing, 0 ruff errors, 0 pyright errors).

---

## [0.4.0] - 2026-07-26

### Added
- **ARAC Portal Overhaul & Auto-Captcha:** 
  - Rewrote `AracClient` to support the new `arac.iett.gov.tr` portal architecture (ASP.NET Captcha + Session cookies).
  - Integrated local CPU OCR inference via `ddddocr` (`opencv-python-headless`) to automatically solve captcha images and return `suggestedAnswer` in `POST /v1/arac/session/captcha`.
  - Added `POST /v1/arac/session/create` endpoint for captcha verification and cookie export.
  - Added unified `GET /v1/arac/fleet/{kapino}/detail` returning vehicle profile and active mission schedule in a single roundtrip.
  - Preserved all backward-compatibility helper functions (`_clip`, `_as_text`, `_to_int`, `_to_float`, `_to_bool`, `_is_html_text`, `_extract_error_message`, `_direction_letter_from_route_code`, `_status_from_arac_error`, `_ms_to_iso`, `_require_arac_session_headers`).
- **Batch Disruption Announcements:** Added `/v1/routes/announcements/batch` endpoint to batch and fetch active announcements concurrently across multiple routes.
- **SQLite & In-Memory Caching:** Upgraded caching layer with SQLite persistent caching, stale-while-revalidate policies, and LazyLock synchronization.
- **Variant Stop & Route Indexing:** Extended in-memory stops indexer to index stops by sub-route variant code.

### Fixed
- **Docker Footprint:** Replaced heavy `opencv-python` GUI dependency with lightweight `opencv-python-headless`.
- **Test Suite Restoration:** Restored and updated full backend test suite to 100% pass rate (361/361 pytest tests passing).
- **Ruff & Typing Compliance:** Resolved all Ruff formatting, linting rules, and strict type annotations across `app/` and `tests/`.

### Dependencies
- Updated `httpx>=0.28.1`, `aiohttp`, `ruff`, `pytest`, `pytest-asyncio`, and `aioresponses`.

---

## [0.3.25] - 2026-05-30

### Fixed
- Fixed memory leak and zombie bus issues in fleet poller by purging stale buses during full snapshots.
- Implemented `SkipCache` handling to prevent caching of invalid or coord-less stops.
- Created robust `sweep_forever` background daemon to continuously garbage collect expired cache entries.
- Fixed time consistency bugs by explicitly using `time.monotonic()` for all TTL operations.
- Corrected typos and mocked data structure errors in test suites.

---

## [0.3.24] - 2026-05-30

### Fixed
- Fixed token expiry logic in ntcapi client to guard against clock skew generating negative token lifespans.
- Fixed uncached IettClient bypass in stops.py arrivals `via` filtering by routing through the cached router endpoint.
- Fixed deadlock in `requestGps` on the frontend where failed `getCurrentPosition` caused permanent lockout.
- Fixed silent exception swallowing on frontend ARAC captcha refresh failure.
- Fixed fetch retry logic in the frontend client to properly recreate a fresh `AbortSignal` for retry execution.
- Fixed focus trap bug in `StopPage.tsx` where tabbing while focus was outside the dialog bypassed the trap.

---

## [0.3.23] - 2026-05-30

### Fixed
- Fixed backend tests failing due to endpoint swap from IETT SOAP to Mobiett JSON endpoints. Mocked `ntcapi.iett.istanbul` properly.

---

## [0.3.18] - 2026-05-30

### Fixed
- Replaced dead IETT SOAP endpoints with official Mobiett JSON endpoints (`ntcapi.iett.istanbul`).
- Search stops, search routes, route schedules, and route live fleets now successfully retrieve data via the new API.
- Re-architected `IettClient` and `MobiettClient` initialization to ensure OAuth2 cache survival and avoid request amplification.
