# iett-middle

[![Tests](https://img.shields.io/badge/tests-373%20passed-brightgreen)](#running-tests)
[![Coverage](https://img.shields.io/badge/coverage-report%20in%20CI-informational)](#running-tests)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Version](https://img.shields.io/badge/version-0.3.17-orange)](./CHANGELOG.md)

[🇹🇷 Türkçe (Turkish)](#türkçe) | [🇬🇧 English](#english)

---

## 🇹🇷 Türkçe

İstanbul İETT toplu taşıma API'leri için akıllı önbelleğe sahip (caching) proxy servisi.

[İETT](https://iett.istanbul), İstanbul'un belediye otobüs işletmecisidir. Ham API'leri SOAP, belgelenmemiş HTML ve artık **resmi Mobiett uygulamasından (`ntcapi.iett.istanbul`) gelen JSON uç noktalarının** bir karışımıdır.
Bu servis, tüm bunları temiz, sürümlendirilmiş REST + JSON formatına dönüştürür ve bellek içi TTL önbelleğe alma işlemi uygular.
İBB'nin son açık API kısıtlamalarını (halka açık verilerin kapatılması) aşmak için aktif olarak Mobiett JSON API'sine geri dönüş (fallback) yapar.

Üç depoluk bir projenin parçasıdır:
[**iett-middle**](https://github.com/pcislocked/iett-middle) (bu depo) ·
[iett-hacs](https://github.com/pcislocked/iett-hacs) (Home Assistant entegrasyonu) ·
[iett-pwa](https://github.com/pcislocked/iett-pwa) (web uygulaması)

### Hızlı Başlangıç (Geliştirme)

```bash
cd iett-middle
python -m venv .venv
.venv\Scripts\activate        # Windows
# veya: source .venv/bin/activate  (Linux/macOS)

pip install -r requirements.txt
pip install -r requirements-dev.txt

uvicorn app.main:app --reload --port 8000
```

API Dökümanı → http://localhost:8000/docs  
Sistem Durumu → http://localhost:8000/health

### Yapılandırma

`.env.example` dosyasını `.env` olarak kopyalayın ve gerektiği gibi düzenleyin:

| Değişken | Örnek (.env.example) | Açıklama |
|---|---|---|
| `IETT_SOAP_BASE` | `https://api.ibb.gov.tr/iett` | İETT SOAP temel URL'si |
| `IETT_REST_BASE` | `https://iett.istanbul` | İETT REST temel URL'si |
| `ARAC_BASE` | `https://arac.iett.gov.tr/api` | ARAÇ şifreli API temel URL'si |
| `TRAFIK_BASE` | `https://trafik.ibb.gov.tr` | İBB trafik API temeli |
| `OSRM_BASE` | `https://router.project-osrm.org` | OSRM rota sunucusu |
| `CACHE_TTL_FLEET` | `15` | Filo önbellek süresi (saniye) |
| `CACHE_TTL_ARRIVALS` | `20` | Varış süreleri önbellek süresi |
| `FLEET_CACHE_MAX_AGE` | `900` | Filo önbelleğini 15 dakikada bir yenilemeye zorlar |
| `FLEET_MANUAL_REFRESH_COOLDOWN` | `10` | Elle yenileme çağrıları arasındaki minimum saniye |
| `ENABLE_OUTGOING_TRACE` | `false` | Ayrıntılı aiohttp izleme loglarını etkinleştirir |
| `PORT` | `8000` | Dinleme portu |

### API Uç Noktaları

```
GET /v1/fleet                                 tüm aktif otobüsler (~7k kayıt, 15s önbellek)
GET /v1/fleet/{kapino}                        kapı numarasına göre tek bir otobüs

POST /v1/arac/session/captcha                 captcha doğrulama görselini al
POST /v1/arac/session/getpicture              captcha görseli almak için alias
POST /v1/arac/session/create                  captcha cevabından ARAÇ oturumu oluştur
POST /v1/arac/session/response                captcha cevabını göndermek için alias
GET /v1/arac/fleet                            ARAÇ filo anlık durumu (oturum başlıkları gerektirir)
GET /v1/arac/fleet/{kapino}                   ARAÇ tek otobüs profili (oturum başlıkları gerektirir)
GET /v1/arac/fleet/{kapino}/missions          ARAÇ görev zaman çizelgesi (oturum başlıkları gerektirir)
GET /v1/arac/routes/{route_id}/stops          ARAÇ hat durakları (oturum başlıkları gerektirir)

GET /v1/stops/search?q={name}                 durak arama
GET /v1/stops/{dcode}/arrivals                bir duraktaki canlı tahmini varışlar (20s önbellek)
GET /v1/stops/{dcode}/arrivals?via={dcode2}   dcode2 durağından da geçen otobüslere göre filtrelenmiş varışlar
GET /v1/stops/{dcode}/routes                  bir duraktan geçen tüm hat kodları

GET /v1/routes/search?q={name}                hat arama (örn: 14M)
GET /v1/routes/{hat_kodu}/buses               bir hattaki otobüslerin canlı GPS konumları (15s önbellek)
GET /v1/routes/{hat_kodu}/stops               koordinatlarla birlikte sıralı durak listesi (24s önbellek)
GET /v1/routes/{hat_kodu}/schedule            planlanan kalkış saatleri (1s önbellek)
GET /v1/routes/{hat_kodu}/announcements       aktif aksama uyarıları (5d önbellek)

GET /v1/traffic/index                         şehir geneli % yoğunluk (30s önbellek)
GET /v1/traffic/segments                      yol segmenti hızları (30s önbellek)

GET /health                                   çalışma süresi + önbellek istatistikleri
GET /docs                                     Swagger UI
```

Not: middle, ARAC sessionId/sessionKey bilgilerini kalıcı olarak saklamaz. İstemciler kendi oturum kimlik bilgilerini tutar ve her veri isteğinde iletir.

### Testleri Çalıştırma

```bash
pip install -r requirements-dev.txt
pytest
```

### Docker (Prod)

```bash
# Repo kökünden (docker-compose.yml içerir)
docker compose build middle
docker compose up -d middle

# Loglar
docker compose logs -f middle
```

### Bilinen Sorunlar

- `GetFiloAracKonum_json` (tüm filo) BÜYÜK HARFLİ alan adları kullanır; `GetHatOtoKonum_json` (hat filosu) küçük harf kullanır. Her ikisi de aynı `BusPosition` modeline normalize edilir.
- `GetStationInfo` JSON değil, HTML döndürür. BeautifulSoup ile ayrıştırılır.
- `DurakDetay_GYY`: `XKOORDINATI` = **boylam (longitude)**, `YKOORDINATI` = **enlem (latitude)** (kafa karıştırıcı bir şekilde ters çevrilmiş).
- **Mobiett API Fallback**: İBB'nin halka açık SOAP erişimini kapatması nedeniyle, arka uç büyük ölçüde `ntcapi.iett.istanbul`'a dayanır ve `MobiettClient` üzerinden verileri birleştirir.

### Lisans & Hukuki

Bu proje İstanbul Büyükşehir Belediyesi'nden (İBB) alınan verileri kullanmaktadır.
[İBB Açık Veri Lisansı](https://data.ibb.gov.tr/license) uyarınca aşağıdaki atıf yapılmaktadır:
> **Atıf 4.0 Uluslararası (CC BY 4.0) kapsamında lisanslanan kamu sektörü bilgilerini içerir.**

İBB'nin son dönemde uygulamaya koyduğu kamu verisi karartmasını aşmak ve kamuya ait bu verileri halka sunabilmek için erişilebilir her türlü yöntemle (legal/illegal) veri çekmeye devam edeceğiz.

---

## 🇬🇧 English

Smart caching proxy for Istanbul IETT public transit APIs.

[IETT](https://iett.istanbul) is Istanbul's municipal bus operator. Their raw APIs are a mix of
SOAP, undocumented HTML, and now **JSON endpoints from the official Mobiett app** (`ntcapi.iett.istanbul`). 
This service normalises all of them into clean, versioned REST + JSON with in-memory TTL caching. 
It actively falls back to the Mobiett JSON API to bypass IBB's recent public API restrictions.

Part of a three-repo stack:
[**iett-middle**](https://github.com/pcislocked/iett-middle) (this repo) ·
[iett-hacs](https://github.com/pcislocked/iett-hacs) (Home Assistant integration) ·
[iett-pwa](https://github.com/pcislocked/iett-pwa) (web app)

### Quick start (development)

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

### Configuration

Copy `.env.example` to `.env` and edit as needed:

| Variable | Example (.env.example) | Description |
|---|---|---|
| `IETT_SOAP_BASE` | `https://api.ibb.gov.tr/iett` | IETT SOAP base URL |
| `IETT_REST_BASE` | `https://iett.istanbul` | IETT REST base URL |
| `ARAC_BASE` | `https://arac.iett.gov.tr/api` | ARAC encrypted API base URL |
| `TRAFIK_BASE` | `https://trafik.ibb.gov.tr` | IBB traffic API base |
| `OSRM_BASE` | `https://router.project-osrm.org` | OSRM routing server |
| `CACHE_TTL_FLEET` | `15` | Fleet cache TTL (seconds) |
| `CACHE_TTL_ARRIVALS` | `20` | Arrivals cache TTL |
| `FLEET_CACHE_MAX_AGE` | `900` | Force fleet cache refresh every 15 min (prevents 6h+ stale FILO data) |
| `FLEET_MANUAL_REFRESH_COOLDOWN` | `10` | Minimum seconds between accepted `POST /v1/fleet/refresh` calls |
| `ENABLE_OUTGOING_TRACE` | `false` | Enable verbose per-request outgoing aiohttp trace logs |
| `PORT` | `8000` | Listen port |

### API endpoints

```
GET /v1/fleet                                 all active buses (~7k records, cached 15s)
GET /v1/fleet/{kapino}                        single bus by door number

POST /v1/arac/session/captcha                 fetch captcha challenge image
POST /v1/arac/session/getpicture              alias for captcha challenge fetch
POST /v1/arac/session/create                  create ARAC session from captcha answer
POST /v1/arac/session/response                alias for captcha answer submit
GET /v1/arac/fleet                            ARAC fleet snapshot (requires session headers)
GET /v1/arac/fleet/{kapino}                   ARAC single bus profile (requires session headers)
GET /v1/arac/fleet/{kapino}/missions          ARAC mission timeline (requires session headers)
GET /v1/arac/routes/{route_id}/stops          ARAC route stops (requires session headers)

GET /v1/stops/search?q={name}                 stop search
GET /v1/stops/{dcode}/arrivals                live ETAs at a stop (cached 20s)
GET /v1/stops/{dcode}/arrivals?via={dcode2}   ETAs filtered to buses also passing dcode2
GET /v1/stops/{dcode}/routes                  all route codes through a stop

GET /v1/routes/search?q={name}                route search (e.g. 14M)
GET /v1/routes/{hat_kodu}/buses               live GPS of buses on a route (cached 15s)
GET /v1/routes/{hat_kodu}/stops               ordered stop list with coords (cached 24h)
GET /v1/routes/{hat_kodu}/schedule            planned departures (cached 1h)
GET /v1/routes/{hat_kodu}/announcements       active disruption alerts (cached 5m)

GET /v1/traffic/index                         city-wide % congestion (cached 30s)
GET /v1/traffic/segments                      per-road segment speeds (cached 30s)

GET /health                                   uptime + cache stats
GET /docs                                     Swagger UI
```

Note: middle does not persist ARAC sessionId/sessionKey. Clients keep their own
session credentials and pass them on each ARAC data request.

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

### Docker (production)

Point at a remote Docker host or run locally:

- GHCR published image targets linux/amd64 and linux/arm64.

```bash
# From repo root (contains docker-compose.yml)
docker compose build middle
docker compose up -d middle

# Logs
docker compose logs -f middle
```

### Known quirks

- `GetFiloAracKonum_json` (all-fleet) uses CAPITALISED field names; `GetHatOtoKonum_json` (route-fleet) uses lowercase. Both are normalised to the same `BusPosition` model.
- `GetStationInfo` returns HTML, not JSON. Parsed with BeautifulSoup.
- `DurakDetay_GYY`: `XKOORDINATI` = **longitude**, `YKOORDINATI` = **latitude** (confusingly swapped).
- **Mobiett API Fallback**: Due to IBB blocking public SOAP access, the backend heavily relies on `ntcapi.iett.istanbul` and merges data via `MobiettClient`.

### License & Legal

This project uses data sourced from the Istanbul Metropolitan Municipality (IBB). 
In compliance with the [IBB Open Data License](https://data.ibb.gov.tr/license), the following attribution is made:
> **Atıf 4.0 Uluslararası (CC BY 4.0) kapsamında lisanslanan kamu sektörü bilgilerini içerir.**

We will continue to pull as much data as possible through any accessible means to bypass the recent public data blackout.
