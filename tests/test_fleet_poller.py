"""Tests for app.services.fleet_poller."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from app.services.fleet_poller import refresh_fleet_once
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
