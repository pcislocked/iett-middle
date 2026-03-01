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
    direction: str | None = None          # terminal name, e.g. "YENİ CAMİİ"
    direction_letter: str | None = None   # "G" or "D", derived from guzergahkodu
    nearest_stop: str | None = None
    stop_sequence: int | None = None      # current stop index along the route


class BusPositionWithTrail(BusPosition):
    """BusPosition extended with a rolling position history."""
    trail: list[TrailPoint] = []


class BusDetail(BusPositionWithTrail):
    """BusPositionWithTrail with resolved route code and stop list for map rendering.

    ``resolved_route_code`` prefers the live ``route_code``; falls back to the
    last known route seen for this kapino since server startup (covers parked
    / nightly-service buses whose route_code returns to None between trips).
    ``route_stops`` is the ordered stop list for both directions — client draws
    the direction that matches ``direction_letter``.
    """
    resolved_route_code: str | None = None
    route_stops: list[dict] = []


class Arrival(BaseModel):
    route_code: str
    destination: str
    eta_minutes: int | None
    eta_raw: str
    plate: str | None = None
    kapino: str | None = None
    # Live position from ybs response
    lat: float | None = None
    lon: float | None = None
    speed_kmh: int | None = None
    last_seen_ts: str | None = None
    # Nested amenity flags — None means data unavailable for this source
    amenities: dict | None = None
