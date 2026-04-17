"""ARAC router — /v1/arac (user-session-backed endpoints)."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from fastapi.concurrency import run_in_threadpool

from app.deps import get_session
from app.models.arac import (
    AracAutoSolveRequest,
    AracAutoSolveResponse,
    AracCaptchaResponse,
    AracMissionItem,
    AracMissionsResponse,
    AracMissionSummary,
    AracRouteStop,
    AracSessionCreateRequest,
    AracSessionCreateResponse,
)
from app.models.bus import BusPosition
from app.services.arac_captcha_solver import collect_captcha_candidates_from_base64
from app.services.arac_client import AracApiError, AracClient

router = APIRouter()

_OCR_SOLVER_TIMEOUT_SECONDS = 8.0


def _status_from_arac_error(exc: AracApiError, fallback: int = 502) -> int:
    code = exc.status_code if isinstance(exc.status_code, int) else fallback
    if 400 <= code <= 599:
        return code
    return fallback


def _as_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return None


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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
            detail="Missing ARAC session headers: X-Arac-Session-Id and X-Arac-Session-Key",
        )
    return session_id, session_key


@router.post("/session/captcha", response_model=AracCaptchaResponse)
async def get_arac_captcha() -> AracCaptchaResponse:
    client = AracClient(get_session())
    try:
        payload = await client.get_captcha()
    except AracApiError as exc:
        raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc

    return AracCaptchaResponse(
        captchaId=payload["captchaId"],
        captchaImageBase64=payload["captchaImage"],
    )


@router.post("/session/getpicture", response_model=AracCaptchaResponse)
async def get_arac_captcha_picture() -> AracCaptchaResponse:
    """Alias for captcha challenge fetch, kept for client workflow clarity."""
    return await get_arac_captcha()


@router.post("/session/create", response_model=AracSessionCreateResponse)
async def create_arac_session(payload: AracSessionCreateRequest) -> AracSessionCreateResponse:
    client = AracClient(get_session())
    try:
        session = await client.create_session(
            captcha_id=payload.captchaId,
            captcha_answer=payload.captchaAnswer,
        )
    except AracApiError as exc:
        raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc

    return AracSessionCreateResponse(
        sessionId=session["sessionId"],
        sessionKey=session["sessionKey"],
    )


@router.post("/session/response", response_model=AracSessionCreateResponse)
async def respond_arac_captcha(payload: AracSessionCreateRequest) -> AracSessionCreateResponse:
    """Alias for captcha response submission endpoint."""
    return await create_arac_session(payload)


@router.post("/session/auto-solve", response_model=AracAutoSolveResponse)
async def auto_solve_arac_session(payload: AracAutoSolveRequest) -> AracAutoSolveResponse:
    client = AracClient(get_session())

    captcha_id = (payload.captchaId or "").strip()
    captcha_image = (payload.captchaImageBase64 or "").strip()

    if not captcha_id or not captcha_image:
        try:
            challenge = await client.get_captcha()
        except AracApiError as exc:
            raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc
        captcha_id = challenge["captchaId"]
        captcha_image = challenge["captchaImage"]

    try:
        candidates = await asyncio.wait_for(
            run_in_threadpool(
                collect_captcha_candidates_from_base64,
                captcha_image,
                max_candidates=payload.maxCandidates,
            ),
            timeout=_OCR_SOLVER_TIMEOUT_SECONDS,
        )
    except TimeoutError as exc:
        raise HTTPException(503, detail="Captcha auto-solve timed out") from exc
    except AracApiError as exc:
        raise HTTPException(_status_from_arac_error(exc, fallback=503), detail=str(exc)) from exc

    if not candidates:
        return AracAutoSolveResponse(
            captchaId=captcha_id,
            captchaImageBase64=captcha_image,
            solved=False,
            strategy="ocr-candidates",
            candidatesTried=[],
            selectedCandidate=None,
            sessionId=None,
            sessionKey=None,
            error="No 4-character captcha candidates generated",
        )

    attempted: list[str] = []
    if not payload.createSession:
        return AracAutoSolveResponse(
            captchaId=captcha_id,
            captchaImageBase64=captcha_image,
            solved=False,
            strategy="ocr-candidates",
            candidatesTried=[],
            selectedCandidate=candidates[0],
            sessionId=None,
            sessionKey=None,
            error="createSession=false; returning best candidate only",
        )

    last_error: str | None = None
    for candidate in candidates:
        attempted.append(candidate)
        try:
            session = await client.create_session(captcha_id=captcha_id, captcha_answer=candidate)
            return AracAutoSolveResponse(
                captchaId=captcha_id,
                captchaImageBase64=captcha_image,
                solved=True,
                strategy="ocr-candidates",
                candidatesTried=attempted,
                selectedCandidate=candidate,
                sessionId=session["sessionId"],
                sessionKey=session["sessionKey"],
                error=None,
            )
        except AracApiError as exc:
            last_error = str(exc)
            if exc.status_code and exc.status_code >= 500:
                break

    return AracAutoSolveResponse(
        captchaId=captcha_id,
        captchaImageBase64=captcha_image,
        solved=False,
        strategy="ocr-candidates",
        candidatesTried=attempted,
        selectedCandidate=None,
        sessionId=None,
        sessionKey=None,
        error=last_error or "Auto-solve candidates failed",
    )


@router.get("/fleet", response_model=list[BusPosition])
async def get_arac_fleet(
    credentials: tuple[str, str] = Depends(_require_arac_session_headers),
) -> list[BusPosition]:
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
    session_id, session_key = credentials
    client = AracClient(get_session())
    try:
        return await client.get_vehicle(kapino, session_id=session_id, session_key=session_key)
    except AracApiError as exc:
        raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc


@router.get("/fleet/{kapino}/missions", response_model=AracMissionsResponse)
async def get_arac_missions(
    kapino: str = Path(..., pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,39}$"),
    credentials: tuple[str, str] = Depends(_require_arac_session_headers),
) -> AracMissionsResponse:
    session_id, session_key = credentials
    client = AracClient(get_session())
    try:
        raw_missions = await client.get_missions(kapino, session_id=session_id, session_key=session_key)
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
    session_id, session_key = credentials
    client = AracClient(get_session())
    try:
        return await client.get_route_stops(route_id, session_id=session_id, session_key=session_key)
    except AracApiError as exc:
        raise HTTPException(_status_from_arac_error(exc), detail=str(exc)) from exc
