"""Pydantic model for IETT bus garages."""
from __future__ import annotations

from pydantic import BaseModel


class Garage(BaseModel):
    code: str | None = None
    name: str
    latitude: float
    longitude: float
