"""ARAC router — /v1/arac (user-session-backed endpoints)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.deps import limiter
from app.models.arac import (
    AracCaptchaResponse,
    AracMissionsResponse,
    AracMissionSummary,
    AracSessionCreateRequest,
    AracSessionCreateResponse,
)
from app.models.bus import BusPosition
from app.services.arac_client import AracApiError, AracClient, solve_captcha_image
from app.services.cache import cache_get_or_fetch

logger = logging.getLogger(__name__)

router = APIRouter()

# Store cookies for captcha flows (max 1000 items, TTL 10 minutes)
_captcha_cookies = TTLCache[str, dict[str, str]](maxsize=1000, ttl=600)


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
        session_key_raw = request.headers.get("X-Arac-Session-Key") or request.headers.get(
            "X-Session-Key"
        )
        if not session_key_raw:
            raise HTTPException(
                401,
                detail="Missing X-Arac-Session-Key header. Detail: X-Arac-Session-Id X-Arac-Session-Key X-Session-Id X-Session-Key",
            )
        import json

        try:
            parsed = json.loads(session_key_raw)
            if not isinstance(parsed, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
            ):
                raise ValueError("Cookies must be a flat string-to-string dictionary")
            return parsed
        except (ValueError, TypeError):
            raise HTTPException(400, detail="X-Arac-Session-Key is not valid JSON.")

    sid = x_arac_session_id or x_session_id
    skey = x_arac_session_key or x_session_key
    if not sid or not skey:
        raise HTTPException(
            401, detail="Missing X-Arac-Session-Id X-Arac-Session-Key X-Session-Id X-Session-Key"
        )
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
        result_cookies: dict[str, str] = {}
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

            # Export session cookies as sessionKey for the client
            result_cookies = {c.key: c.value for c in temp_session.cookie_jar}

        except AracApiError as exc:
            logger.warning("create_arac_session failed: %s", exc)
            status = _status_from_arac_error(exc, 502)
            detail_msg = str(exc) if status < 500 else "ARAC servisine ulaşılamadı."
            raise HTTPException(status, detail=detail_msg) from exc
        finally:
            _captcha_cookies.pop(payload.captchaId, None)

    import json

    return AracSessionCreateResponse(
        sessionId=payload.kapino,  # kapino as session identifier
        sessionKey=json.dumps(result_cookies),  # cookies as JSON string
    )


@router.get("/suggest")
@limiter.limit("30/minute")
async def suggest_arac_bus(
    request: Request,
    q: str = Query(..., min_length=2, max_length=30),
) -> list[dict[str, str]]:
    """Suggest vehicles by Kapı No or Plaka via ARAC API with persistent cache fallback."""
    from app.services import vehicle_cache

    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as temp_session:
        client = AracClient(temp_session)
        try:
            payload = await client._post_json(
                "/Home/GetAllVehicleSelectList",
                {
                    "page": 1,
                    "pageSize": 20,
                    "search": q,
                },
            )
            if isinstance(payload, dict) and payload.get("isSuccess"):
                items = payload.get("data", [])
                if isinstance(items, list):
                    loop = asyncio.get_running_loop()
                    loop.run_in_executor(None, vehicle_cache.upsert_vehicles_batch, items)
                    return [
                        {
                            "doorNumber": str(item.get("doorNumber", "")),
                            "plate": str(item.get("plate", "")),
                        }
                        for item in items
                        if isinstance(item, dict)
                    ]
        except AracApiError:
            pass

    # Fallback to local persistent cache if upstream fails or returns empty
    cached_hits = vehicle_cache.search_vehicles(q, limit=20)
    return [
        {
            "doorNumber": str(c.get("door_number", "")),
            "plate": str(c.get("plate", "")),
        }
        for c in cached_hits
    ]


@router.get("/fleet/{kapino}/auto-detail")
@limiter.limit("10/minute")
async def get_arac_bus_auto_detail(
    request: Request,
    kapino: str = Path(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}$"),
) -> dict[str, Any]:
    """Automatically fetch vehicle profile + missions by auto-solving the captcha."""
    key = f"arac:autodetail:{kapino.upper()}"

    async def _fetch():
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        async with aiohttp.ClientSession(connector=connector) as temp_session:
            client = AracClient(temp_session)
            try:
                # 1. Get Captcha
                captcha_data = await client.get_captcha()
                captcha_image = captcha_data.get("captchaImage") or captcha_data.get("image")
                if not captcha_image:
                    raise HTTPException(502, detail="No captcha image returned")

                # 2. Solve Captcha
                suggested_answer = await asyncio.to_thread(solve_captcha_image, captcha_image)
                if not suggested_answer:
                    raise HTTPException(502, detail="Auto-captcha solve failed")

                # 3. Create Session (Submit Captcha)
                captcha_vehicle_hash = await client.get_vehicle_hash(kapino)
                success = await client.submit_captcha(captcha_vehicle_hash, suggested_answer)
                if not success:
                    raise HTTPException(502, detail="Auto-captcha failed")

                # 4. Get Detail (Requires a fresh single-use vehicle_hash!)
                detail_vehicle_hash = await client.get_vehicle_hash(kapino)
                detail = await client.get_detail(detail_vehicle_hash)

                data_vehicle = detail.get("dataVehicle", {})
                data_task = detail.get("dataTask", [])

                profile = AracClient.normalize_bus_position(data_vehicle)
                missions = AracClient.normalize_missions(data_task)

                completed = sum(1 for m in missions if m.state == "T")
                pending = sum(1 for m in missions if m.state == "B")
                line_codes = sorted({m.line_code for m in missions if m.line_code})

                active_route = None
                if data_task and len(data_task) > 0:
                    active_route = str(data_task[0].get("lineCode", "")) or None
                profile.route_code = active_route

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
            except AracApiError as exc:
                logger.warning("get_arac_bus_auto_detail failed: %s", exc)
                status = _status_from_arac_error(exc, 502)
                detail_msg = (
                    str(exc) if status < 500 else "ARAC otomatik detay servisine ulaşılamadı."
                )
                raise HTTPException(status, detail=detail_msg) from exc

    return await cache_get_or_fetch(key, 30, _fetch, stale_ttl=60)


@router.get("/fleet/{kapino}/detail")
@limiter.limit("30/minute")
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
        parsed = _json.loads(session_key_raw)
        if not isinstance(parsed, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()
        ):
            raise ValueError("Cookies must be a flat string-to-string dictionary")
        cookies_dict: dict[str, str] = parsed
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, detail="X-Arac-Session-Key is not valid JSON.") from exc

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

    # Find active route code (fallback to first task's line_code)
    active_route = None
    if data_task and len(data_task) > 0:
        active_route = str(data_task[0].get("lineCode", "")) or None

    profile.route_code = active_route

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
