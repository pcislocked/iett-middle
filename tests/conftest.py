"""Shared test fixtures and real captured API payloads."""
from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from typing import Any

# Windows: aiohttp needs SelectorEventLoop
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# ---------------------------------------------------------------------------
# Real captured SOAP XML responses (from live probe 2026-02-27)
# ---------------------------------------------------------------------------

FLEET_ALL_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Body>
    <GetFiloAracKonum_jsonResponse xmlns="http://tempuri.org/">
      <GetFiloAracKonum_jsonResult>[{"Operator":"İstanbul Halk Ulaşım Tic.A.Ş","Garaj":null,"KapiNo":"A-001","Saat":"00:19:57","Boylam":"29.0155733333333","Enlem":"41.1073516666667","Hiz":"0","Plaka":"34 HO 1000"}]</GetFiloAracKonum_jsonResult>
    </GetFiloAracKonum_jsonResponse>
  </soap:Body>
</soap:Envelope>"""

ROUTE_FLEET_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Body>
    <GetHatOtoKonum_jsonResponse xmlns="http://tempuri.org/">
      <GetHatOtoKonum_jsonResult>[{"kapino":"C-325","boylam":"29.0109726666667","enlem":"41.0819041666667","hatkodu":"500T","guzergahkodu":"500T_D_D0","hatad":"TUZLA ŞİFA MAHALLESİ - 4. LEVENT METRO","yon":"ŞİFA SONDURAK","son_konum_zamani":"2026-02-27 00:05:54","yakinDurakKodu":"113333"}]</GetHatOtoKonum_jsonResult>
    </GetHatOtoKonum_jsonResponse>
  </soap:Body>
</soap:Envelope>"""

ROUTE_FLEET_EMPTY_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Body>
    <GetHatOtoKonum_jsonResponse xmlns="http://tempuri.org/">
      <GetHatOtoKonum_jsonResult>[]</GetHatOtoKonum_jsonResult>
    </GetHatOtoKonum_jsonResponse>
  </soap:Body>
</soap:Envelope>"""

ARRIVALS_HTML = """\
<div class="line-list">
  <div class="line-item">
    <div class="content content-header">
      <p>Duraktan Geçen Otobüsler <small>Varış Süresi</small></p>
    </div>
  </div>
  <div class="line-item">
    <div class="content">
      <span>500T</span>
      <p>4.LEVENT METRO - ŞİFA SONDURAK <b>(00:10) 4 dk</b></p>
    </div>
  </div>
  <div class="line-item">
    <div class="content">
      <span>14M</span>
      <p>YENİ CAMİİ <b>(00:25) 12 dk</b></p>
    </div>
  </div>
</div>"""

SCHEDULE_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Body>
    <GetPlanlananSeferSaati_jsonResponse xmlns="http://tempuri.org/">
      <GetPlanlananSeferSaati_jsonResult>[{"SHATKODU":"500T","HATADI":"TUZLA ŞİFA MAHALLESİ - CEVİZLİBAĞ","SGUZERAH":"500T_D_D0","SYON":"D","SGUNTIPI":"H","GUZERGAH_ISARETI":null,"SSERVISTIPI":"ÖHO","DT":"05:55"}]</GetPlanlananSeferSaati_jsonResult>
    </GetPlanlananSeferSaati_jsonResponse>
  </soap:Body>
</soap:Envelope>"""

ANNOUNCEMENTS_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Body>
    <GetDuyurular_jsonResponse xmlns="http://tempuri.org/">
      <GetDuyurular_jsonResult>[{"HATKODU":"500T","HAT":"TUZLA ŞİFA MAHALLESİ - 4. LEVENT METRO","TIP":"Günlük","GUNCELLEME_SAATI":"Kayit Saati: 09:00","MESAJ":"YOĞUN TRAFİK NEDENİYLE GÜZERGAH DEĞİŞİKLİĞİ UYGULANMAKTADIR."}]</GetDuyurular_jsonResult>
    </GetDuyurular_jsonResponse>
  </soap:Body>
</soap:Envelope>"""

ROUTE_STOPS_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Body>
    <DurakDetay_GYYResponse xmlns="http://tempuri.org/">
      <DurakDetay_GYYResult>
        <NewDataSet>
          <Table>
            <HATKODU>500T</HATKODU>
            <YON>D</YON>
            <SIRANO>1</SIRANO>
            <DURAKKODU>301341</DURAKKODU>
            <DURAKADI>4.LEVENT METRO</DURAKADI>
            <XKOORDINATI>29.007309</XKOORDINATI>
            <YKOORDINATI>41.084170</YKOORDINATI>
            <DURAKTIPI>CCMODERN</DURAKTIPI>
            <ISLETMEBOLGE>Avrupa3</ISLETMEBOLGE>
            <ISLETMEALTBOLGE>Şişli</ISLETMEALTBOLGE>
            <ILCEADI>Sisli</ILCEADI>
          </Table>
        </NewDataSet>
      </DurakDetay_GYYResult>
    </DurakDetay_GYYResponse>
  </soap:Body>
</soap:Envelope>"""

