"""Tests for the global notice background poller (app.services.notice_poller)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.cache import cache_get
from app.services.notice_poller import GLOBAL_NOTICES_CACHE_KEY, notice_poll_loop


@pytest.mark.asyncio
async def test_notice_poll_loop_fetches_and_caches() -> None:
    sample_notice = {
        "notice_noticeid": "N-101",
        "notice_title": "Test Notice",
        "notice_body": "Notice body content",
        "notice_starttime": 1700000000,
        "notice_endtime": 1800000000,
        "page_pageid": "P-1",
        "page_name": "Main",
        "notice_imageid": None,
    }

    with (
        patch("app.services.notice_poller.get_session"),
        patch(
            "app.services.notice_poller.get_global_notices",
            new_callable=AsyncMock,
            return_value=[sample_notice],
        ),
        patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]),
    ):
        with pytest.raises(asyncio.CancelledError):
            await notice_poll_loop()

    cached = await cache_get(GLOBAL_NOTICES_CACHE_KEY)
    assert cached is not None
    assert len(cached) == 1
    assert cached[0]["notice_title"] == "Test Notice"
