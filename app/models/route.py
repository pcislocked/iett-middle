"""Pydantic response models for route data."""
from __future__ import annotations

from pydantic import BaseModel


class ScheduledDeparture(BaseModel):
    route_code: str
    route_name: str
    route_variant: str
    direction: str
    day_type: str
    service_type: str
    departure_time: str


class Announcement(BaseModel):
    route_code: str
    route_name: str
    type: str
    updated_at: str
    message: str


class RouteMetadata(BaseModel):
    """One variant/direction of a route from GetAllRoute."""
    direction_name: str        # e.g. "YENİ CAMİİ - KADIKÖY"
    full_name: str             # e.g. "1991 - YENİ CAMİİ - KADIKÖY - Gidiş"
    variant_code: str          # e.g. "14M_G_D1991"
    direction: int             # 0 = outbound, 1 = return (GUZERGAH_YON)
    depar_no: int              # departure number


class RouteSearchResult(BaseModel):
    hat_kodu: str              # e.g. "500T"
    name: str                  # e.g. "TUZLA ŞİFA MAHALLESİ - 4. LEVENT METRO"
