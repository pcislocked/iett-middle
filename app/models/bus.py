"""Pydantic response models for bus and arrival data."""
from __future__ import annotations

from pydantic import BaseModel


class TrailPoint(BaseModel):
    lat: float
    lon: float
    ts: str  # value of last_seen at that snapshot


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


class BusPositionWithTrail(BusPosition):
    """BusPosition extended with a rolling position history."""
    trail: list[TrailPoint] = []


class Arrival(BaseModel):
    route_code: str
    destination: str
    eta_minutes: int | None
    eta_raw: str
    plate: str | None = None
    kapino: str | None = None
    # Live position from ybs response (son_konum = "lon,lat" string)
    lat: float | None = None
    lon: float | None = None
    speed: int | None = None
    last_seen_ts: str | None = None
    # Amenity flags (1 = available)
    usb: int | None = None
    wifi: int | None = None
    klima: int | None = None
    engelli: int | None = None
