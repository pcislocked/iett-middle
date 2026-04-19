"""Unit tests for app.services.arac_client."""
from __future__ import annotations

import base64
import json
import sys
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
from aioresponses import aioresponses

from app.config import settings
from app.models.bus import BusPosition
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
    return f"{settings.arac_base.rstrip('/')}{path}"


def _sample_bus_payload() -> dict[str, object]:
    return {
        "vehicleDoorCode": "C-1753",
        "numberPlate": "34 HO 1753",
        "operatorId": 5,
        "operatorType": "Istanbul Halk Ulasim",
        "accessibility": True,
        "brandName": "MERCEDES CONECTO",
        "modelYear": 2015,
        "vehicleType": "Solo -12m",
        "seatingCapacity": 27,
        "fullCapacity": 96,
        "isAirConditioned": None,
        "hasUsbCharger": True,
        "hasWifi": False,
        "hasBicycleRack": False,
        "vehicleSoftwareVersion": 2,
        "garageCode": "IKT",
        "garageName": "IKITELLI",
        "latitude": 41.01,
        "longitude": 29.02,
        "speed": 10,
        "lastLocationDate": "18-04-2026",
        "lastLocationTime": "00:16:56",
        "lineCode": "14R_G_D0",
    }


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
        assert _extract_error_message({"detail": "<html><body>405 Not Allowed</body></html>"}) is None
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


