"""Tests for persistent SQLite stop cache and Squid-style stop ingestion hooks."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.deps import clear_stop_store, get_stop_info_by_code, record_stops_batch
from app.main import app
from app.models.stop import StopSearchResult
from app.services import stop_cache


@pytest.fixture(autouse=True)
def _isolate_stop_cache(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_file = str(tmp_path / "test_stops.db")
    monkeypatch.setattr(stop_cache.settings, "stop_cache_path", db_file)
    monkeypatch.setattr("app.routers.stops.get_session", lambda: MagicMock())
    monkeypatch.setattr("app.routers.routes.get_session", lambda: MagicMock())
    stop_cache.close_connection()
    clear_stop_store()
    yield
    stop_cache.close_connection()
    clear_stop_store()


class TestStopCacheDatabase:
    def test_upsert_and_get_stop(self):
        stop_cache.upsert_stop(
            stop_code="100142",
            stop_name="KADIKÖY",
            latitude=40.9901,
            longitude=29.0255,
            district="Kadıköy",
            direction="BOSTANCI",
        )
        res = stop_cache.get_stop("100142")
        assert res is not None
        assert res["stop_code"] == "100142"
        assert res["stop_name"] == "KADIKÖY"
        assert res["latitude"] == pytest.approx(40.9901)
        assert res["longitude"] == pytest.approx(29.0255)
        assert res["district"] == "Kadıköy"
        assert res["direction"] == "BOSTANCI"

    def test_non_destructive_coalesce_merge(self):
        # First write has lat, lon, district
        stop_cache.upsert_stop(
            stop_code="100142",
            stop_name="KADIKÖY",
            latitude=40.9901,
            longitude=29.0255,
            district="Kadıköy",
            direction=None,
        )
        # Second write has direction but no coordinates or district
        stop_cache.upsert_stop(
            stop_code="100142",
            stop_name="",
            latitude=None,
            longitude=None,
            district=None,
            direction="RIHTIM",
        )
        res = stop_cache.get_stop("100142")
        assert res is not None
        assert res["stop_name"] == "KADIKÖY"  # Not wiped
        assert res["latitude"] == pytest.approx(40.9901)  # Preserved
        assert res["longitude"] == pytest.approx(29.0255)  # Preserved
        assert res["district"] == "Kadıköy"  # Preserved
        assert res["direction"] == "RIHTIM"  # Merged

    def test_direction_variant_codes_ignored(self):
        # 'G' and 'D' from RouteStop should never overwrite physical direction
        stop_cache.upsert_stop(
            stop_code="100142",
            stop_name="KADIKÖY",
            direction="BOSTANCI",
        )
        stop_cache.upsert_stop(
            stop_code="100142",
            stop_name="KADIKÖY",
            direction="G",
        )
        res = stop_cache.get_stop("100142")
        assert res is not None
        assert res["direction"] == "BOSTANCI"

    def test_batch_upsert_and_search(self):
        batch: list[dict[str, Any]] = [
            {
                "stop_code": "200101",
                "stop_name": "BEŞİKTAŞ MEYDAN",
                "lat": 41.0422,
                "lon": 29.0067,
                "district": "Beşiktaş",
                "direction": "ORTAKÖY",
            },
            {
                "stop_code": "200102",
                "stop_name": "BEŞİKTAŞ İSKELE",
                "lat": 41.0415,
                "lon": 29.0071,
                "district": "Beşiktaş",
                "direction": "KABATAŞ",
            },
            {
                "stop_code": "300101",
                "stop_name": "TAKSİM",
                "lat": 41.0370,
                "lon": 28.9850,
                "district": "Beyoğlu",
            },
        ]
        stop_cache.upsert_stops_batch(batch)
        all_stops = stop_cache.get_all_stops()
        assert len(all_stops) == 3

        search_res = stop_cache.search_stops("BEŞİKTAŞ")
        assert len(search_res) == 2
        assert all("BEŞİKTAŞ" in s["stop_name"] for s in search_res)

        code_res = stop_cache.search_stops("300101")
        assert len(code_res) == 1
        assert code_res[0]["stop_name"] == "TAKSİM"

    def test_empty_or_invalid_inputs_do_not_crash(self):
        stop_cache.upsert_stop("", "")
        stop_cache.upsert_stops_batch([])
        stop_cache.upsert_stops_batch([{"stop_code": ""}])
        assert stop_cache.get_stop("") is None
        assert stop_cache.search_stops("") == []

    @pytest.mark.asyncio
    async def test_concurrent_read_write_stress(self):
        async def writer(i: int):
            for j in range(10):
                await asyncio.to_thread(
                    stop_cache.upsert_stop,
                    f"code_{i}_{j}",
                    f"Stop {i} {j}",
                    41.0 + (i * 0.001),
                    29.0 + (j * 0.001),
                    "District",
                    "Direction",
                )

        async def reader():
            for _ in range(15):
                await asyncio.to_thread(stop_cache.search_stops, "Stop")
                await asyncio.sleep(0.001)

        await asyncio.gather(
            writer(1),
            writer(2),
            writer(3),
            reader(),
            reader(),
        )
        assert len(stop_cache.get_all_stops()) == 30


class TestStopIngestionHooks:
    def test_record_stops_batch_populates_memory_and_sqlite(self):
        items: list[dict[str, Any]] = [
            {
                "stop_code": "100999",
                "stop_name": "TEST DURAK",
                "latitude": 41.0500,
                "longitude": 29.0100,
                "district": "Şişli",
                "direction": "MECİDİYEKÖY",
            }
        ]
        record_stops_batch(items)
        # Immediate in-memory check
        cached = get_stop_info_by_code("100999")
        assert cached is not None
        assert cached["stop_name"] == "TEST DURAK"
        assert cached["district"] == "Şişli"
        assert cached["direction"] == "MECİDİYEKÖY"

        # SQLite check
        db_stop = stop_cache.get_stop("100999")
        assert db_stop is not None
        assert db_stop["stop_name"] == "TEST DURAK"

    def test_search_stops_enriches_coords_and_falls_back(self):
        client = TestClient(app, raise_server_exceptions=False)
        # Preload stop cache with coordinates
        stop_cache.upsert_stop(
            stop_code="100555",
            stop_name="ZİNCİRLİKUYU",
            latitude=41.0680,
            longitude=29.0120,
            district="Şişli",
            direction="LEVENT",
        )
        record_stops_batch([stop_cache.get_stop("100555")])  # type: ignore

        # Mock IettClient.search_stops returning scraper results without lat/lon
        with patch("app.routers.stops.IettClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.search_stops = AsyncMock(
                return_value=[
                    StopSearchResult(
                        dcode="100555",
                        name="ZİNCİRLİKUYU",
                        path=None,
                        direction=None,
                        district=None,
                    )
                ]
            )
            mock_cls.return_value = mock_client

            resp = client.get("/v1/stops/search?q=ZİNCİRLİKUYU")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["latitude"] == pytest.approx(41.0680)
            assert data[0]["longitude"] == pytest.approx(29.0120)
            assert data[0]["district"] == "Şişli"
            assert data[0]["direction"] == "LEVENT"

    def test_nearby_stops_supports_limit_1000(self):
        client = TestClient(app, raise_server_exceptions=False)
        # Mock ntcapi_client.get_nearby_stops
        fake_stops = [
            {
                "stop_code": f"100{i:03d}",
                "stop_name": f"DURAK {i}",
                "lat": 41.0 + (i * 0.0001),
                "lon": 29.0 + (i * 0.0001),
                "direction": "YON",
            }
            for i in range(100)
        ]
        with patch(
            "app.services.ntcapi_client.get_nearby_stops", new=AsyncMock(return_value=fake_stops)
        ):
            resp = client.get("/v1/stops/nearby?lat=41.0&lon=29.0&limit=100")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 100
            # Verify batch was captured
            assert get_stop_info_by_code("100000") is not None
            assert stop_cache.get_stop("100000") is not None

    def test_get_stop_detail_fallback_to_cache(self):
        client = TestClient(app, raise_server_exceptions=False)
        stop_cache.upsert_stop(
            stop_code="100777",
            stop_name="BOĞAZİÇİ",
            latitude=41.0850,
            longitude=29.0530,
            district="Sarıyer",
            direction="HİSARÜSTÜ",
        )
        record_stops_batch([stop_cache.get_stop("100777")])  # type: ignore

        # When upstream fails with 502, get_stop_detail should fall back to cache
        from app.services.iett_client import IettApiError

        with patch("app.routers.stops.IettClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_stop_detail = AsyncMock(side_effect=IettApiError(500, "Upstream down"))
            mock_cls.return_value = mock_client

            resp = client.get("/v1/stops/100777")
            assert resp.status_code == 200
            data = resp.json()
            assert data["dcode"] == "100777"
            assert data["name"] == "BOĞAZİÇİ"
            assert data["latitude"] == pytest.approx(41.0850)
            assert data["longitude"] == pytest.approx(29.0530)
            assert data["district"] == "Sarıyer"
            assert data["direction"] == "HİSARÜSTÜ"

    def test_search_stops_turkish_case_insensitive(self):
        stop_cache.upsert_stop("200001", "BEŞİKTAŞ MEYDAN", 41.042, 29.008)
        stop_cache.upsert_stop("200002", "ŞİŞLİ CAMİİ", 41.060, 28.987)
        stop_cache.upsert_stop("200003", "KADIKÖY RIHTIM", 40.990, 29.020)

        # Searching with lowercase Turkish characters
        res_besiktas = stop_cache.search_stops("beşiktaş")
        assert len(res_besiktas) >= 1
        assert res_besiktas[0]["stop_code"] == "200001"

        res_sisli = stop_cache.search_stops("şişli")
        assert len(res_sisli) >= 1
        assert res_sisli[0]["stop_code"] == "200002"

        res_kadikoy = stop_cache.search_stops("kadıköy")
        assert len(res_kadikoy) >= 1
        assert res_kadikoy[0]["stop_code"] == "200003"

    def test_record_stops_batch_deferred_coordinates_enters_stop_index(self):
        from app.deps import get_nearby_stops

        clear_stop_store()
        # 1. Record without coords
        record_stops_batch([{"stop_code": "999888", "stop_name": "GEÇİCİ DURAK"}])
        assert get_stop_info_by_code("999888") is not None
        assert len(get_nearby_stops(41.0, 29.0)) == 0

        # 2. Later coords arrive
        record_stops_batch([{"stop_code": "999888", "latitude": 41.0001, "longitude": 29.0001}])
        nearby = get_nearby_stops(41.0, 29.0, radius_m=500.0)
        assert len(nearby) >= 1
        assert nearby[0]["stop_code"] == "999888"

    def test_stop_detail_fallback_to_sqlite_when_memory_empty(self):
        client = TestClient(app, raise_server_exceptions=False)
        stop_cache.upsert_stop("300555", "SQLITE ONLY STOP", 41.1, 29.1, "Beykoz", "KAVACIK")
        clear_stop_store()  # memory is completely cleared

        from app.services.iett_client import IettApiError

        with patch("app.routers.stops.IettClient") as mock_cls:
            mock_client = MagicMock()
            mock_client.get_stop_detail = AsyncMock(side_effect=IettApiError(500, "SOAP down"))
            mock_cls.return_value = mock_client

            resp = client.get("/v1/stops/300555")
            assert resp.status_code == 200
            data = resp.json()
            assert data["dcode"] == "300555"
            assert data["name"] == "SQLITE ONLY STOP"

    def test_record_stops_batch_does_not_insert_empty_stop_name(self):
        clear_stop_store()
        record_stops_batch([{"stop_code": "400444", "district": "Üsküdar"}])
        assert stop_cache.get_stop("400444") is None

    def test_record_stops_batch_persist_flag(self):
        clear_stop_store()
        record_stops_batch([{"stop_code": "500555", "stop_name": "MEMORY ONLY"}], persist=False)
        assert get_stop_info_by_code("500555") is not None
        assert stop_cache.get_stop("500555") is None

    def test_get_stop_coords_returns_none_when_coordless(self):
        from app.deps import get_stop_coords

        clear_stop_store()
        record_stops_batch([{"stop_code": "600111", "stop_name": "COORDLESS"}], persist=False)
        assert get_stop_coords("600111") is None

        record_stops_batch(
            [{"stop_code": "600111", "latitude": 41.02, "longitude": 28.97}], persist=False
        )
        assert get_stop_coords("600111") == pytest.approx((41.02, 28.97))

    def test_record_stops_batch_sets_updated_at_and_shares_dict_ref(self):
        from app.deps import get_nearby_stops, get_stop_index_updated_at

        clear_stop_store()
        assert get_stop_index_updated_at() is None

        record_stops_batch(
            [
                {
                    "stop_code": "700222",
                    "stop_name": "SHARED REF STOP",
                    "latitude": 41.0,
                    "longitude": 29.0,
                }
            ],
            persist=False,
        )
        assert get_stop_index_updated_at() is not None

        # Enrich with district & direction later
        record_stops_batch(
            [{"stop_code": "700222", "district": "Kadıköy", "direction": "RIHTIM"}],
            persist=False,
        )
        nearby = get_nearby_stops(41.0, 29.0, radius_m=500.0)
        assert len(nearby) == 1
        assert nearby[0]["district"] == "Kadıköy"
        assert nearby[0]["direction"] == "RIHTIM"

    def test_update_stop_index_preserves_preloaded_stops_and_metadata(self):
        from app.deps import get_nearby_stops, update_stop_index
        from app.models.stop import NearbyStop

        clear_stop_store()
        record_stops_batch(
            [
                {
                    "stop_code": "800111",
                    "stop_name": "PRELOADED ONLY",
                    "latitude": 41.0,
                    "longitude": 29.0,
                    "district": "Beşiktaş",
                },
                {
                    "stop_code": "800222",
                    "stop_name": "SHARED STOP",
                    "latitude": 41.0005,
                    "longitude": 29.0005,
                    "district": "Şişli",
                    "direction": "MECİDİYEKÖY",
                },
            ],
            persist=False,
        )

        # SOAP returns only 800222 without district
        update_stop_index(
            [
                NearbyStop(
                    stop_code="800222",
                    stop_name="SHARED STOP UPDATED",
                    latitude=41.0005,
                    longitude=29.0005,
                    distance_m=0.0,
                )
            ]
        )

        nearby = get_nearby_stops(41.0, 29.0, radius_m=1000.0)
        codes = {s["stop_code"]: s for s in nearby}
        assert "800111" in codes
        assert "800222" in codes
        assert codes["800222"]["district"] == "Şişli"
        assert codes["800222"]["direction"] == "MECİDİYEKÖY"
