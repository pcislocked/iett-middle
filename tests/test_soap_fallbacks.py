"""Tests for SOAP metadata fallbacks, DurakDetay_GYY wiring, and stop_cache physical metadata."""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses
from fastapi.testclient import TestClient

from app.deps import clear_stop_store, record_stops_batch
from app.main import app
from app.models.stop import RouteStop
from app.services import stop_cache
from app.services.iett_client import IettApiError, IettClient
from app.services.iett_parser import (
    parse_all_stops_json,
    parse_route_info_soap_json,
    parse_stop_info_soap_json,
)

GET_HAT_URL = "https://api.ibb.gov.tr/iett/UlasimAnaVeri/HatDurakGuzergah.asmx"
IBB_GYY_URL = "https://api.ibb.gov.tr/iett/ibb/ibb.asmx"
FLEET_URL = "https://api.ibb.gov.tr/iett/FiloDurum/SeferGerceklesme.asmx"


def _soap_envelope(tag: str, inner_json: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<soap:Body><{tag}Response xmlns="http://tempuri.org/">'
        f"<{tag}Result>{inner_json}</{tag}Result>"
        f"</{tag}Response></soap:Body></soap:Envelope>"
    )


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    from app.services.cache import _store

    _store.clear()
    db_file = str(tmp_path / "fallback_stops.db")
    monkeypatch.setattr(stop_cache.settings, "stop_cache_path", db_file)
    stop_cache.close_connection()
    clear_stop_store()
    yield
    _store.clear()
    stop_cache.close_connection()
    clear_stop_store()


class TestSoapMetadataParsers:
    def test_parse_stop_info_soap_json(self) -> None:
        payload = json.dumps(
            [
                {
                    "SDURAKKODU": 260211,
                    "SDURAKADI": "KAVACIK",
                    "KOORDINAT": "POINT (29.087292 41.088704)",
                    "ILCEADI": "Beykoz",
                    "AKILLI": "VAR",
                    "FIZIKI": "FULL KAPALI",
                    "DURAK_TIPI": "İETTCAMLI",
                    "ENGELLIKULLANIM": "Uygun",
                }
            ]
        )
        xml = _soap_envelope("GetDurak_json", payload)
        info = parse_stop_info_soap_json(xml, "260211")
        assert info is not None
        assert info["ilce"] == "Beykoz"
        assert info["fiziki_durum"] == "FULL KAPALI"
        assert info["akilli_durak"] is True
        assert info["durak_tipi"] == "İETTCAMLI"

    def test_parse_all_stops_json_keeps_model_dump_clean_and_sets_meta(self) -> None:
        payload = json.dumps(
            [
                {
                    "SDURAKKODU": 260211,
                    "SDURAKADI": "KAVACIK",
                    "KOORDINAT": "POINT (29.087292 41.088704)",
                    "ILCEADI": "Beykoz",
                    "SYON": "KAVACIK",
                    "AKILLI": "YOK",
                    "FIZIKI": "FULL KAPALI",
                    "DURAK_TIPI": "İETTCAMLI",
                    "ENGELLIKULLANIM": "Uygun Degil",
                }
            ]
        )
        stops = parse_all_stops_json(_soap_envelope("GetDurak_json", payload))
        assert len(stops) == 1
        dumped = stops[0].model_dump()
        assert "fiziki_durum" not in dumped
        assert stops[0]._stop_meta is not None
        assert stops[0]._stop_meta["akilli_durak"] is False
        assert stops[0]._stop_meta["fiziki_durum"] == "FULL KAPALI"

    def test_parse_route_info_soap_json(self) -> None:
        payload = json.dumps(
            [
                {
                    "SHATKODU": "500T",
                    "SHATADI": "TUZLA ŞİFA MAHALLESİ - 4. LEVENT METRO",
                    "TARIFE": "2 BİLETLİ",
                    "HAT_UZUNLUGU": 56.7456,
                    "SEFER_SURESI": 272.93,
                }
            ]
        )
        info = parse_route_info_soap_json(_soap_envelope("GetHat_json", payload), "500T")
        assert info is not None
        assert info["hat_kodu"] == "500T"
        assert info["tarife"] == "2 BİLETLİ"
        assert info["trip_duration_min"] == 136
        assert info["hat_tipi"] is None
        assert info["details"] == ["Hat Uzunluğu: 56.75 km"]