class TestRequestJson:
    def test_headers_include_session_when_provided(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        headers = client._headers("sid", "skey")
        assert headers["X-Session-Id"] == "sid"
        assert headers["X-Session-Key"] == "skey"

    async def test_success_json(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(_base_url("/session/captcha"), payload={"captchaId": "cid", "captchaImage": "img"})  # type: ignore[reportUnknownMemberType]
            payload = await client._request_json("POST", "/session/captcha", raw_data="")
        assert payload["captchaId"] == "cid"

    async def test_non_json_success_raises(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(  # type: ignore[reportUnknownMemberType]
                _base_url("/session/captcha"),
                status=200,
                body="<html>ok</html>",
                headers={"Content-Type": "text/html"},
            )
            with pytest.raises(AracApiError, match="returned non-JSON content"):
                await client._request_json("POST", "/session/captcha", raw_data="")

    async def test_malformed_json_success_raises(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(  # type: ignore[reportUnknownMemberType]
                _base_url("/session/captcha"),
                status=200,
                body="{not-json",
                headers={"Content-Type": "application/json"},
            )
            with pytest.raises(AracApiError, match="returned malformed JSON"):
                await client._request_json("POST", "/session/captcha", raw_data="")

    async def test_http_error_prefers_payload_message(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(_base_url("/session/create"), status=400, payload={"error": "Wrong CAPTCHA"})  # type: ignore[reportUnknownMemberType]
            with pytest.raises(AracApiError, match="Wrong CAPTCHA") as exc_info:
                await client._request_json("POST", "/session/create", json_body={})
        assert exc_info.value.status_code == 400

    async def test_http_error_default_message(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(_base_url("/session/create"), status=500, payload={"oops": 1})  # type: ignore[reportUnknownMemberType]
            with pytest.raises(AracApiError, match="failed with status 500"):
                await client._request_json("POST", "/session/create", json_body={})

    async def test_http_error_html_detail_is_sanitized(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(  # type: ignore[reportUnknownMemberType]
                _base_url("/session/captcha"),
                status=405,
                payload={"detail": "<html><body>405 Not Allowed</body></html>"},
            )
            with pytest.raises(AracApiError, match="status 405") as exc_info:
                await client._request_json("POST", "/session/captcha", raw_data="")
        assert "<html" not in str(exc_info.value).lower()

    async def test_http_error_text_html_405_raw_payload_is_sanitized(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with aioresponses() as m:
            m.post(  # type: ignore[reportUnknownMemberType]
                _base_url("/session/captcha"),
                status=405,
                body="<html><head><title>405</title></head><body>Not Allowed</body></html>",
                headers={"Content-Type": "text/html"},
            )
            with pytest.raises(AracApiError, match="HTML error page") as exc_info:
                await client._request_json("POST", "/session/captcha", raw_data="")
        message = str(exc_info.value)
        assert "status 405" in message
        assert "<html" not in message.lower()

    async def test_network_exception_is_wrapped(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with patch.object(session, "request", side_effect=RuntimeError("boom")):
            with pytest.raises(AracApiError, match="failed: boom"):
                await client._request_json("GET", "/task/crypto/pubkey")


class TestCryptoHelpers:
    def test_prepare_encryption_bundle_roundtrip(self) -> None:
        crypto = pytest.importorskip("cryptography")
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub_der = private_key.public_key().public_bytes(
            encoding=Encoding.DER,
            format=PublicFormat.SubjectPublicKeyInfo,
        )
        aes_key, enc_key = AracClient._prepare_encryption_bundle(base64.b64encode(pub_der).decode("utf-8"))

        decrypted_key = private_key.decrypt(
            base64.b64decode(enc_key),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        assert decrypted_key == aes_key
        assert len(aes_key) == 32
        assert crypto is not None

    def test_prepare_encryption_bundle_missing_crypto(self) -> None:
        import builtins

        original_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name.startswith("cryptography"):
                raise ModuleNotFoundError("missing")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            with pytest.raises(AracApiError, match="cryptography package"):
                AracClient._prepare_encryption_bundle("AA==")

    def test_prepare_encryption_bundle_invalid_key_raises_arac_error(self) -> None:
        pytest.importorskip("cryptography")
        with pytest.raises(AracApiError, match="Failed to prepare ARAC encryption bundle"):
            AracClient._prepare_encryption_bundle("AA==")

    def test_decrypt_if_needed_passthroughs(self) -> None:
        assert AracClient._decrypt_if_needed(b"k", "text") == "text"
        payload = {"foo": "bar"}
        assert AracClient._decrypt_if_needed(b"k", payload) == payload

    def test_decrypt_if_needed_success(self) -> None:
        pytest.importorskip("cryptography")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        key = AESGCM.generate_key(bit_length=256)
        aesgcm = AESGCM(key)
        iv = b"123456789012"
        plain = json.dumps({"ok": True}).encode("utf-8")
        cipher = aesgcm.encrypt(iv, plain, None)

        payload = {
            "iv": base64.b64encode(iv).decode("utf-8"),
            "data": base64.b64encode(cipher).decode("utf-8"),
        }
        result = AracClient._decrypt_if_needed(key, payload)
        assert result == {"ok": True}

    def test_decrypt_if_needed_invalid_payload_raises(self) -> None:
        with pytest.raises(AracApiError, match="Failed to decrypt"):
            AracClient._decrypt_if_needed(b"k", {"iv": "bad", "data": "bad"})

    def test_decrypt_if_needed_missing_crypto(self) -> None:
        import builtins

        original_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name.startswith("cryptography"):
                raise ModuleNotFoundError("missing")
            return original_import(name, *args, **kwargs)

        payload = {"iv": "aXY=", "data": "ZGF0YQ=="}
        with patch("builtins.__import__", side_effect=_fake_import):
            with pytest.raises(AracApiError, match="cryptography package"):
                AracClient._decrypt_if_needed(b"k", payload)


class TestClientMethods:
    async def test_get_captcha_success(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with patch.object(client, "_request_json", AsyncMock(return_value={"captchaId": "cid", "captchaImage": "img"})):
            payload = await client.get_captcha()
        assert payload == {"captchaId": "cid", "captchaImage": "img"}

    async def test_get_captcha_falls_back_to_getpicture_on_405(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        request_mock = AsyncMock(
            side_effect=[
                AracApiError("ARAC POST /session/captcha failed with status 405", status_code=405),
                {"captchaId": "cid-2", "captchaImage": "BBB"},
            ]
        )
        with patch.object(client, "_request_json", request_mock):
            payload = await client.get_captcha()

        assert payload == {"captchaId": "cid-2", "captchaImage": "BBB"}
        assert request_mock.await_args_list[0].args[:2] == ("POST", "/session/captcha")
        assert request_mock.await_args_list[1].args[:2] == ("POST", "/session/getpicture")

    async def test_get_captcha_tries_get_after_both_post_attempts_fail(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        request_mock = AsyncMock(
            side_effect=[
                AracApiError("ARAC POST /session/captcha failed with status 405", status_code=405),
                AracApiError("ARAC POST /session/getpicture failed with status 404", status_code=404),
                {"captchaId": "cid-3", "captchaImage": "CCC"},
            ]
        )
        with patch.object(client, "_request_json", request_mock):
            payload = await client.get_captcha()

        assert payload == {"captchaId": "cid-3", "captchaImage": "CCC"}
        assert request_mock.await_args_list[0].args[:2] == ("POST", "/session/captcha")
        assert request_mock.await_args_list[1].args[:2] == ("POST", "/session/getpicture")
        assert request_mock.await_args_list[2].args[:2] == ("GET", "/session/captcha")

    async def test_get_captcha_stops_on_non_retryable_error(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        request_mock = AsyncMock(
            side_effect=[AracApiError("Wrong CAPTCHA", status_code=400)]
        )
        with patch.object(client, "_request_json", request_mock):
            with pytest.raises(AracApiError, match="Wrong CAPTCHA"):
                await client.get_captcha()

        assert request_mock.await_count == 1
        assert request_mock.await_args_list[0].args[:2] == ("POST", "/session/captcha")

    async def test_get_captcha_stops_after_retryable_then_non_retryable_error(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        request_mock = AsyncMock(
            side_effect=[
                AracApiError("ARAC POST /session/captcha failed with status 405", status_code=405),
                AracApiError("Wrong CAPTCHA", status_code=400),
            ]
        )
        with patch.object(client, "_request_json", request_mock):
            with pytest.raises(AracApiError, match="Wrong CAPTCHA"):
                await client.get_captcha()

        assert request_mock.await_count == 2
        assert request_mock.await_args_list[0].args[:2] == ("POST", "/session/captcha")
        assert request_mock.await_args_list[1].args[:2] == ("POST", "/session/getpicture")

    async def test_get_captcha_exhausts_all_attempts_and_raises_last_error(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        request_mock = AsyncMock(
            side_effect=[
                AracApiError("ARAC POST /session/captcha failed with status 405", status_code=405),
                AracApiError("ARAC POST /session/getpicture failed with status 405", status_code=405),
                AracApiError("ARAC GET /session/captcha failed with status 405", status_code=405),
                AracApiError("ARAC GET /session/getpicture failed with status 405", status_code=405),
            ]
        )
        with patch.object(client, "_request_json", request_mock):
            with pytest.raises(AracApiError, match="GET /session/getpicture"):
                await client.get_captcha()

        assert request_mock.await_count == 4
        assert request_mock.await_args_list[0].args[:2] == ("POST", "/session/captcha")
        assert request_mock.await_args_list[1].args[:2] == ("POST", "/session/getpicture")
        assert request_mock.await_args_list[2].args[:2] == ("GET", "/session/captcha")
        assert request_mock.await_args_list[3].args[:2] == ("GET", "/session/getpicture")

    async def test_get_captcha_continues_when_payload_missing_then_accepts_later_attempt(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        request_mock = AsyncMock(
            side_effect=[
                {"captchaId": "cid-only"},
                {"captchaId": "cid-final", "captchaImage": "IMG"},
            ]
        )
        with patch.object(client, "_request_json", request_mock):
            payload = await client.get_captcha()

        assert payload == {"captchaId": "cid-final", "captchaImage": "IMG"}
        assert request_mock.await_count == 2
        assert request_mock.await_args_list[0].args[:2] == ("POST", "/session/captcha")
        assert request_mock.await_args_list[1].args[:2] == ("POST", "/session/getpicture")

    async def test_get_captcha_invalid_payloads(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with patch.object(client, "_request_json", AsyncMock(return_value=[])):
            with pytest.raises(AracApiError, match="not an object"):
                await client.get_captcha()

        with patch.object(client, "_request_json", AsyncMock(return_value={"captchaId": "cid"})):
            with pytest.raises(AracApiError, match="missing captchaId or captchaImage"):
                await client.get_captcha()

    async def test_create_session_success_and_failures(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with patch.object(client, "_request_json", AsyncMock(return_value={"sessionId": "sid", "sessionKey": "skey"})):
            payload = await client.create_session("cid", " abcd ")
        assert payload == {"sessionId": "sid", "sessionKey": "skey"}

        with patch.object(client, "_request_json", AsyncMock(return_value=[])):
            with pytest.raises(AracApiError, match="not an object"):
                await client.create_session("cid", "abcd")

        with patch.object(client, "_request_json", AsyncMock(return_value={"sessionId": "sid"})):
            with pytest.raises(AracApiError, match="missing sessionId or sessionKey"):
                await client.create_session("cid", "abcd")

    async def test_fetch_encrypted_task_happy_path(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)

        request_mock = AsyncMock(side_effect=[{"key": "PUBKEY"}, {"data": "X", "iv": "Y"}])
        with (
            patch.object(client, "_request_json", request_mock),
            patch.object(client, "_prepare_encryption_bundle", return_value=(b"aes", "ENCKEY")),
            patch.object(client, "_decrypt_if_needed", return_value={"ok": 1}) as decrypt_mock,
        ):
            payload = await client._fetch_encrypted_task("/task/bus-fleet/buses", session_id="sid", session_key="skey")

        assert payload == {"ok": 1}
        decrypt_mock.assert_called_once()
        assert request_mock.await_count == 2

    async def test_fetch_encrypted_task_invalid_pubkey_payload(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with patch.object(client, "_request_json", AsyncMock(return_value=[])):
            with pytest.raises(AracApiError, match="unexpected payload"):
                await client._fetch_encrypted_task("/task/bus-fleet/buses", session_id="sid", session_key="skey")

        with patch.object(client, "_request_json", AsyncMock(return_value={"x": 1})):
            with pytest.raises(AracApiError, match="response missing key"):
                await client._fetch_encrypted_task("/task/bus-fleet/buses", session_id="sid", session_key="skey")

    def test_normalize_bus_position(self) -> None:
        row = _sample_bus_payload()
        bus = AracClient._normalize_bus_position(row)
        assert isinstance(bus, BusPosition)
        assert bus is not None
        assert bus.kapino == "C-1753"
        assert bus.operator_id == 5
        assert bus.direction_letter == "G"

    def test_normalize_bus_position_missing_required_fields(self) -> None:
        row = _sample_bus_payload()
        row.pop("vehicleDoorCode")
        assert AracClient._normalize_bus_position(row) is None

    def test_normalize_bus_position_internal_exception_returns_none(self) -> None:
        row = _sample_bus_payload()
        with patch("app.services.arac_client.BusPosition", side_effect=Exception("bad model")):
            assert AracClient._normalize_bus_position(row) is None

    async def test_get_fleet(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        rows = [_sample_bus_payload(), {"invalid": True}, "skip"]

        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value=rows)):
            fleet = await client.get_fleet(session_id="sid", session_key="skey")
        assert len(fleet) == 1
        assert fleet[0].kapino == "C-1753"

        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value={"x": 1})):
            with pytest.raises(AracApiError, match="fleet payload is not a list"):
                await client.get_fleet(session_id="sid", session_key="skey")

    async def test_get_vehicle(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)

        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value=_sample_bus_payload())):
            bus = await client.get_vehicle("C-1753", session_id="sid", session_key="skey")
        assert bus.kapino == "C-1753"

        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value=[_sample_bus_payload()])):
            bus = await client.get_vehicle("C-1753", session_id="sid", session_key="skey")
        assert bus.kapino == "C-1753"

        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value=[])):
            with pytest.raises(AracApiError, match="payload for 'C-1753' is empty") as exc_info:
                await client.get_vehicle("C-1753", session_id="sid", session_key="skey")
            assert exc_info.value.status_code == 404

        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value={"x": 1})):
            with pytest.raises(AracApiError, match="could not be normalized") as exc_info:
                await client.get_vehicle("C-1753", session_id="sid", session_key="skey")
            assert exc_info.value.status_code == 502

    async def test_get_missions(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value=[{"taskId": 1}, "skip"])):
            payload = await client.get_missions("C-1753", session_id="sid", session_key="skey")
        assert payload == [{"taskId": 1}]

        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value={"x": 1})):
            with pytest.raises(AracApiError, match="missions payload is not a list"):
                await client.get_missions("C-1753", session_id="sid", session_key="skey")

    async def test_get_route_stops(self, session: aiohttp.ClientSession) -> None:
        client = AracClient(session)
        rows = [
            {
                "stopOrder": 2,
                "stopId": 2,
                "stopName": "B",
                "latitude": 41.1,
                "longitude": 29.1,
            },
            {
                "stopOrder": 1,
                "stopId": 1,
                "stopName": "A",
                "latitude": 41.0,
                "longitude": 29.0,
            },
            "skip",
        ]
        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value=rows)):
            stops = await client.get_route_stops("16", session_id="sid", session_key="skey")
        assert len(stops) == 2
        assert stops[0].stop_order == 1
        assert stops[0].stop_name == "A"

        with patch.object(client, "_fetch_encrypted_task", AsyncMock(return_value={"x": 1})):
            with pytest.raises(AracApiError, match="route-stops payload is not a list"):
                await client.get_route_stops("16", session_id="sid", session_key="skey")


class TestCaptchaRetryPolicy:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (AracApiError("no status"), True),
            (AracApiError("upstream 500", status_code=500), True),
            (AracApiError("method not allowed", status_code=405), True),
            (AracApiError("unsupported media", status_code=415), True),
            (AracApiError("returned non-JSON content", status_code=200), True),
            (AracApiError("returned malformed JSON", status_code=200), True),
            (AracApiError("HTML error page", status_code=400), True),
            (AracApiError("Wrong CAPTCHA", status_code=400), False),
        ],
    )
    def test_should_retry_captcha_fetch_matrix(self, error: AracApiError, expected: bool) -> None:
        assert AracClient._should_retry_captcha_fetch(error) is expected
