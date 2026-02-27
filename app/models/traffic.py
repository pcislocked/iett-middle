"""Pydantic response models for IBB traffic data."""
from __future__ import annotations

from pydantic import BaseModel


class TrafficSegment(BaseModel):
    segment_id: int
    speed_kmh: int
    congestion: int  # 1-7 scale (1=free, 6=no data, 7=closed)
    timestamp: str


class TrafficIndex(BaseModel):
    percent: int
    description: str
