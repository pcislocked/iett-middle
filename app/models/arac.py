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


class AracAutoSolveRequest(BaseModel):
    captchaId: str | None = None
    # 65,536 chars ≈ 48 KiB decoded — well above any real captcha image;
    # prevents DoS via oversized payloads fed into the OCR pipeline.
    captchaImageBase64: str | None = Field(default=None, max_length=65536)
    createSession: bool = False
    maxCandidates: int = Field(default=8, ge=1, le=20)


class AracAutoSolveResponse(BaseModel):
    captchaId: str
    captchaImageBase64: str
    solved: bool
    strategy: str
    candidatesTried: list[str] = Field(default_factory=list)
    selectedCandidate: str | None = None
    sessionId: str | None = None
    sessionKey: str | None = None
    error: str | None = None


class AracMissionItem(BaseModel):
    task_id: int | None = None
    line_code: str | None = None
    line_name: str | None = None
    route_code: str | None = None
    route_id: int | None = None
    route_direction: int | None = None
    task_status: int | None = None
    task_status_code: str | None = None
    approximate_start_time_ms: int | None = None
    approximate_end_time_ms: int | None = None
    approximate_start_time: str | None = None
    approximate_end_time: str | None = None
    gprs_active: bool | None = None
    is_active: bool | None = None


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
