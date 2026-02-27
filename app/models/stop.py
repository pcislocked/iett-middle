"""Pydantic response models for stop data."""
from __future__ import annotations

from pydantic import BaseModel


class StopSearchResult(BaseModel):
    dcode: str
    name: str
    path: str | None = None


class RouteStop(BaseModel):
    route_code: str
    direction: str
    sequence: int
    stop_code: str
    stop_name: str
    latitude: float
    longitude: float
    district: str | None = None
