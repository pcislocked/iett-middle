"""ARAC API client with captcha/session bootstrap and encrypted task calls."""
from __future__ import annotations

import base64
import json
from typing import Any

import aiohttp

from app.config import settings
from app.models.arac import AracRouteStop
from app.models.bus import BusPosition
from app.utils.coerce import _as_text, _to_bool, _to_float, _to_int


_BASE_HEADERS = {
    # ARAC captcha endpoints may reject non-browser-like requests with HTML 403 pages.
    # Mirror the public web client's fetch profile to keep captcha bootstrap stable.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://arac.iett.gov.tr",
    "Referer": "https://arac.iett.gov.tr/",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "sec-ch-ua": '"Chromium";v="135", "Not-A.Brand";v="8"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}


class AracApiError(Exception):
    """Raised when an ARAC API call fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def _clip(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _is_html_text(value: str) -> bool:
    # Keep this bounded for large upstream payloads.
    preview = value[:1200].lstrip()
    if not preview:
        return False
    head = preview[:400].lower()
    return (
        "<html" in head
        or "<!doctype html" in head
        or "<body" in head
        or "<head" in head
        or "<center" in head
    )


def _extract_error_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                if _is_html_text(text):
                    continue
                return text
    return None


def _direction_letter_from_route_code(route_code: str | None) -> str | None:
    if not route_code:
        return None
    parts = route_code.split("_")
    for part in parts:
        if part in {"G", "D"}:
            return part
    return None


class AracClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._base_url = settings.arac_base.rstrip("/")

    def _headers(self, session_id: str | None = None, session_key: str | None = None) -> dict[str, str]:
        headers = dict(_BASE_HEADERS)
        if session_id and session_key:
            headers["X-Session-Id"] = session_id
            headers["X-Session-Key"] = session_key
        return headers

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        raw_data: str | bytes | None = None,
        session_id: str | None = None,
        session_key: str | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        kwargs: dict[str, Any] = {
            "headers": self._headers(session_id, session_key),
            "timeout": aiohttp.ClientTimeout(total=30),
        }
        if json_body is not None:
            kwargs["json"] = json_body
        elif raw_data is not None:
            kwargs["data"] = raw_data

        try:
            async with self._session.request(method, url, **kwargs) as resp:
                status_code = resp.status
                ctype = resp.headers.get("content-type", "")
                text = await resp.text()

                payload: Any
                if "application/json" in ctype:
                    try:
                        payload = json.loads(text) if text else {}
                    except json.JSONDecodeError:
                        payload = {"_raw": _clip(text)}
                        if status_code < 400:
                            raise AracApiError(
                                f"ARAC {method} {path} returned malformed JSON",
                                status_code=status_code,
                                payload=payload,
                            )
                else:
                    payload = {"_raw": _clip(text)}
                    if status_code < 400:
                        raise AracApiError(
                            f"ARAC {method} {path} returned non-JSON content",
                            status_code=status_code,
                            payload=payload,
                        )

                if status_code >= 400:
                    detail = _extract_error_message(payload)
                    if not detail and isinstance(payload, dict):
                        raw_text = payload.get("_raw")
                        if isinstance(raw_text, str) and _is_html_text(raw_text):
                            detail = f"ARAC {method} {path} failed with status {status_code} (HTML error page)"
                    if not detail:
                        detail = f"ARAC {method} {path} failed with status {status_code}"
                    raise AracApiError(detail, status_code=status_code, payload=payload)
                return payload
        except AracApiError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AracApiError(f"ARAC {method} {path} failed: {exc}") from exc

    @staticmethod
    def _should_retry_captcha_fetch(exc: AracApiError) -> bool:
        code = exc.status_code
        if code is None:
            return True
        if code >= 500:
            return True
        if code in {404, 405, 415}:
            return True
        lowered = str(exc).lower()
        if "non-json content" in lowered or "malformed json" in lowered:
            return True
        if "html error page" in lowered:
            return True
        return False

    @staticmethod
    def _prepare_encryption_bundle(pubkey_b64: str) -> tuple[bytes, str]:
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            from cryptography.hazmat.primitives.serialization import load_der_public_key
        except ModuleNotFoundError as exc:
            raise AracApiError(
                "cryptography package is required for encrypted ARAC endpoints"
            ) from exc

        try:
            der = base64.b64decode(pubkey_b64)
            public_key = load_der_public_key(der)
            aes_key = AESGCM.generate_key(bit_length=256)
            encrypted = public_key.encrypt(
                aes_key,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
            return aes_key, base64.b64encode(encrypted).decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise AracApiError(f"Failed to prepare ARAC encryption bundle: {exc}") from exc

    @staticmethod
    def _decrypt_if_needed(aes_key: bytes, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        if "data" not in payload or "iv" not in payload:
            return payload
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ModuleNotFoundError as exc:
            raise AracApiError(
                "cryptography package is required for encrypted ARAC endpoints"
            ) from exc

        try:
            aesgcm = AESGCM(aes_key)
            cipher = base64.b64decode(payload["data"])
            iv = base64.b64decode(payload["iv"])
            plain = aesgcm.decrypt(iv, cipher, None)
            return json.loads(plain.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise AracApiError(f"Failed to decrypt ARAC response payload: {exc}") from exc

    async def _fetch_encrypted_task(
        self,
        path: str,
        *,
        session_id: str,
        session_key: str,
    ) -> Any:
        pubkey_payload = await self._request_json("GET", "/task/crypto/pubkey")
        if not isinstance(pubkey_payload, dict):
            raise AracApiError("ARAC /task/crypto/pubkey returned unexpected payload")
        pubkey = _as_text(pubkey_payload.get("key"))
        if not pubkey:
            raise AracApiError("ARAC /task/crypto/pubkey response missing key")

        aes_key, enc_key = self._prepare_encryption_bundle(pubkey)
        encrypted_response = await self._request_json(
            "POST",
            path,
            json_body={"encKey": enc_key},
            session_id=session_id,
            session_key=session_key,
        )
        return self._decrypt_if_needed(aes_key, encrypted_response)

    @staticmethod
    def _normalize_bus_position(item: dict[str, Any]) -> BusPosition | None:
        kapino = _as_text(item.get("vehicleDoorCode"))
        lat = _to_float(item.get("latitude"))
        lon = _to_float(item.get("longitude"))
        if not kapino or lat is None or lon is None:
            return None

        date_part = _as_text(item.get("lastLocationDate"))
        time_part = _as_text(item.get("lastLocationTime"))
        last_seen = " ".join(part for part in (date_part, time_part) if part)
        route_code = _as_text(item.get("lineCode")) or _as_text(item.get("routeCode"))

        try:
            return BusPosition(
                kapino=kapino,
                plate=_as_text(item.get("numberPlate")),
                latitude=lat,
                longitude=lon,
                speed=_to_int(item.get("speed")),
                operator=_as_text(item.get("operatorType")),
                last_seen=last_seen or "unknown",
                route_code=route_code,
                direction_letter=_direction_letter_from_route_code(route_code),
                operator_id=_to_int(item.get("operatorId")),
                operator_name=_as_text(item.get("operatorType")),
                vehicle_brand=_as_text(item.get("brandName")),
                model_year=_to_int(item.get("modelYear")),
                vehicle_type=_as_text(item.get("vehicleType")),
                seating_capacity=_to_int(item.get("seatingCapacity")),
                full_capacity=_to_int(item.get("fullCapacity")),
                accessible=_to_bool(item.get("accessibility")),
                has_usb=_to_bool(item.get("hasUsbCharger")),
                has_wifi=_to_bool(item.get("hasWifi")),
                has_bicycle_rack=_to_bool(item.get("hasBicycleRack")),
                is_air_conditioned=_to_bool(item.get("isAirConditioned")),
                garage_code=_as_text(item.get("garageCode")),
                garage_name=_as_text(item.get("garageName")),
                vehicle_software_version=_to_int(item.get("vehicleSoftwareVersion")),
            )
        except Exception:  # noqa: BLE001
            return None

    async def get_captcha(self) -> dict[str, str]:
        attempts: tuple[tuple[str, str, str | bytes | None], ...] = (
            ("POST", "/session/captcha", ""),
            ("POST", "/session/getpicture", ""),
            ("GET", "/session/captcha", None),
            ("GET", "/session/getpicture", None),
        )

        last_error: AracApiError | None = None
        for method, path, raw_data in attempts:
            try:
                payload = await self._request_json(method, path, raw_data=raw_data)
            except AracApiError as exc:
                last_error = exc
                if not self._should_retry_captcha_fetch(exc):
                    break
                continue

            if not isinstance(payload, dict):
                last_error = AracApiError(f"ARAC captcha response from {path} is not an object")
                continue

            captcha_id = _as_text(payload.get("captchaId"))
            captcha_image = _as_text(payload.get("captchaImage"))
            if captcha_id and captcha_image:
                return {"captchaId": captcha_id, "captchaImage": captcha_image}

            last_error = AracApiError(
                f"ARAC captcha response from {path} missing captchaId or captchaImage"
            )

        if last_error is not None:
            raise last_error
        raise AracApiError("ARAC captcha challenge could not be fetched")

    async def create_session(self, captcha_id: str, captcha_answer: str) -> dict[str, str]:
        payload = await self._request_json(
            "POST",
            "/session/create",
            json_body={
                "captchaId": captcha_id,
                "captchaAnswer": captcha_answer.strip().upper(),
            },
        )
        if not isinstance(payload, dict):
            raise AracApiError("ARAC session/create response is not an object")

        session_id = _as_text(payload.get("sessionId"))
        session_key = _as_text(payload.get("sessionKey"))
        if not session_id or not session_key:
            raise AracApiError("ARAC session/create response missing sessionId or sessionKey")
        return {"sessionId": session_id, "sessionKey": session_key}

    async def get_fleet(self, *, session_id: str, session_key: str) -> list[BusPosition]:
        payload = await self._fetch_encrypted_task(
            "/task/bus-fleet/buses",
            session_id=session_id,
            session_key=session_key,
        )
        if not isinstance(payload, list):
            raise AracApiError("ARAC fleet payload is not a list")

        buses: list[BusPosition] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_bus_position(item)
            if normalized is not None:
                buses.append(normalized)
        return buses

    async def get_vehicle(self, kapino: str, *, session_id: str, session_key: str) -> BusPosition:
        payload = await self._fetch_encrypted_task(
            f"/task/bus-fleet/buses/{kapino}",
            session_id=session_id,
            session_key=session_key,
        )
        raw: dict[str, Any] | None
        if isinstance(payload, dict):
            raw = payload
        elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
            raw = payload[0]
        else:
            raw = None

        if raw is None:
            raise AracApiError(
                f"ARAC vehicle payload for {kapino!r} is empty",
                status_code=404,
            )

        normalized = self._normalize_bus_position(raw)
        if normalized is None:
            raise AracApiError(
                f"ARAC vehicle payload for {kapino!r} could not be normalized",
                status_code=502,
            )
        return normalized

    async def get_missions(self, kapino: str, *, session_id: str, session_key: str) -> list[dict[str, Any]]:
        payload = await self._fetch_encrypted_task(
            f"/task/getCarTasks/{kapino}",
            session_id=session_id,
            session_key=session_key,
        )
        if not isinstance(payload, list):
            raise AracApiError("ARAC missions payload is not a list")
        return [item for item in payload if isinstance(item, dict)]

    async def get_route_stops(
        self,
        route_id: str,
        *,
        session_id: str,
        session_key: str,
    ) -> list[AracRouteStop]:
        payload = await self._fetch_encrypted_task(
            f"/task/route-stops/{route_id}",
            session_id=session_id,
            session_key=session_key,
        )
        if not isinstance(payload, list):
            raise AracApiError("ARAC route-stops payload is not a list")

        stops: list[AracRouteStop] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            stop_name = _as_text(item.get("stopName")) or ""
            stop_order = _to_int(item.get("stopOrder")) or (len(stops) + 1)
            stops.append(
                AracRouteStop(
                    stop_order=stop_order,
                    stop_id=_to_int(item.get("stopId")),
                    stop_name=stop_name,
                    latitude=_to_float(item.get("latitude")),
                    longitude=_to_float(item.get("longitude")),
                )
            )

        stops.sort(key=lambda s: s.stop_order)
        return stops
