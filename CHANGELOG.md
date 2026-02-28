# Changelog

All notable changes to iett-middle are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
