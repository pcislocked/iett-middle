"""Pydantic response models for stop data."""
from __future__ import annotations

from pydantic import BaseModel


class StopSearchResult(BaseModel):
    dcode: str
    name: str
    path: str | None = None


class StopDetail(BaseModel):
    """Stop name + optional coordinates (for map pin)."""
    dcode: str
    name: str
    latitude: float | None = None
    longitude: float | None = None
    direction: str | None = None


class RouteStop(BaseModel):
    route_code: str
    direction: str
    sequence: int
    stop_code: str
    stop_name: str
    latitude: float | None = None
    longitude: float | None = None
    district: str | None = None


class NearbyStop(BaseModel):
    """A stop with its distance from a query point."""
    stop_code: str
    stop_name: str
    latitude: float
    longitude: float
    district: str | None = None
    direction: str | None = None
    distance_m: float = 0.0