ROUTES_BY_STATION_HTML = """\
<div class="line-list">
  <div class="line-item">
    <a href="/tr/RouteDetail/14M"><span>14M</span><span>KADIKÖY - YENİ CAMİİ</span></a>
  </div>
  <div class="line-item">
    <a href="/tr/RouteDetail/15TY"><span>15TY</span><span>HEKİMBAŞI - TOKATKOY</span></a>
  </div>
</div>"""

SEARCH_JSON: dict[str, list[dict[str, Any]]] = {
    "list": [
        {
            "Path": "/StationDetail?dkod=220602&stationname=ahmet-mithat",
            "Code": "<img>",
            "Name": "AHMET MİTHAT EFENDİ - Beykoz - TEKKE MEVKİİ Yönü",
            "Location": None,
            "Stationcode": 220602,
        },
        {
            "Path": "/RouteDetail/14M",
            "Code": "<img>",
            "Name": "14M",
            "Location": None,
            "Stationcode": 0,
        },
    ]
}

ROUTE_SEARCH_JSON: dict[str, list[dict[str, Any]]] = {
    "list": [
        {
            "Path": "/RouteDetail?hkod=500T",
            "Code": "500T",
            "Name": "TUZLA ŞİFA MAHALLESİ - 4. LEVENT METRO",
            "Location": None,
            "Stationcode": 0,
        },
        {
            "Path": "/StationDetail?dkod=220602",
            "Code": "<img>",
            "Name": "SOME STOP",
            "Location": None,
            "Stationcode": 220602,
        },
    ]
}

ROUTE_METADATA_JSON: list[dict[str, Any]] = [
    {
        "GUZERGAH_ADI": "TUZLA ŞİFA MAHALLESİ - 4. LEVENT METRO",
        "GUZERGAH_GUZERGAH_KODU": "500T_D_D0",
        "GUZERGAH_YON": 0,
        "GUZERGAH_DEPAR_NO": 1,
        "GUZERGAH_GUZERGAH_ADI": "4. LEVENT METRO YÖNÜ",
        "HAT_HAT_ADI": None,
        "HAT_HAT_KODU": None,
    },
    {
        "GUZERGAH_ADI": "TUZLA ŞİFA MAHALLESİ - 4. LEVENT METRO",
        "GUZERGAH_GUZERGAH_KODU": "500T_G_G0",
        "GUZERGAH_YON": 1,
        "GUZERGAH_DEPAR_NO": 2,
        "GUZERGAH_GUZERGAH_ADI": "TUZLA YÖNÜ",
        "HAT_HAT_ADI": None,
        "HAT_HAT_KODU": None,
    },
]

GARAGE_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetGaraj_jsonResponse xmlns="http://tempuri.org/">
      <GetGaraj_jsonResult>[{"GarajAdi":"IKITELLI GARAJ","GarajKodu":"IKT","KoordinatX":"28.7980","KoordinatY":"41.0620"},{"GarajAdi":"ANADOLU GARAJ","GarajKodu":"AND","KoordinatX":"29.1500","KoordinatY":"40.9800"}]</GetGaraj_jsonResult>
    </GetGaraj_jsonResponse>
  </soap:Body>
</soap:Envelope>"""

STOP_DETAIL_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetDurak_jsonResponse xmlns="http://tempuri.org/">
      <GetDurak_jsonResult>[{"SDURAKKODU":220602,"SDURAKADI":"AHMET MİTHAT EFENDİ","KOORDINAT":"POINT (29.0871 41.1234)","ILCEADI":"Beykoz"}]</GetDurak_jsonResult>
    </GetDurak_jsonResponse>
  </soap:Body>
</soap:Envelope>"""

ALL_STOPS_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetDurak_jsonResponse xmlns="http://tempuri.org/">
      <GetDurak_jsonResult>[{"SDURAKKODU":100022,"SDURAKADI":"OKTAY RIFAT CADDES\u0130","KOORDINAT":"POINT (28.6952 41.0046)","ILCEADI":"Esenyurt","SYON":"BEYLIKD\u00dcZ\u00dc"},{"SDURAKKODU":100151,"SDURAKADI":"MENEK\u015eE","KOORDINAT":"POINT (28.6599 40.9783)","ILCEADI":"Beylikd\u00fcz\u00fc","SYON":"AVCILAR"},{"SDURAKKODU":301341,"SDURAKADI":"4.LEVENT METRO","KOORDINAT":"POINT (29.0073 41.0842)","ILCEADI":"Sisli","SYON":null}]</GetDurak_jsonResult>
    </GetDurak_jsonResponse>
  </soap:Body>
</soap:Envelope>"""
