# Changelog

All notable changes to iett-middle are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
