"""ARAC router — /v1/arac (user-session-backed endpoints)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Path, Request

from app.deps import limiter
from app.models.arac import (
    AracCaptchaResponse,
    AracMissionItem,
    AracMissionsResponse,
    AracMissionSummary,
    AracSessionCreateRequest,
    AracSessionCreateResponse,
)
from app.models.bus import BusPosition
from app.services.arac_client import AracApiError, AracClient, solve_captcha_image

logger = logging.getLogger(__name__)

router = APIRouter()

# Store cookies for captcha flows (max 1000 items, TTL 10 minutes)
_captcha_cookies: TTLCache[str, dict[str, str]] = TTLCache(maxsize=1000, ttl=600)


def _status_from_arac_error(exc: AracApiError, fallback: int = 502) -> int:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status <= 599:
        return status
    return fallback


def _as_bool(val: Any) -> bool | None:
    from app.utils.coerce import _to_bool
    return _to_bool(val)


def _as_int(val: Any) -> int | None:
    from app.services.arac_client import _to_int
    return _to_int(val)


def _as_str(val: Any) -> str | None:
    from app.utils.coerce import _as_text
    return _as_text(val)


def _ms_to_iso(val: Any) -> str | None:
    if val is None:
        return None
    from datetime import datetime, timezone
    try:
        fval = float(val)
        if fval < 0 or fval > 1e15:
            return None
        ts = fval / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _require_arac_session_headers(
    request: Request | None = None,
    *,
    x_arac_session_id: str | None = None,
    x_arac_session_key: str | None = None,
    x_session_id: str | None = None,
    x_session_key: str | None = None,
) -> dict[str, str] | tuple[str, str]:
    if request is not None:
        session_key_raw = request.headers.get("X-Arac-Session-Key") or request.headers.get("X-Session-Key")
        if not session_key_raw:
            raise HTTPException(401, detail="Missing X-Arac-Session-Key header. Detail: X-Arac-Session-Id X-Arac-Session-Key X-Session-Id X-Session-Key")
        import json
        try:
            return json.loads(session_key_raw)
        except (ValueError, TypeError):
            raise HTTPException(400, detail="X-Arac-Session-Key is not valid JSON.")

    sid = x_arac_session_id or x_session_id
    skey = x_arac_session_key or x_session_key
    if not sid or not skey:
        raise HTTPException(401, detail="Missing X-Arac-Session-Id X-Arac-Session-Key X-Session-Id X-Session-Key")
    return (sid, skey)


@router.get("/session/captcha", response_model=AracCaptchaResponse)
@router.post("/session/captcha", response_model=AracCaptchaResponse)
@limiter.limit("60/minute")
async def get_arac_captcha(request: Request) -> AracCaptchaResponse:
    """Fetch a captcha challenge image.

    Returns captchaId, base64 image, and an OCR-suggested answer.
    The client should first try the suggestedAnswer automatically.
    If it fails, show the image to the user for manual entry.
    """
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as temp_session:
        client = AracClient(temp_session)
        try:
            captcha_data = await client.get_captcha()
        except AracApiError as exc:
            logger.warning("get_arac_captcha failed: %s", exc)
            status = _status_from_arac_error(exc, 502)
            detail_msg = str(exc) if status < 500 else "ARAC captcha servisine ulaşılamadı."
            raise HTTPException(status, detail=detail_msg) from exc

        captcha_id = captcha_data["captchaId"]
        captcha_image = captcha_data["captchaImage"]

        # Save cookies for later use in /session/create
        cookies_dict = {c.key: c.value for c in temp_session.cookie_jar}
        _captcha_cookies[captcha_id] = cookies_dict

    # Try OCR asynchronously in thread pool to prevent blocking asyncio loop
    suggested = await asyncio.to_thread(solve_captcha_image, captcha_image)

    return AracCaptchaResponse(
        captchaId=captcha_id,
        captchaImageBase64=captcha_image,
        suggestedAnswer=suggested,
    )

@router.post("/session/create", response_model=AracSessionCreateResponse)
@limiter.limit("60/minute")
async def create_arac_session(
    request: Request,
    payload: AracSessionCreateRequest,
) -> AracSessionCreateResponse:
    """Create an ARAC session by solving a captcha for a specific vehicle.

    Takes captchaId, captchaAnswer, and kapino.
    Backend fetches a fresh vehicle hash, submits the captcha, and if
    successful returns cookies as sessionKey for subsequent requests.
    """
    cookies_dict = _captcha_cookies.get(payload.captchaId)
    if cookies_dict is None:
        raise HTTPException(
            status_code=400,
            detail="Captcha session not found or expired. Please request a new captcha.",
        )

    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(
        connector=connector,
        cookies=cookies_dict,
    ) as temp_session:
        client = AracClient(temp_session)
        try:
            # Fetch fresh vehicle hash (dynamic, changes every time)
            vehicle_hash = await client.get_vehicle_hash(payload.kapino)

            # Submit captcha answer
            success = await client.submit_captcha(vehicle_hash, payload.captchaAnswer)
            if not success:
                raise AracApiError(
                    "Captcha doğrulanamadı. Kod yanlış olabilir.",
                    status_code=401,
                )

            # Remove used captcha cookies
            _captcha_cookies.pop(payload.captchaId, None)

            # Export session cookies as sessionKey for the client
            result_cookies = {c.key: c.value for c in temp_session.cookie_jar}

        except AracApiError as exc:
            logger.warning("create_arac_session failed: %s", exc)
            status = _status_from_arac_error(exc, 502)
            detail_msg = str(exc) if status < 500 else "ARAC servisine ulaşılamadı."
            raise HTTPException(status, detail=detail_msg) from exc

    import json
    return AracSessionCreateResponse(
        sessionId=payload.kapino,  # kapino as session identifier
        sessionKey=json.dumps(result_cookies),  # cookies as JSON string
    )

@router.get("/fleet/{kapino}/detail")
async def get_arac_bus_detail(
    request: Request,
    kapino: str = Path(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}$"),
) -> dict[str, Any]:
    """Get vehicle profile + missions in a single call.

    Requires X-Arac-Session-Key header with JSON-encoded cookies.
    """
    session_key_raw = request.headers.get("X-Arac-Session-Key")
    if not session_key_raw:
        raise HTTPException(401, detail="Missing X-Arac-Session-Key header.")

    import json as _json
    try:
        cookies_dict = _json.loads(session_key_raw)
    except (ValueError, TypeError):
        raise HTTPException(400, detail="X-Arac-Session-Key is not valid JSON.")

    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(
        connector=connector,
        cookies=cookies_dict,
    ) as temp_session:
        client = AracClient(temp_session)
        try:
            vehicle_hash = await client.get_vehicle_hash(kapino)
            detail = await client.get_detail(vehicle_hash)
        except AracApiError as exc:
            logger.warning("get_arac_bus_detail failed: %s", exc)
            status = _status_from_arac_error(exc, 502)
            detail_msg = str(exc) if status < 500 else "ARAC araç detay servisine ulaşılamadı."
            raise HTTPException(status, detail=detail_msg) from exc

    data_vehicle = detail.get("dataVehicle", {})
    data_task = detail.get("dataTask", [])

    profile = AracClient.normalize_bus_position(data_vehicle)
    missions = AracClient.normalize_missions(data_task)

    completed = sum(1 for m in missions if m.state == "T")
    pending = sum(1 for m in missions if m.state == "B")
    line_codes = sorted({m.line_code for m in missions if m.line_code})

    return {
        "profile": profile.model_dump(),
        "missions": AracMissionsResponse(
            kapino=kapino,
            summary=AracMissionSummary(
                mission_count=len(missions),
                completed_count=completed,
                pending_count=pending,
                distinct_line_codes=line_codes,
            ),
            missions=missions,
        ).model_dump(),
    }


@router.get("/fleet/{kapino}", response_model=BusPosition)
async def get_arac_bus(
    kapino: str = Path(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}$"),
    x_arac_session_key: str | None = None,
) -> BusPosition:
    """Get vehicle profile from ARAC API using stored session cookies."""
    raise HTTPException(501, detail="Use /fleet/{kapino}/detail instead")
