"""Tests for app.services.fleet_poller."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.fleet_poller import refresh_fleet_forever, refresh_fleet_once
from app.services.iett_client import IettApiError


class TestRefreshFleetOnce:
    async def test_success_updates_fleet(self) -> None:
        mock_buses = [MagicMock(), MagicMock()]
        mock_client = AsyncMock()
        mock_client.get_all_buses.return_value = mock_buses

        with (
            patch("app.deps.get_session", return_value=MagicMock()),
            patch("app.services.iett_client.IettClient", return_value=mock_client),
            patch("app.deps.update_fleet") as mock_update,
        ):
            await refresh_fleet_once()

        mock_update.assert_called_once_with(mock_buses)

    async def test_iett_api_error_is_swallowed(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_all_buses.side_effect = IettApiError("service down")

        with (
            patch("app.deps.get_session", return_value=MagicMock()),
            patch("app.services.iett_client.IettClient", return_value=mock_client),
        ):
            await refresh_fleet_once()  # must not raise

    async def test_unexpected_exception_is_swallowed(self) -> None:
        mock_client = AsyncMock()
        mock_client.get_all_buses.side_effect = RuntimeError("boom")

        with (
            patch("app.deps.get_session", return_value=MagicMock()),
            patch("app.services.iett_client.IettClient", return_value=mock_client),
        ):
            await refresh_fleet_once()  # must not raise


class TestRefreshFleetForever:
    async def test_schedules_refresh_and_cancels_cleanly(self) -> None:
        calls = {"count": 0}

        async def fake_ensure_fleet_fresh(*, max_age_seconds: int = 30) -> None:
            assert max_age_seconds == 0
            calls["count"] += 1

        async def fake_sleep(_: int) -> None:
            raise asyncio.CancelledError

        with (
            patch("app.deps.ensure_fleet_fresh", side_effect=fake_ensure_fleet_fresh),
            patch("app.services.fleet_poller.asyncio.sleep", side_effect=fake_sleep),
        ):
            with pytest.raises(asyncio.CancelledError):
                await refresh_fleet_forever(900)

        assert calls["count"] == 1

    async def test_clamps_periodic_interval_to_15_minutes_max(self) -> None:
        calls = {"count": 0}
        sleep_args: list[int] = []

        async def fake_ensure_fleet_fresh(*, max_age_seconds: int = 30) -> None:
            assert max_age_seconds == 0
            calls["count"] += 1

        async def fake_sleep(seconds: int) -> None:
            sleep_args.append(seconds)
            raise asyncio.CancelledError

        with (
            patch("app.deps.ensure_fleet_fresh", side_effect=fake_ensure_fleet_fresh),
            patch("app.services.fleet_poller.asyncio.sleep", side_effect=fake_sleep),
        ):
            with pytest.raises(asyncio.CancelledError):
                await refresh_fleet_forever(9_999)

        assert calls["count"] == 1
        assert sleep_args == [900]
