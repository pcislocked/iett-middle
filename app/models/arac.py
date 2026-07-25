"""Pydantic models for ARAC session and task endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AracCaptchaResponse(BaseModel):
    captchaId: str
    captchaImageBase64: str
    suggestedAnswer: str | None = None


class AracSessionCreateRequest(BaseModel):
    captchaId: str
    captchaAnswer: str
    kapino: str


class AracSessionCreateResponse(BaseModel):
    sessionId: str
    sessionKey: str


class AracMissionItem(BaseModel):
    line_code: str | None = None
    first_stop: str | None = None
    departure_time: str | None = None
    state: str | None = None


class AracMissionSummary(BaseModel):
    mission_count: int
    completed_count: int
    pending_count: int
    distinct_line_codes: list[str] = Field(default_factory=list)


class AracMissionsResponse(BaseModel):
    kapino: str
    summary: AracMissionSummary
    missions: list[AracMissionItem] = Field(default_factory=list)
