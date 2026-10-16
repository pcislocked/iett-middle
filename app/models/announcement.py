"""Pydantic response models for official IETT announcements."""

from __future__ import annotations

from pydantic import BaseModel


class OfficialAnnouncementItem(BaseModel):
    id: str
    title: str
    date: str
    url: str


class OfficialAnnouncementsResponse(BaseModel):
    page: int
    total_items: int
    items: list[OfficialAnnouncementItem]
