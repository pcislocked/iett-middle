"""Pydantic response models for bus and arrival data."""
from __future__ import annotations

from pydantic import BaseModel


class BusPosition(BaseModel):
    kapino: str
    plate: str | None = None
    latitude: float
    longitude: float
    speed: int | None = None
    operator: str | None = None
    last_seen: str
    route_code: str | None = None
    route_name: str | None = None
    direction: str | None = None
    nearest_stop: str | None = None


class Arrival(BaseModel):
    route_code: str
    destination: str
    eta_minutes: int | None
    eta_raw: str
