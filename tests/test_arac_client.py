"""Unit tests for app.services.arac_client."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aioresponses import aioresponses

from app.services.arac_client import (
    AracApiError,
    AracClient,
    _as_text,
    _clip,
    _direction_letter_from_route_code,
    _extract_error_message,
    _is_html_text,
    _to_bool,
    _to_float,
    _to_int,
    solve_captcha_image,
)


@pytest.fixture()
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.ThreadedResolver() if sys.platform == "win32" else None
    )
    s = aiohttp.ClientSession(connector=connector)
    yield s
    await s.close()


def _base_url(path: str) -> str:
    return f"https://arac.iett.gov.tr{path}"


class TestHelperFns:
    def test_clip_truncates(self) -> None:
        value = _clip("a" * 600, limit=10)
        assert value.startswith("a" * 10)
        assert value.endswith("...<truncated>")

    def test_as_text(self) -> None:
        assert _as_text(None) is None
        assert _as_text("  abc  ") == "abc"
        assert _as_text("   ") is None

    def test_to_int(self) -> None:
        assert _to_int(None) is None
        assert _to_int("7") == 7
        assert _to_int(5.1) == 5
        assert _to_int("bad") is None
        assert _to_int(10**400) is None

    def test_to_float(self) -> None:
        assert _to_float("7.5") == pytest.approx(7.5)
        assert _to_float("bad") is None
        assert _to_float(10**400) is None

    def test_to_bool(self) -> None:
        assert _to_bool(True) is True
        assert _to_bool(1) is True
        assert _to_bool(0) is False
        assert _to_bool("yes") is True
        assert _to_bool("No") is False
        assert _to_bool("unknown") is None

    def test_extract_error_message(self) -> None:
        assert _extract_error_message({"message": "x"}) == "x"
        assert _extract_error_message({"error": "y"}) == "y"
        assert _extract_error_message({"detail": "z"}) == "z"
        assert (
            _extract_error_message({"detail": "<html><body>405 Not Allowed</body></html>"}) is None
        )
        assert _extract_error_message({"oops": 1}) is None

    def test_is_html_text(self) -> None:
        assert _is_html_text("<html><body>oops</body></html>") is True
        assert _is_html_text("<!doctype html><html>") is True
        assert _is_html_text("Wrong CAPTCHA") is False

    def test_direction_letter_from_route_code(self) -> None:
        assert _direction_letter_from_route_code("14R_G_D0") == "G"
        assert _direction_letter_from_route_code("14R_D_D0") == "D"
        assert _direction_letter_from_route_code("14R") is None
        assert _direction_letter_from_route_code(None) is None


class TestAracClientMethods:
    async def test_get_vehicle_hash_success(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(
                _base_url("/Home/GetAllVehicleSelectList"),
                payload={
                    "isSuccess": True,
                    "data": [
                        {"doorNumber": "C-1753", "value": "hash_123"},
                        {"doorNumber": "A-328", "value": "hash_456"},
                    ],
                },
            )
            vhash = await client.get_vehicle_hash("C-1753")
            assert vhash == "hash_123"

    async def test_get_vehicle_hash_not_found(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(
                _base_url("/Home/GetAllVehicleSelectList"),
                payload={"isSuccess": True, "data": []},
            )
            with pytest.raises(AracApiError, match="Vehicle hash not found") as exc:
                await client.get_vehicle_hash("X-999")
            assert exc.value.status_code == 404

    async def test_get_vehicle_hash_rate_limited(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(
                _base_url("/Home/GetAllVehicleSelectList"),
                payload={
                    "isSuccess": False,
                    "data": [],
                    "message": "Çok fazla istek yapıldı. Lütfen biraz bekleyin.",
                },
            )
            with pytest.raises(AracApiError) as exc:
                await client.get_vehicle_hash("C-1753")
            assert exc.value.status_code == 429

    async def test_get_captcha_success(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(
                _base_url("/Home/Captcha"),
                payload={"isSuccess": True, "captchaImage": "data:image/png;base64,iVBORw=="},
            )
            res = await client.get_captcha()
            assert "captchaId" in res
            assert res["captchaImage"] == "data:image/png;base64,iVBORw=="

    async def test_get_captcha_missing_image(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(
                _base_url("/Home/Captcha"),
                payload={"isSuccess": True, "captchaImage": ""},
            )
            with pytest.raises(AracApiError, match="missing captchaImage"):
                await client.get_captcha()

    async def test_submit_captcha_success(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(
                _base_url("/Home/Control"),
                payload={"isSuccess": True},
            )
            ok = await client.submit_captcha("hash_123", "123456")
            assert ok is True

    async def test_submit_captcha_failed(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(
                _base_url("/Home/Control"),
                payload={"isSuccess": False, "message": "Yanlış captcha"},
            )
            ok = await client.submit_captcha("hash_123", "000000")
            assert ok is False

    async def test_get_detail_success(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(
                _base_url("/Home/GetDetail"),
                payload={
                    "isSuccess": True,
                    "dataVehicle": {
                        "vehicleDoorCode": "C-1753",
                        "numberPlate": "34 HO 1753",
                        "latitude": 41.01,
                        "longitude": 28.97,
                        "operatorType": "ÖHO",
                    },
                    "dataTask": [],
                },
            )
            detail = await client.get_detail("hash_123")
            assert detail["isSuccess"] is True
            assert detail["dataVehicle"]["vehicleDoorCode"] == "C-1753"

    async def test_get_detail_unauthorized(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(
                _base_url("/Home/GetDetail"),
                payload={"isSuccess": False, "message": "Session expired"},
            )
            with pytest.raises(AracApiError) as exc:
                await client.get_detail("hash_123")
            assert exc.value.status_code == 401

    def test_solve_captcha_image_strips_prefix(self) -> None:
        # Invalid base64 or garbage returns None gracefully
        res = solve_captcha_image("data:image/png;base64,invalid_garbage")
        assert res is None or isinstance(res, str)

    def test_normalize_bus_position(self) -> None:
        pos = AracClient.normalize_bus_position(
            {
                "vehicleDoorCode": "C-1753",
                "numberPlate": "34 HO 1753",
                "latitude": "41.0123",
                "longitude": "28.9765",
                "operatorType": "İstanbul Halk Ulaşım",
                "accessibility": True,
                "hasUsbCharger": False,
                "hasWifi": True,
                "hasBicycleRack": False,
                "isAirConditioned": True,
                "lastLocationDate": "2026-07-25",
                "lastLocationTime": "21:30:00",
            }
        )
        assert pos.kapino == "C-1753"
        assert pos.plate == "34 HO 1753"
        assert pos.latitude == pytest.approx(41.0123)
        assert pos.longitude == pytest.approx(28.9765)
        assert pos.operator_name == "İstanbul Halk Ulaşım"
        assert pos.accessible is True
        assert pos.has_wifi is True
        assert pos.last_seen == "2026-07-25 21:30:00"

    def test_normalize_missions(self) -> None:
        missions = AracClient.normalize_missions(
            [
                {
                    "lineCode": "14R",
                    "firstStop": "KADIKÖY",
                    "orer": "21:45",
                }
            ]
        )
        assert len(missions) == 1
        assert missions[0].line_code == "14R"
        assert missions[0].first_stop == "KADIKÖY"
        assert missions[0].departure_time == "21:45"
