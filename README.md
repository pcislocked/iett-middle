# iett-middle

[![Tests](https://img.shields.io/badge/tests-363%20passed-brightgreen)](#testleri-çalıştırma)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Version](https://img.shields.io/badge/version-0.4.1-orange)](./CHANGELOG.md)
[![License](https://img.shields.io/badge/license-CC%20BY%204.0-blue)](https://data.ibb.gov.tr/license)

[🇹🇷 Türkçe (Turkish)](#türkçe) | [🇬🇧 English](#english)

---

## 🇹🇷 Türkçe

`iett-middle`, İstanbul İETT toplu taşıma API'leri, Mobiett mobil altyapısı, `arac.iett.gov.tr` şifreli servisleri ve İBB açık veri kaynakları için geliştirilmiş **akıllı önbelleğe (TTL caching) ve otomatik yedekleme (fallback) mekanizmalarına sahip REST API proxy servisidir.**

[İETT](https://iett.istanbul), İstanbul'un belediye otobüs işletmecisidir. Ham altyapısı SOAP XML, belgelenmemiş HTML kazıma ve resmi Mobiett uygulamasından (`ntcapi.iett.istanbul`) gelen JSON uç noktalarının karmaşık bir bileşimidir. `iett-middle`, tüm bu karmaşık veri kaynaklarını temiz, tip garantili, sürümlendirilmiş REST + JSON formatına dönüştürür ve bellek içi TTL önbellekleme uygulayarak sunucu yükünü ve yanıt sürelerini optimize eder.

Üç depoluk projenin arka yüz (backend) bileşenidir:
[**iett-middle**](https://github.com/pcislocked/iett-middle) (bu depo) ·
[iett-pwa](https://github.com/pcislocked/iett-pwa) (web uygulaması) ·
[iett-hacs](https://github.com/pcislocked/iett-hacs) (Home Assistant entegrasyonu)

---

### 🌟 Öne Çıkan Özellikler

- **⚡ Akıllı Bellek İçi TTL Önbellekleme (In-Memory Cache):** Filo (~7k araç: 15s), Durak Varışları (20s), Sefer Saatleri (1sa), Duyurular (5dk) ve Garajlar (24sa) için optimize edilmiş TTL önbellekleme.
- **🔄 Otomatik Mobiett & SOAP Fallback:** İBB açık verilerindeki SOAP kısıtlamalarını ve veri karartmalarını aşmak için arka planda Mobiett JSON servisleri ile otomatik veri birleştirme (merge).
- **🔒 ARAÇ Oturum & Otomatik Captcha Çözücü (`arac.iett.gov.tr`):** Dahili `ddddocr` OCR modeli ile otomatik captcha yanıtı üretme, istemci bazlı izole oturum yönetimi (`X-Arac-Session-Key`) ve manuel captcha doğrulaması.
- **📍 Ultra Hızlı Yakın Durak İndeksi (Spatial Indexing):** Sunucu başlangıcında yüklenen R-Tree mekansal indeksi sayesinde kullanıcının koordinatına en yakın durakları milisaniyeler içinde hesaplama (`GET /v1/stops/nearby`).
- **🛡️ Güvenlik & Rate Limiting:** SlowAPI ile uç nokta bazlı hız sınırlaması, hata kalkanı (error shielding) ve ASP.NET iç hata sızıntılarını engelleme.
- **📊 Canlı Sistem Durumu & Metrikler:** `/health` uç noktası üzerinden sistem çalışma süresi (uptime) ve bellek önbellek istatistikleri sunumu.

---

### 🚀 Hızlı Başlangıç (Geliştirme)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
# veya: source .venv/bin/activate  (Linux/macOS)

pip install -r requirements.txt
pip install -r requirements-dev.txt

uvicorn app.main:app --reload --port 8000
```

- **Swagger UI API Dokümanı:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Dokümanı:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Sistem Durumu:** [http://localhost:8000/health](http://localhost:8000/health)

---

### ⚙️ Yapılandırma (`.env`)

`.env.example` dosyasını `.env` olarak kopyalayın ve gerektiği gibi düzenleyin:

| Değişken | Örnek | Açıklama |
|---|---|---|
| `IETT_SOAP_BASE` | `https://api.ibb.gov.tr/iett` | İETT SOAP temel URL'si |
| `IETT_REST_BASE` | `https://iett.istanbul` | İETT REST temel URL'si |
| `ARAC_BASE` | `https://arac.iett.gov.tr/api` | ARAÇ şifreli API temel URL'si |
| `TRAFIK_BASE` | `https://trafik.ibb.gov.tr` | İBB trafik API temeli |
| `CACHE_TTL_FLEET` | `15` | Filo önbellek süresi (saniye) |
| `CACHE_TTL_ARRIVALS` | `20` | Varış süreleri önbellek süresi |
| `ENABLE_OUTGOING_TRACE` | `false` | Ayrıntılı giden istek izleme logları |
| `PORT` | `8000` | Dinleme portu |

---

### 📡 Temel API Uç Noktaları

```
GET  /v1/fleet                                 tüm aktif otobüsler (~7k kayıt, 15s önbellek)
GET  /v1/fleet/meta                            hafif filo durumu: otobüs sayısı + son güncelleme zamanı
POST /v1/fleet/refresh                        filo verilerini anında yeniden çekmeyi tetikle
GET  /v1/fleet/{kapino}                        kapı numarasına göre tek otobüsün konumu ve izi
GET  /v1/fleet/{kapino}/detail                 kapı numarasına göre tek otobüs, çözümlenmiş hat kodu ve durak listesi

POST /v1/arac/session/captcha                 captcha doğrulama görseli ve önerilen OCR yanıtı al
POST /v1/arac/session/create                  captcha cevabından ARAÇ oturumu oluştur
GET  /v1/arac/fleet/{kapino}/detail           ARAÇ otobüs detayları, görevleri ve özellikleri (oturum başlıkları gerektirir)

GET  /v1/stops/search?q={name}                 durak arama
GET  /v1/stops/nearby?lat={lat}&lon={lon}      yakındaki duraklar (konum araması)
GET  /v1/stops/{dcode}                         durak adı ve koordinatları
GET  /v1/stops/{dcode}/arrivals                bir duraktaki canlı tahmini varışlar (20s önbellek)
GET  /v1/stops/{dcode}/arrivals?via={dcode2}   dcode2 durağından da geçen otobüslere göre filtrelenmiş varışlar
GET  /v1/stops/{dcode}/announcements           durak bazlı ve hattan gelen aktif duyuruların birleşimi

GET  /v1/routes/search?q={name}                hat arama (örn: 14M)
GET  /v1/routes/{hat_kodu}                     hat varyant ve yön metaverileri
GET  /v1/routes/{hat_kodu}/buses               bir hattaki otobüslerin canlı GPS konumları (15s önbellek)
GET  /v1/routes/{hat_kodu}/stops               koordinatlarla birlikte sıralı durak listesi
GET  /v1/routes/{hat_kodu}/schedule            planlanan kalkış saatleri (1sa önbellek)
GET  /v1/routes/{hat_kodu}/announcements       aktif aksama duyuruları

GET  /v1/announcements/global                  önbelleğe alınmış genel sistem duyuruları
GET  /v1/garages                               tüm İETT otobüs garaj konumları (24sa önbellek)
GET  /v1/traffic/index                         şehir geneli trafik yoğunluk % indeksi (30s önbellek)
GET  /health                                   çalışma süresi + önbellek istatistikleri
```

---

### 🧪 Testleri Çalıştırma

```bash
pytest                                         # Tüm backend testlerini çalıştır (363/363 green)
ruff check                                     # Linter denetimi
pyright                                        # Statik tip denetimi
```

---

### 📦 Docker (Production)

```bash
docker compose build middle
docker compose up -d middle
docker compose logs -f middle
```

---

### ⚖️ Lisans & Legal

Bu proje İstanbul Büyükşehir Belediyesi'nden (İBB) alınan verileri kullanmaktadır.  
[İBB Açık Veri Lisansı](https://data.ibb.gov.tr/license) uyarınca:  
> **Atıf 4.0 Uluslararası (CC BY 4.0) kapsamında lisanslanan kamu sektörü bilgilerini içerir.**

Detaylı KVKK ve veri işleme politikası için:  
[https://pcislocked.net/kvkk/#iett-pwa](https://pcislocked.net/kvkk/#iett-pwa)

---

## 🇬🇧 English

`iett-middle` is a high-performance Python/FastAPI proxy service with smart in-memory TTL caching and automatic fallback mechanisms for Istanbul IETT public transit APIs, Mobiett services, and `arac.iett.gov.tr` APIs.

### Key Features
- **In-Memory TTL Caching:** Optimized caching for fleet positions (15s), ETAs (20s), timetables (1h), and alerts (5m).
- **Mobiett & SOAP Fallback:** Seamlessly merges Mobiett JSON APIs and legacy SOAP data to bypass IBB API restrictions.
- **ARAC Captcha Solver:** Integrated OCR solver (`ddddocr`) for automated captcha handling and per-session credential validation.
- **Spatial Nearby Stops Index:** Fast R-Tree spatial indexing for instant nearby stop lookup (`GET /v1/stops/nearby`).
- **Security & Error Shielding:** Rate-limiting via SlowAPI, sanitizing raw ASP.NET HTML stack traces.

### Quick Start
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### Docs & Health
- **Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Uptime Health:** [http://localhost:8000/health](http://localhost:8000/health)
