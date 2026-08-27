"""Tests for the IETT official timetable footnote scraper (app.services.iett_scraper)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.iett_scraper import (
    _fetch_official_footnotes,
    get_official_footnotes,
    match_footnote,
)

SAMPLE_IETT_HTML = """
<html>
<body>
<table class="line-table">
    <thead>
        <tr><th class="routedetailstartend">KÖPRÜBAŞI KALKIŞ</th></tr>
    </thead>
    <tbody>
        <tr>
            <td>06:10 (-1)</td>
            <td>07:00 (-2)</td>
            <td>08:00</td>
        </tr>
    </tbody>
</table>
<table class="line-table">
    <thead>
        <tr><th class="routedetailstartend">ÜSKÜDAR KALKIŞ</th></tr>
    </thead>
    <tbody>
        <tr>
            <td>06:40</td>
            <td>07:30 (-3)</td>
            <td>08:30</td>
        </tr>
    </tbody>
</table>
</body>
</html>
"""


@pytest.mark.asyncio
async def test_fetch_official_footnotes_parses_html() -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=SAMPLE_IETT_HTML)

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    result = await _fetch_official_footnotes("15F", mock_session)

    assert "KÖPRÜBAŞI KALKIŞ" in result
    assert result["KÖPRÜBAŞI KALKIŞ"]["H"]["06:10"] == "-1"
    assert result["KÖPRÜBAŞI KALKIŞ"]["C"]["07:00"] == "-2"
    assert "ÜSKÜDAR KALKIŞ" in result
    assert result["ÜSKÜDAR KALKIŞ"]["C"]["07:30"] == "-3"


@pytest.mark.asyncio
async def test_fetch_official_footnotes_handles_http_error() -> None:
    mock_resp = MagicMock()
    mock_resp.status = 500

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    result = await _fetch_official_footnotes("15F", mock_session)
    assert result == {}


@pytest.mark.asyncio
async def test_fetch_official_footnotes_handles_network_exception() -> None:
    mock_session = MagicMock()
    mock_session.post.side_effect = Exception("network timeout")

    result = await _fetch_official_footnotes("15F", mock_session)
    assert result == {}


@pytest.mark.asyncio
async def test_get_official_footnotes_cached() -> None:
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=SAMPLE_IETT_HTML)

    mock_session = MagicMock()
    mock_session.post.return_value.__aenter__.return_value = mock_resp

    res1 = await get_official_footnotes("500T", mock_session)
    assert isinstance(res1, dict)
    assert "KÖPRÜBAŞI KALKIŞ" in res1


def test_match_footnote_exact_direction() -> None:
    notes_dict = {
        "KÖPRÜBAŞI KALKIŞ": {
            "H": {"06:10": "-1"},
            "C": {"07:00": "-2"},
        },
        "ÜSKÜDAR KALKIŞ": {
            "C": {"07:30": "-3"},
        },
    }

    metadata = [
        {
            "direction": 0,
            "direction_name": "KÖPRÜBAŞI - ÜSKÜDAR CAMİİ ÖNÜ",
        },
        {
            "direction": 1,
            "direction_name": "ÜSKÜDAR - KÖPRÜBAŞI",
        },
    ]

    # Matching direction G (direction: 0, starts with KÖPRÜBAŞI)
    assert match_footnote(notes_dict, metadata, "G", "H", "06:10") == "-1"
    assert match_footnote(notes_dict, metadata, "G", "C", "07:00") == "-2"

    # Matching direction D (direction: 1, starts with ÜSKÜDAR)
    assert match_footnote(notes_dict, metadata, "D", "C", "07:30") == "-3"

    # Non-existent departure
    assert match_footnote(notes_dict, metadata, "G", "H", "09:00") is None


def test_match_footnote_fallback_unique() -> None:
    notes_dict = {
        "UNKNOWN DIR": {
            "H": {"06:10": "-1"},
        }
    }
    metadata = [{"direction": 0, "direction_name": "MISMATCHED NAME - SOMEWHERE"}]

    # Fallback to unique match
    assert match_footnote(notes_dict, metadata, "G", "H", "06:10") == "-1"
