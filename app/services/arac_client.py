"""ARAC API client for the new arac.iett.gov.tr portal.

Uses ASP.NET session cookies and per-vehicle captcha verification.
Captcha images are solved automatically via ddddocr with manual fallback.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

import aiohttp

from app.models.arac import AracMissionItem
from app.models.bus import BusPosition
from app.utils.coerce import _as_text, _to_bool, _to_float

logger = logging.getLogger(__name__)

_ARAC_BASE = "https://arac.iett.gov.tr"


def _clip(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _to_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        if isinstance(val, (int, float)):
            if abs(val) > 1e300:
                return None
            return int(val)
        s = str(val).strip()
        if not s:
            return None
        res = int(float(s))
        if abs(res) > 1e300:
            return None
        return res
    except (ValueError, TypeError, OverflowError):
        return None


def _is_html_text(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith("<!doctype html") or "<html" in t or "<head" in t or "<body" in t


def _extract_error_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for k in ("message", "Message", "error", "Error", "detail", "Detail"):
            v = payload.get(k)
            if isinstance(v, str) and v.strip() and not _is_html_text(v):
                return v.strip()
    elif isinstance(payload, str) and payload.strip() and not _is_html_text(payload):
        return payload.strip()[:200]
    return None


def _direction_letter_from_route_code(route_code: str | None) -> str | None:
    if not route_code:
        return None
    parts = route_code.split("_")
    if len(parts) >= 2 and parts[1].upper() in ("G", "D"):
        return parts[1].upper()
    return None

_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://arac.iett.gov.tr",
    "Referer": "https://arac.iett.gov.tr/",
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


# ── OCR singleton ───────────────────────────────────────────────────────────

_ocr_instance = None


def _get_ocr():
    """Lazy-load ddddocr. Model is loaded once and reused."""
    global _ocr_instance
    if _ocr_instance is None:
        import ddddocr
        _ocr_instance = ddddocr.DdddOcr(show_ad=False)
    return _ocr_instance


def solve_captcha_image(image_base64: str) -> str | None:
    """Attempt to OCR a base64-encoded captcha image.

    Returns the recognized text, or None if OCR fails.
    """
    try:
        if "," in image_base64:
            image_base64 = image_base64.split(",", 1)[1]
        img_bytes = base64.b64decode(image_base64)
        res = _get_ocr().classification(img_bytes)
        return res if res else None
    except Exception:  # noqa: BLE001
        logger.warning("ddddocr captcha solve failed", exc_info=True)
        return None


# ── Client ──────────────────────────────────────────────────────────────────

class AracClient:
    """Client for the new arac.iett.gov.tr API."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def _post_json(self, path: str, body: dict | None = None) -> Any:
        """POST to arac.iett.gov.tr and return parsed JSON."""
        url = f"{_ARAC_BASE}{path}"
        kwargs: dict[str, Any] = {
            "headers": dict(_BASE_HEADERS),
            "timeout": aiohttp.ClientTimeout(total=30),
        }
        if body is not None:
            kwargs["json"] = body
        else:
            kwargs["data"] = ""

        async with self._session.post(url, **kwargs) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise AracApiError(
                    f"ARAC {path} failed with status {resp.status}",
                    status_code=resp.status,
                    payload=text[:500],
                )
            ctype = resp.headers.get("content-type", "")
            if "application/json" not in ctype:
                raise AracApiError(
                    f"ARAC {path} returned non-JSON (status {resp.status})",
                    status_code=resp.status,
                    payload=text[:500],
                )
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise AracApiError(
                    f"ARAC {path} returned malformed JSON",
                    status_code=resp.status,
                ) from exc

    async def get_vehicle_hash(self, kapino: str) -> str:
        """Fetch the dynamic encrypted hash for a door number.

        This hash changes on every request. Do NOT cache it.
        """
        payload = await self._post_json("/Home/GetAllVehicleSelectList", {
            "page": 1,
            "pageSize": 10,
            "search": kapino,
        })
        if not isinstance(payload, dict) or not payload.get("isSuccess"):
            msg = f"GetAllVehicleSelectList failed for {kapino}"
            status_code = 502
            if isinstance(payload, dict):
                msg = payload.get("message") or msg
                if "çok fazla" in msg.lower():
                    status_code = 429
            raise AracApiError(
                msg,
                status_code=status_code,
                payload=payload,
            )
        items = payload.get("data", [])
        for item in items:
            if isinstance(item, dict) and item.get("doorNumber") == kapino:
                value = item.get("value")
                if value:
                    return value
        raise AracApiError(
            f"Vehicle hash not found for {kapino}",
            status_code=404,
        )

    async def get_captcha(self) -> dict[str, str]:
        """Fetch a captcha image. Returns captchaId and base64 image.

        The session cookies (.AspNetCore.Session etc.) are stored in
        self._session.cookie_jar automatically by aiohttp.
        """
        payload = await self._post_json("/Home/Captcha")
        if not isinstance(payload, dict) or not payload.get("isSuccess"):
            raise AracApiError("Captcha fetch failed", payload=payload)

        captcha_image = payload.get("captchaImage", "")
        if not captcha_image:
            raise AracApiError("Captcha response missing captchaImage")

        # Generate a captchaId from the session cookie
        cookies = self._session.cookie_jar.filter_cookies(
            aiohttp.client.URL(_ARAC_BASE)
        )
        captcha_session_key = ""
        for key, cookie in cookies.items():
            if key == "Captcha_Session_Key":
                captcha_session_key = cookie.value
                break

        captcha_id = captcha_session_key or "auto"

        return {
            "captchaId": captcha_id,
            "captchaImage": captcha_image,
        }

    async def submit_captcha(
        self, vehicle_hash: str, captcha_text: str
    ) -> bool:
        """Submit captcha answer with the vehicle hash to /Home/Control.

        Returns True if verification succeeded.
        """
        payload = await self._post_json("/Home/Control", {
            "DoorNumber": vehicle_hash,
            "CaptchaText": captcha_text,
        })
        if isinstance(payload, dict) and payload.get("isSuccess"):
            return True
        return False

    async def get_detail(self, vehicle_hash: str) -> dict:
        """Fetch vehicle details + missions via /Home/GetDetail.

        Must be called after a successful submit_captcha with the same
        session cookies. Requires a FRESH vehicle_hash (call
        get_vehicle_hash again — hashes are single-use/dynamic).
        """
        payload = await self._post_json("/Home/GetDetail", {
            "DoorNumber": vehicle_hash,
        })
        if not isinstance(payload, dict) or not payload.get("isSuccess"):
            msg = "GetDetail failed"
            if isinstance(payload, dict):
                msg = payload.get("message", msg)
            raise AracApiError(msg, status_code=401, payload=payload)
        return payload

    @staticmethod
    def normalize_bus_position(data_vehicle: dict) -> BusPosition:
        """Convert dataVehicle from GetDetail into a BusPosition."""
        kapino = _as_text(data_vehicle.get("vehicleDoorCode")) or "?"
        lat = _to_float(data_vehicle.get("latitude")) or 0.0
        lon = _to_float(data_vehicle.get("longitude")) or 0.0

        date_part = _as_text(data_vehicle.get("lastLocationDate")) or ""
        time_part = _as_text(data_vehicle.get("lastLocationTime")) or ""
        last_seen = f"{date_part} {time_part}".strip() or "unknown"

        return BusPosition(
            kapino=kapino,
            plate=_as_text(data_vehicle.get("numberPlate")),
            latitude=lat,
            longitude=lon,
            speed=None,  # Yeni API'de speed yok
            operator=_as_text(data_vehicle.get("operatorType")),
            last_seen=last_seen,
            route_code=None,  # Yeni API'de route_code yok
            operator_name=_as_text(data_vehicle.get("operatorType")),
            accessible=_to_bool(data_vehicle.get("accessibility")),
            has_usb=_to_bool(data_vehicle.get("hasUsbCharger")),
            has_wifi=_to_bool(data_vehicle.get("hasWifi")),
            has_bicycle_rack=_to_bool(data_vehicle.get("hasBicycleRack")),
            is_air_conditioned=_to_bool(data_vehicle.get("isAirConditioned")),
            # Aşağıdaki alanlar yeni API'de mevcut değil:
            vehicle_brand=None,
            model_year=None,
            vehicle_type=None,
            seating_capacity=None,
            full_capacity=None,
            garage_code=None,
            garage_name=None,
            vehicle_software_version=None,
        )

    @staticmethod
    def normalize_missions(data_task: list) -> list[AracMissionItem]:
        """Convert dataTask array from GetDetail into AracMissionItem list."""
        missions: list[AracMissionItem] = []
        for item in data_task:
            if not isinstance(item, dict):
                continue
            missions.append(AracMissionItem(
                line_code=_as_text(item.get("lineCode")),
                first_stop=_as_text(item.get("firstStop")),
                departure_time=_as_text(item.get("orer")),
                state=_as_text(item.get("state")),
            ))
        return missions
