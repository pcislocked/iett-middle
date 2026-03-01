"""Tests for app.services.stop_indexer — background stop-catalogue loader."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.stop import NearbyStop


def _stop(code: str = "301341") -> NearbyStop:
    return NearbyStop(
        stop_code=code,
        stop_name="Test Stop",
        latitude=41.0,
        longitude=29.0,
        district="Şişli",
        distance_m=0.0,
    )


class TestIndexStopsForever:
    """Tests for the index_stops_forever background coroutine."""

    def test_successful_run_updates_index(self) -> None:
        """Normal path: get_all_stops succeeds → update_stop_index called once, then CancelledError exits."""
        mock_client = MagicMock()
        mock_client.get_all_stops = AsyncMock(return_value=[_stop()])

        call_count = 0

        async def fake_sleep(_: float) -> None:
            nonlocal call_count
            call_count += 1
            raise asyncio.CancelledError  # exit after first sleep

        with (
            patch("app.services.iett_client.IettClient", return_value=mock_client),
            patch("app.deps.get_session", return_value=MagicMock()),
            patch("app.deps.update_stop_index") as mock_update,
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(_run())

        mock_update.assert_called_once()
        args = mock_update.call_args[0][0]
        assert len(args) == 1
        assert args[0].stop_code == "301341"

    def test_iett_api_error_retries_after_60s(self) -> None:
        """When get_all_stops raises IettApiError, should sleep 60 s then retry."""
        from app.services.iett_client import IettApiError

        attempt = 0
        mock_client = MagicMock()

        async def maybe_raise():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise IettApiError("network down")
            return [_stop()]

        mock_client.get_all_stops = maybe_raise

        sleep_calls: list[float] = []

        async def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError

        with (
            patch("app.services.iett_client.IettClient", return_value=mock_client),
            patch("app.deps.get_session", return_value=MagicMock()),
            patch("app.deps.update_stop_index"),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(_run())

        # First sleep must be the 60 s retry (IettApiError path)
        assert sleep_calls[0] == 60

    def test_cancelled_error_during_sleep_propagates(self) -> None:
        """CancelledError raised during the long nightly sleep should propagate cleanly."""
        mock_client = MagicMock()
        mock_client.get_all_stops = AsyncMock(return_value=[_stop()])

        async def immediate_cancel(_: float) -> None:
            raise asyncio.CancelledError

        with (
            patch("app.services.iett_client.IettClient", return_value=mock_client),
            patch("app.deps.get_session", return_value=MagicMock()),
            patch("app.deps.update_stop_index"),
            patch("asyncio.sleep", side_effect=immediate_cancel),
        ):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(_run())

    def test_unexpected_exception_does_not_crash_loop(self) -> None:
        """An unexpected exception inside the fetch block is caught; loop continues to sleep."""
        mock_client = MagicMock()
        mock_client.get_all_stops = AsyncMock(side_effect=RuntimeError("surprise"))

        sleep_calls: list[float] = []

        async def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)
            raise asyncio.CancelledError

        with (
            patch("app.services.iett_client.IettClient", return_value=mock_client),
            patch("app.deps.get_session", return_value=MagicMock()),
            patch("app.deps.update_stop_index"),
            patch("asyncio.sleep", side_effect=fake_sleep),
        ):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(_run())

        # Loop should have slept after the unexpected error (falls through to nightly sleep)
        assert len(sleep_calls) >= 1


async def _run() -> None:
    """Helper: import and run index_stops_forever inside current event loop."""
    from app.services.stop_indexer import index_stops_forever
    await index_stops_forever()
