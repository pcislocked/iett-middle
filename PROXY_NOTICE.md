# IETT Upstream Proxy Notice / Proxy Trafiği Bilgilendirmesi

[🇹🇷 Türkçe](#türkçe) | [🇬🇧 English](#english)

---

## 🇹🇷 Türkçe

Bu bilgilendirme metni, İBB / İETT sunucularına (`api.ibb.gov.tr`) `iett-middle` servisinden veya yetkili proxy ağ geçitlerinden yönlendirilen ağ istekleri (`User-Agent: iett-middle/... (via proxy)`) için hazırlanmıştır.

### 1. Bu İstekler Nedir?
`iett-middle`, İstanbul Büyükşehir Belediyesi'nin [Açık Veri Portalı](https://data.ibb.gov.tr/) lisansı (CC-BY 4.0) kapsamında geliştirilen açık kaynaklı, kar amacı gütmeyen sivil bir toplu taşıma API proxy'si ve önbellekleme servisidir. [İETT PWA](https://github.com/pcislocked/iett-pwa) ve [Home Assistant](https://github.com/pcislocked/iett-hacs) kullanıcılarına anlık otobüs ve hat bilgilerini optimize ederek ulaştırır.

### 2. Neden Proxy / Konut Ağ Geçidi Kullanılıyor?
Bulut veri merkezlerinden (örn. Avrupa sunucuları) belediye SOAP servislerine yapılan doğrudan bağlantılarda zaman zaman yaşanan yurt dışı ağ tıkanıklıkları, coğrafi paket düşüşleri veya rota engellemeleri durumunda; hizmetin kesintiye uğramaması adına **yalnızca doğrudan bağlantı başarısız olduğunda** ikincil Türkiye konut ağ geçitleri (proxy) yedek hat olarak devreye girer.

### 3. Sunucu Yükünü Önleme & Katı Hız Sınırlamaları (Rate Limiting)
İBB altyapısını ve aracı ağ geçitlerini korumak için sistemimiz son derece korumacı güvenlik kurallarıyla çalışır:
* **Doğrudan Hat Önceliği:** Doğrudan bağlantı düzenli aralıklarla test edilir; doğrudan erişim mümkün olduğu anda proxy kullanımı derhal durdurulur.
* **Sıkı Aralık Kısıtı:** Proxy başına yapılan ardışık istekler arasında **en az 2 saniye** zorunlu bekleme uygulanır.
* **Pencere İstek Tavanı:** Her bir proxy ağ geçidi üzerinden 2 dakikada **en fazla 45 istek** gönderilir; kota dolduğunda istekler kuyruğa alınmaz, reddedilir.
* **Agresif Önbellekleme:** Tüm filo, durak ve varış verileri bellek içinde 15 saniyeden 24 saate kadar önbelleğe alınarak İBB sunucularına gereksiz mükerrer yük binmesi engellenir.

### 4. İletişim & Koordinasyon
Trafik akışımızla ilgili herhangi bir teknik öneri, soru veya düzenleme talebiniz için lütfen GitHub üzerinden bizimle iletişime geçin:
* **Depo:** [https://github.com/pcislocked/iett-middle](https://github.com/pcislocked/iett-middle)
* **İletişim:** [https://github.com/pcislocked](https://github.com/pcislocked) / GitHub Issues

---

## 🇬🇧 English

This notice clarifies incoming requests directed to IBB / IETT servers (`api.ibb.gov.tr`) originating from `iett-middle` or its authorized proxy relays (`User-Agent: iett-middle/... (via proxy)`).

### 1. What is this service?
`iett-middle` is an open-source, non-commercial public transit API cache and proxy service developed under the Istanbul Metropolitan Municipality [Open Data License](https://data.ibb.gov.tr/license) (CC-BY 4.0). It powers the open-source [IETT PWA](https://github.com/pcislocked/iett-pwa) and [Home Assistant](https://github.com/pcislocked/iett-hacs) integrations for Istanbul commuters.

### 2. Why are requests routed via proxy?
Due to transient inter-datacenter routing issues, European cloud packet drops, or upstream geo-filtering affecting `api.ibb.gov.tr`, fallback Turkish residential relays (proxies) are utilized **strictly when direct connections fail**, ensuring high availability for public transit riders.

### 3. Server Protection & Strict Rate Limiting
To ensure zero operational impact on IBB infrastructure:
* **Direct-First Priority:** Direct connections are probed periodically; proxy routing is immediately vacated the moment direct connectivity is healthy.
* **Mandatory Spacing:** A mandatory minimum **2.0-second cooldown** is enforced between consecutive requests per proxy.
* **Window Request Cap:** Maximum **45 requests per 2-minute rolling window** are allowed per residential proxy gateway.
* **Aggressive In-Memory Caching:** Heavy TTL caching (15 seconds to 24 hours) shields upstream municipal servers from redundant queries.

### 4. Contact & Inquiries
For operational inquiries, technical coordination, or traffic adjustments:
* **Repository:** [https://github.com/pcislocked/iett-middle](https://github.com/pcislocked/iett-middle)
* **Maintainer:** [https://github.com/pcislocked](https://github.com/pcislocked)
