# iett-middle

[![Tests](https://img.shields.io/badge/tests-108%20passed-brightgreen)](#running-tests)
[![Coverage](https://img.shields.io/badge/coverage-73%25-yellow)](#running-tests)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Version](https://img.shields.io/badge/version-0.1-orange)](https://github.com/pcislocked/iett-middle/releases/tag/v0.1)

Smart caching proxy for Istanbul IETT public transit APIs.

[IETT](https://iett.istanbul) is Istanbul's municipal bus operator. Their raw APIs are a mix of
SOAP, undocumented HTML, and JSON endpoints — this service normalises all of them into clean,
versioned REST + JSON with in-memory TTL caching and optional [OSRM](http://project-osrm.org/)
route enrichment.

Part of a three-repo stack:
[**iett-middle**](https://github.com/pcislocked/iett-middle) (this repo) ·
[iett-hacs](https://github.com/pcislocked/iett-hacs) (Home Assistant integration) ·
[iett-pwa](https://github.com/pcislocked/iett-pwa) (web app)

## Quick start (development)

```bash
cd iett-middle
python -m venv .venv
.venv\Scripts\activate        # Windows
# or: source .venv/bin/activate  (Linux/macOS)

pip install -r requirements.txt
pip install -r requirements-dev.txt

uvicorn app.main:app --reload --port 8000
```

API docs → http://localhost:8000/docs  
Health    → http://localhost:8000/health

## Configuration

Copy `.env.example` to `.env` and edit as needed:

| Variable | Default | Description |
|---|---|---|
| `IETT_SOAP_BASE` | `https://api.ibb.gov.tr/iett` | IETT SOAP base URL |
| `IETT_REST_BASE` | `https://iett.istanbul` | IETT REST base URL |
| `TRAFIK_BASE` | `https://trafik.ibb.gov.tr` | IBB traffic API base |
| `OSRM_BASE` | `https://router.project-osrm.org` | OSRM routing server |
| `CACHE_TTL_FLEET` | `15` | Fleet cache TTL (seconds) |
| `CACHE_TTL_ARRIVALS` | `20` | Arrivals cache TTL |
| `PORT` | `8000` | Listen port |

## API endpoints

```
GET /v1/fleet                                 all active buses (~7k records, cached 15s)
GET /v1/fleet/{kapino}                        single bus by door number

GET /v1/stops/search?q={name}                 stop search
GET /v1/stops/{dcode}/arrivals                live ETAs at a stop (cached 20s)
GET /v1/stops/{dcode}/arrivals?via={dcode2}   ETAs filtered to buses also passing dcode2
GET /v1/stops/{dcode}/routes                  all route codes through a stop

GET /v1/routes/{hat_kodu}/buses               live GPS of buses on a route (cached 15s)
GET /v1/routes/{hat_kodu}/stops               ordered stop list with coords (cached 24h)
GET /v1/routes/{hat_kodu}/schedule            planned departures (cached 1h)
GET /v1/routes/{hat_kodu}/announcements       active disruption alerts (cached 5m)

GET /v1/traffic/index                         city-wide % congestion (cached 30s)
GET /v1/traffic/segments                      per-road segment speeds (cached 30s)

GET /health                                   uptime + cache stats
GET /docs                                     Swagger UI
```

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Docker (production)

Point at a remote Docker host or run locally:

```bash
# From repo root (contains docker-compose.yml)
docker compose build middle
docker compose up -d middle

# Logs
docker compose logs -f middle
```

## Known quirks

- `GetFiloAracKonum_json` (all-fleet) uses CAPITALISED field names; `GetHatOtoKonum_json` (route-fleet) uses lowercase. Both are normalised to the same `BusPosition` model.
- `GetStationInfo` returns HTML, not JSON. Parsed with BeautifulSoup.
- `DurakDetay_GYY`: `XKOORDINATI` = **longitude**, `YKOORDINATI` = **latitude** (confusingly swapped).
- OSRM enrichment is on-demand only (nearest bus per route) to avoid public rate limits.
