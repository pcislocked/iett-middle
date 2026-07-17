"""ARAC router — /v1/arac (user-session-backed endpoints)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiohttp
from cachetools import TTLCache
from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request

from app.deps import get_session, limiter
from app.models.arac import (
    AracCaptchaResponse,
    AracMissionItem,
    AracMissionsResponse,
    AracMissionSummary,
    AracRouteStop,
    AracSessionCreateRequest,
    AracSessionCreateResponse,
)
from app.models.bus import BusPosition
from app.services.arac_client import AracApiError, AracClient
from app.utils.coerce import (
    _as_text as _as_str,
)
from app.utils.coerce import (
    _to_bool as _as_bool,
)
from app.utils.coerce import (
    _to_int as _as_int,
)

router = APIRouter()

# Store cookies for captcha flows (max 1000 items, TTL 10 minutes)
_captcha_cookies = TTLCache(maxsize=1000, ttl=600)


def _status_from_arac_error(exc: AracApiError, fallback: int = 502) -> int:
    code = exc.status_code if isinstance(exc.status_code, int) else fallback
    if 400 <= code <= 599:
        return code
    return fallback


def _ms_to_iso(value: int | None) -> str | None:
    if value is None or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _normalize_mission_item(raw: dict[str, Any]) -> AracMissionItem:
    task_start_ms = _as_int(raw.get("taskStartTime"))
    task_end_ms = _as_int(raw.get("taskEndTime"))
    task_coming_ms = _as_int(raw.get("taskComingTime"))
    approx_start_ms = _as_int(raw.get("approximateStartTime"))
    approx_end_ms = _as_int(raw.get("approximateEndTime"))
    last_location_ms = _as_int(raw.get("lastLocationTime"))
    updated_time_ms = _as_int(raw.get("updatedTime"))
    updated_start_ms = _as_int(raw.get("updatedStartTime"))
    sending_time_ms = _as_int(raw.get("sendingTime"))
    sending_time_old_ms = _as_int(raw.get("sendingTimeOld"))
    delivery_report_time_ms = _as_int(raw.get("deliveryReportTime"))

    return AracMissionItem(
        task_id=_as_int(raw.get("taskId")),
        archive_id=_as_int(raw.get("archiveId")),
        task_start_time_ms=task_start_ms,
        task_end_time_ms=task_end_ms,
        task_coming_time_ms=task_coming_ms,
        line_code=_as_str(raw.get("lineCode")),
        line_name=_as_str(raw.get("lineName")),
        route_code=_as_str(raw.get("routeCode")),
        route_id=_as_int(raw.get("routeId")),
        route_direction=_as_int(raw.get("routeDirection")),
        service_no=_as_int(raw.get("serviceNo")),
        driver_register_no=_as_str(raw.get("driverRegisterNo")),
        unread_message=_as_bool(raw.get("unreadMessage")),
        task_status=_as_int(raw.get("taskStatus")),
        task_status_code=_as_str(raw.get("taskStatusCode")),
        old_line_name=_as_str(raw.get("oldLineName")),
        superior_name=_as_str(raw.get("superiorName")),
        bus_door_number=_as_str(raw.get("busDoorNumber")),
        driver_id=_as_int(raw.get("driverId")),
        vehicle_id=_as_int(raw.get("vehicleId")),
        line_id=_as_int(raw.get("lineId")),
        justification_id=_as_int(raw.get("justificationId")),
        last_location_time_ms=last_location_ms,
        updated_by=_as_str(raw.get("updatedBy")),
        intervention_code=_as_str(raw.get("interventionCode")),
        note=_as_str(raw.get("note")),
        updated_time_ms=updated_time_ms,
        updated_start_time_ms=updated_start_ms,
        approximate_start_time_ms=approx_start_ms,
        approximate_end_time_ms=approx_end_ms,
        task_start_time=_ms_to_iso(task_start_ms),
        task_end_time=_ms_to_iso(task_end_ms),
        task_coming_time=_ms_to_iso(task_coming_ms),
        last_location_time=_ms_to_iso(last_location_ms),
        updated_time=_ms_to_iso(updated_time_ms),
        updated_start_time=_ms_to_iso(updated_start_ms),
        approximate_start_time=_ms_to_iso(approx_start_ms),
        approximate_end_time=_ms_to_iso(approx_end_ms),
        is_active=_as_bool(raw.get("isActive")),
        last_point_order_number=_as_int(raw.get("lastPointOrderNumber")),
        task_type_id=_as_int(raw.get("taskTypeId")),
        created_by=_as_int(raw.get("createdBy")),
        last_stop_passed_code=_as_str(raw.get("lastStopPassedCode")),
        last_stop_passed_name=_as_str(raw.get("lastStopPassedName")),
        stop_id=_as_int(raw.get("stopId")),
        stop_code=_as_str(raw.get("stopCode")),
        stop_name=_as_str(raw.get("stopName")),
        sending_time_ms=sending_time_ms,
        sending_time=_ms_to_iso(sending_time_ms),
        sending_time_old_ms=sending_time_old_ms,
        sending_time_old=_ms_to_iso(sending_time_old_ms),
        has_plan_sent=_as_bool(raw.get("hasPlanSent")),
        delivery_report_time_ms=delivery_report_time_ms,
        delivery_report_time=_ms_to_iso(delivery_report_time_ms),
        gprs_active=_as_bool(raw.get("gprsActive")),
    )


def _summarize_missions(missions: list[AracMissionItem]) -> AracMissionSummary:
    line_codes = sorted({m.line_code for m in missions if m.line_code})
    route_codes = sorted({m.route_code for m in missions if m.route_code})
    active_count = sum(1 for m in missions if m.is_active is True)
    return AracMissionSummary(
        mission_count=len(missions),
        active_count=active_count,
        distinct_line_codes=line_codes,
        distinct_route_codes=route_codes,
    )


def _require_arac_session_headers(
    x_arac_session_id: str | None = Header(default=None, alias="X-Arac-Session-Id"),
    x_arac_session_key: str | None = Header(default=None, alias="X-Arac-Session-Key"),
    x_session_id: str | None = Header(default=None, alias="X-Session-Id"),
    x_session_key: str | None = Header(default=None, alias="X-Session-Key"),
) -> tuple[str, str]:
    session_id = x_arac_session_id or x_session_id
    session_key = x_arac_session_key or x_session_key
    if not session_id or not session_key:
        raise HTTPException(
            401,
            detail=(
                "Missing ARAC session headers. Provide X-Arac-Session-Id and "
                "X-Arac-Session-Key (or legacy X-Session-Id and X-Session-Key)."
            ),
        )
    return session_id, session_key


@router.post("/session/captcha", response_model=AracCaptchaResponse)
@limiter.limit("15/minute")
async def get_arac_captcha(request: Request) -> AracCaptchaResponse:
    """Fetch a captcha challenge image to initialize an ARAC session.

    Returns a unique `captchaId` and a base64 encoded image string. The client must
    solve this captcha and pass the answer to the `/session/create` endpoint.
    """
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as temp_session:
        client = AracClient(temp_session)
        try:
            payload = await client.get_captcha()
        except AracApiError as exc:
            raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc

        captcha_id = payload["captchaId"]
        cookies_dict = {c.key: c.value for c in temp_session.cookie_jar}
        _captcha_cookies[captcha_id] = cookies_dict

    return AracCaptchaResponse(
        captchaId=captcha_id,
        captchaImageBase64=payload["captchaImage"],
    )


@router.post("/session/getpicture", response_model=AracCaptchaResponse)
@limiter.limit("15/minute")
async def get_arac_captcha_picture(request: Request) -> AracCaptchaResponse:
    """Alias for `/session/captcha` (Fetch Captcha).

    Kept for backward compatibility and client workflow clarity.
    """
    return await get_arac_captcha(request)


@router.post("/session/create", response_model=AracSessionCreateResponse)
@limiter.limit("15/minute")
async def create_arac_session(
    request: Request,
    payload: AracSessionCreateRequest,
) -> AracSessionCreateResponse:
    """Create a new ARAC session by submitting a solved captcha.

    Takes the `captchaId` and the user's `captchaAnswer`. If successful, returns
    a `sessionId` and `sessionKey` which are required in the headers of all
    subsequent authenticated ARAC requests.
    """
    if payload.captchaId not in _captcha_cookies:
        raise HTTPException(
            status_code=400,
            detail="Captcha session not found or expired. Please request a new captcha.",
        )
    cookies_dict = _captcha_cookies.get(payload.captchaId)
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(
        connector=connector,
        cookies=cookies_dict,  # pyright: ignore[reportArgumentType]
    ) as temp_session:
        client = AracClient(temp_session)
        try:
            session = await client.create_session(
                captcha_id=payload.captchaId,
                captcha_answer=payload.captchaAnswer,
            )
            # Remove cookies after successful use
            _captcha_cookies.pop(payload.captchaId, None)
        except AracApiError as exc:
            raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc

    return AracSessionCreateResponse(
        sessionId=session["sessionId"],
        sessionKey=session["sessionKey"],
    )


@router.post("/session/response", response_model=AracSessionCreateResponse)
@limiter.limit("15/minute")
async def respond_arac_captcha(
    request: Request,
    payload: AracSessionCreateRequest,
) -> AracSessionCreateResponse:
    """Alias for `/session/create` (Submit Captcha Answer).

    Kept for backward compatibility and client workflow clarity.
    """
    return await create_arac_session(request, payload)


@router.get("/fleet", response_model=list[BusPosition])
async def get_arac_fleet(
    credentials: tuple[str, str] = Depends(_require_arac_session_headers),
) -> list[BusPosition]:
    """Get a complete snapshot of the fleet from the authenticated ARAC API.

    Requires active ARAC session credentials in the headers (`X-Arac-Session-Id` and `X-Arac-Session-Key`).
    """
    session_id, session_key = credentials
    client = AracClient(get_session())
    try:
        return await client.get_fleet(session_id=session_id, session_key=session_key)
    except AracApiError as exc:
        raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc


@router.get("/fleet/{kapino}", response_model=BusPosition)
async def get_arac_bus(
    kapino: str = Path(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}$"),
    credentials: tuple[str, str] = Depends(_require_arac_session_headers),
) -> BusPosition:
    """Get the live profile and position of a single bus from the ARAC API.

    Requires active ARAC session credentials in the headers (`X-Arac-Session-Id` and `X-Arac-Session-Key`).
    """
    session_id, session_key = credentials
    client = AracClient(get_session())
    try:
        return await client.get_vehicle(
            kapino, session_id=session_id, session_key=session_key
        )
    except AracApiError as exc:
        raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc


@router.get("/fleet/{kapino}/missions", response_model=AracMissionsResponse)
async def get_arac_missions(
    kapino: str = Path(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}$"),
    credentials: tuple[str, str] = Depends(_require_arac_session_headers),
) -> AracMissionsResponse:
    """Get the daily mission timeline (assignments and route history) for a specific bus.

    Requires active ARAC session credentials in the headers (`X-Arac-Session-Id` and `X-Arac-Session-Key`).
    """
    session_id, session_key = credentials
    client = AracClient(get_session())
    try:
        raw_missions = await client.get_missions(
            kapino, session_id=session_id, session_key=session_key
        )
    except AracApiError as exc:
        raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc

    missions = [_normalize_mission_item(raw) for raw in raw_missions]

    return AracMissionsResponse(
        kapino=kapino,
        summary=_summarize_missions(missions),
        missions=missions,
    )


@router.get("/routes/{route_id}/stops", response_model=list[AracRouteStop])
async def get_arac_route_stops(
    route_id: str = Path(..., pattern=r"^\d{1,10}$"),
    credentials: tuple[str, str] = Depends(_require_arac_session_headers),
) -> list[AracRouteStop]:
    """Get the ordered list of stops for a specific route ID from the ARAC API.

    Note that `route_id` is the internal numeric ID (e.g., 1234), not the hat_kodu (e.g., 500T).
    Requires active ARAC session credentials in the headers (`X-Arac-Session-Id` and `X-Arac-Session-Key`).
    """
    session_id, session_key = credentials
    client = AracClient(get_session())
    try:
        return await client.get_route_stops(
            route_id, session_id=session_id, session_key=session_key
        )
    except AracApiError as exc:
        raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc
