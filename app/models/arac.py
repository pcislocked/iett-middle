"""Pydantic models for ARAC session and encrypted-task endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AracCaptchaResponse(BaseModel):
    captchaId: str
    captchaImageBase64: str


class AracSessionCreateRequest(BaseModel):
    captchaId: str
    captchaAnswer: str


class AracSessionCreateResponse(BaseModel):
    sessionId: str
    sessionKey: str


class AracMissionItem(BaseModel):
    task_id: int | None = None
    archive_id: int | None = None
    task_start_time_ms: int | None = None
    task_end_time_ms: int | None = None
    task_coming_time_ms: int | None = None
    line_code: str | None = None
    line_name: str | None = None
    route_code: str | None = None
    route_id: int | None = None
    route_direction: int | None = None
    service_no: int | None = None
    driver_register_no: str | None = None
    unread_message: bool | None = None
    task_status: int | None = None
    task_status_code: str | None = None
    old_line_name: str | None = None
    superior_name: str | None = None
    bus_door_number: str | None = None
    driver_id: int | None = None
    vehicle_id: int | None = None
    line_id: int | None = None
    justification_id: int | None = None
    last_location_time_ms: int | None = None
    updated_by: str | None = None
    intervention_code: str | None = None
    note: str | None = None
    updated_time_ms: int | None = None
    updated_start_time_ms: int | None = None
    approximate_start_time_ms: int | None = None
    approximate_end_time_ms: int | None = None
    task_start_time: str | None = None
    task_end_time: str | None = None
    task_coming_time: str | None = None
    last_location_time: str | None = None
    updated_time: str | None = None
    updated_start_time: str | None = None
    approximate_start_time: str | None = None
    approximate_end_time: str | None = None
    is_active: bool | None = None
    last_point_order_number: int | None = None
    task_type_id: int | None = None
    created_by: int | None = None
    last_stop_passed_code: str | None = None
    last_stop_passed_name: str | None = None
    stop_id: int | None = None
    stop_code: str | None = None
    stop_name: str | None = None
    sending_time_ms: int | None = None
    sending_time: str | None = None
    sending_time_old_ms: int | None = None
    sending_time_old: str | None = None
    has_plan_sent: bool | None = None
    delivery_report_time_ms: int | None = None
    delivery_report_time: str | None = None
    gprs_active: bool | None = None


class AracMissionSummary(BaseModel):
    mission_count: int
    active_count: int
    distinct_line_codes: list[str] = Field(default_factory=list)
    distinct_route_codes: list[str] = Field(default_factory=list)


class AracMissionsResponse(BaseModel):
    kapino: str
    summary: AracMissionSummary
    missions: list[AracMissionItem] = Field(default_factory=list)


class AracRouteStop(BaseModel):
    stop_order: int
    stop_id: int | None = None
    stop_name: str
    latitude: float | None = None
    longitude: float | None = None