class TestStopCachePhysicalMigration:
    def test_migrates_legacy_stop_cache_table_and_persists_physical_info(self, tmp_path) -> None:
        legacy_db = tmp_path / "legacy_stops.db"
        conn = sqlite3.connect(legacy_db)
        conn.execute(
            """
            CREATE TABLE stop_cache (
                stop_code TEXT PRIMARY KEY,
                stop_name TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                district TEXT,
                direction TEXT,
                updated_at REAL NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO stop_cache VALUES ('260211', 'KAVACIK', 41.088, 29.087, 'Beykoz', 'KAVACIK', 1.0);"
        )
        conn.commit()
        conn.close()

        stop_cache.close_connection()
        stop_cache.settings.stop_cache_path = str(legacy_db)
        stop_cache.upsert_stop(
            stop_code="260211",
            stop_name="KAVACIK",
            fiziki_durum="FULL KAPALI",
            akilli_durak=False,
            durak_tipi="İETTCAMLI",
        )
        row = stop_cache.get_stop("260211")
        assert row is not None
        assert row["district"] == "Beykoz"
        assert row["fiziki_durum"] == "FULL KAPALI"
        assert row["akilli_durak"] is False
        assert row["durak_tipi"] == "İETTCAMLI"


class TestIettClientSoapMethods:
    async def test_get_route_buses_use_mobiett_false_skips_mobiett(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.soap_router import soap_router

        monkeypatch.setattr(soap_router, "_direct_healthy", True)
        route_xml = _soap_envelope(
            "GetHatOtoKonum_json",
            json.dumps(
                [
                    {
                        "kapino": "A5108",
                        "boylam": "28.724",
                        "enlem": "40.984",
                        "hatkodu": "34AS",
                        "guzergahkodu": "34AS_G_D9032",
                        "hatad": "AVCILAR - SÖĞÜTLÜÇEŞME",
                        "yon": "SÖĞÜTLÜÇEŞME",
                        "son_konum_zamani": "11:00:00",
                    }
                ]
            ),
        )
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector()) as session:
            client = IettClient(session)
            client.mobiett.get_live_fleet = AsyncMock(side_effect=AssertionError("must not call"))
            with aioresponses() as m:
                m.post(FLEET_URL, body=route_xml)
                buses = await client.get_route_buses("34AS", use_mobiett=False)
        assert len(buses) == 1
        assert buses[0].kapino == "A-5108"
        client.mobiett.get_live_fleet.assert_not_called()

    async def test_get_route_stops_soap_durak_detay_gyy(self) -> None:
        gyy_xml = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
            '<soap:Body><DurakDetay_GYYResponse xmlns="http://tempuri.org/">'
            "<DurakDetay_GYYResult><NewDataSet><Table>"
            "<HATKODU>500T</HATKODU><YON>D</YON><SIRANO>1</SIRANO>"
            "<DURAKKODU>301341</DURAKKODU><DURAKADI>4.LEVENT METRO</DURAKADI>"
            "<XKOORDINATI>29.007309</XKOORDINATI><YKOORDINATI>41.084170</YKOORDINATI>"
            "<ILCEADI>Sisli</ILCEADI>"
            "</Table></NewDataSet></DurakDetay_GYYResult>"
            "</DurakDetay_GYYResponse></soap:Body></soap:Envelope>"
        )
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector()) as session:
            client = IettClient(session)
            with aioresponses() as m:
                m.post(IBB_GYY_URL, body=gyy_xml)
                stops = await client.get_route_stops_soap("500T")
        assert len(stops) == 1
        assert stops[0].stop_code == "301341"
        assert stops[0].latitude == pytest.approx(41.084170)

    async def test_get_route_info_and_stop_info_soap(self) -> None:
        hat_xml = _soap_envelope(
            "GetHat_json",
            json.dumps([{"SHATKODU": "500T", "TARIFE": "2 BİLETLİ", "SEFER_SURESI": 272.93}]),
        )
        durak_xml = _soap_envelope(
            "GetDurak_json",
            json.dumps(
                [
                    {
                        "SDURAKKODU": 260211,
                        "ILCEADI": "Beykoz",
                        "FIZIKI": "FULL KAPALI",
                        "AKILLI": "YOK",
                    }
                ]
            ),
        )
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector()) as session:
            client = IettClient(session)
            with aioresponses() as m:
                m.post(GET_HAT_URL, body=hat_xml)
                m.post(GET_HAT_URL, body=durak_xml)
                r_info = await client.get_route_info_soap("500T")
                s_info = await client.get_stop_info_soap("260211")
        assert r_info is not None and r_info["tarife"] == "2 BİLETLİ"
        assert s_info is not None and s_info["ilce"] == "Beykoz"
        assert s_info["akilli_durak"] is False


class TestRouterFallbacks:
    def test_route_stops_prefers_durak_detay_gyy_before_html_scrape(self) -> None:
        client = TestClient(app)
        soap_stop = RouteStop(
            route_code="500T",
            direction="G",
            sequence=1,
            stop_code="301341",
            stop_name="4.LEVENT METRO",
            latitude=41.084,
            longitude=29.007,
            district="Şişli",
        )
        mock_client = MagicMock()
        mock_client.get_route_stops_soap = AsyncMock(return_value=[soap_stop])
        mock_client.get_route_stops = AsyncMock(side_effect=AssertionError("must not scrape HTML"))

        with (
            patch("app.routers.routes.get_session", return_value=MagicMock()),
            patch(
                "app.routers.routes.ntcapi_client.get_route_stops",
                AsyncMock(side_effect=Exception("unused")),
            ),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            from app.services.ntcapi_client import NtcApiError

            with patch(
                "app.routers.routes.ntcapi_client.get_route_stops",
                AsyncMock(side_effect=NtcApiError("ntc down")),
            ):
                resp = client.get("/v1/routes/500T/stops")
        assert resp.status_code == 200
        assert resp.json()[0]["stop_code"] == "301341"
        mock_client.get_route_stops.assert_not_called()

    def test_route_info_falls_back_to_get_hat_json(self) -> None:
        client = TestClient(app)
        mock_client = MagicMock()
        mock_client.scrape_route_info = AsyncMock(side_effect=IettApiError("html blocked"))
        mock_client.get_route_info_soap = AsyncMock(
            return_value={
                "hat_kodu": "500T",
                "trip_duration_min": 136,
                "hat_tipi": None,
                "tarife": "2 BİLETLİ",
                "details": ["Hat Uzunluğu: 56.75 km"],
            }
        )
        with (
            patch("app.routers.routes.get_session", return_value=MagicMock()),
            patch("app.routers.routes.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/routes/500T/info")
        assert resp.status_code == 200
        assert resp.json() == {
            "hat_kodu": "500T",
            "trip_duration_min": 136,
            "hat_tipi": None,
            "tarife": "2 BİLETLİ",
        }

    def test_stop_info_3_tier_fallback(self) -> None:
        client = TestClient(app)
        mock_client = MagicMock()
        mock_client.scrape_stop_info = AsyncMock(side_effect=IettApiError("html down"))
        mock_client.get_stop_info_soap = AsyncMock(
            return_value={
                "dcode": "260211",
                "ilce": "Beykoz",
                "fiziki_durum": "FULL KAPALI",
                "akilli_durak": False,
            }
        )
        with (
            patch("app.routers.stops.get_session", return_value=MagicMock()),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp = client.get("/v1/stops/260211/info")
        assert resp.status_code == 200
        assert resp.json()["ilce"] == "Beykoz"
        assert resp.json()["akilli_durak"] is False

        # Tier 3: both HTML and SOAP fail -> serve from stop_cache
        from app.services.cache import _store

        _store.clear()
        record_stops_batch(
            [
                {
                    "stop_code": "260212",
                    "stop_name": "KAVACIK",
                    "latitude": 41.089,
                    "longitude": 29.088,
                    "district": "Beykoz",
                    "fiziki_durum": "ACIK",
                    "akilli_durak": True,
                }
            ]
        )
        stop_cache.upsert_stop(
            stop_code="260212",
            stop_name="KAVACIK",
            district="Beykoz",
            fiziki_durum="ACIK",
            akilli_durak=True,
        )
        mock_client.get_stop_info_soap = AsyncMock(side_effect=IettApiError("soap down"))
        with (
            patch("app.routers.stops.get_session", return_value=MagicMock()),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp2 = client.get("/v1/stops/260212/info")
        assert resp2.status_code == 200
        assert resp2.json() == {
            "dcode": "260212",
            "ilce": "Beykoz",
            "fiziki_durum": "ACIK",
            "akilli_durak": True,
        }

        # All 3 tiers empty -> 502
        _store.clear()
        with (
            patch("app.routers.stops.get_session", return_value=MagicMock()),
            patch("app.routers.stops.IettClient", return_value=mock_client),
        ):
            resp3 = client.get("/v1/stops/999999/info")
        assert resp3.status_code == 502
