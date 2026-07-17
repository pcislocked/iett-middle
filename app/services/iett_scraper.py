"""Scraper for the official IETT website to extract footnote mappings.

Fetches the rendered timetable HTML from iett.istanbul and extracts
the ``(-1)``, ``(-2)`` etc. footnote indicators next to departure times.
Results are aggressively cached for 24 hours.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict

import aiohttp
from bs4 import BeautifulSoup

from app.services.cache import cache_get_or_fetch

logger = logging.getLogger(__name__)

# 24-hour cache — timetable footnotes change very rarely
_CACHE_TTL = 86400


async def _fetch_official_footnotes(
    hat_kodu: str, session: aiohttp.ClientSession
) -> dict[str, dict[str, dict[str, str]]]:
    """POST to IETT and parse the scheduled departure HTML.

    Returns mapping:  html_direction_name -> day_type -> "HH:MM" -> note_id
    Example: {"KÖPRÜBAŞI KALKIŞ": {"H": {"06:10": "-1", ...}}, ...}
    """
    url = "https://iett.istanbul/tr/RouteStation/GetScheduledDepartureTimes"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
    }
    data = {"hCode": hat_kodu}

    try:
        async with session.post(
            url, headers=headers, data=data, timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status != 200:
                logger.warning("IETT scraper HTTP %s for %s", resp.status, hat_kodu)
                return {}
            html = await resp.text()
    except Exception as exc:
        logger.warning("IETT scraper network error for %s: %s", hat_kodu, exc)
        return {}

    if not html or len(html) < 50:
        return {}

    soup = BeautifulSoup(html, "lxml")
    tables = soup.select("table.line-table")

    mapping: dict[str, dict[str, dict[str, str]]] = defaultdict(
        lambda: defaultdict(dict)
    )

    for table in tables:
        th = table.select_one("th.routedetailstartend")
        if not th:
            continue
        direction_name = th.get_text(strip=True).upper()

        tbody = table.select_one("tbody")
        if not tbody:
            continue

        for row in tbody.select("tr"):
            cells = row.select("td")
            if not cells:
                continue

            day_mapping = {0: "H", 1: "C", 2: "P"}
            for col_idx, td in enumerate(cells):
                if col_idx not in day_mapping:
                    continue
                day_type = day_mapping[col_idx]

                text = td.get_text(strip=True)
                if not text:
                    continue

                # Parse "06:10 (-1)" or "06:10"
                match = re.search(r"(\d{2}:\d{2})(?:\s*\((-\d+)\))?", text)
                if match:
                    time_str = match.group(1)
                    note_id = match.group(2)
                    if note_id:
                        mapping[direction_name][day_type][time_str] = note_id

    return {k: {d: dict(t) for d, t in v.items()} for k, v in mapping.items()}


async def get_official_footnotes(
    hat_kodu: str, session: aiohttp.ClientSession
) -> dict[str, dict[str, dict[str, str]]]:
    """Cached wrapper — fetches once and stores for 24 hours."""
    key = f"official_notes:{hat_kodu}"

    async def _fetch() -> dict:
        return await _fetch_official_footnotes(hat_kodu, session)

    result = await cache_get_or_fetch(
        key, _CACHE_TTL, _fetch, stale_ttl=_CACHE_TTL, jitter=True
    )
    return result if isinstance(result, dict) else {}


def match_footnote(
    notes_dict: dict[str, dict[str, dict[str, str]]],
    metadata: list[dict],
    direction: str,
    day_type: str,
    departure_time: str,
) -> str | None:
    """Find the official footnote ID for a specific departure.

    The HTML uses direction labels like "KÖPRÜBAŞI KALKIŞ".
    Metadata has direction_name like "KÖPRÜBAŞI - ÜSKÜDAR CAMİİ ÖNÜ".
    We match by checking if the first part of metadata direction_name
    appears in the HTML direction label.

    Args:
        notes_dict: scraped {html_dir -> {day_type -> {time -> note_id}}}
        metadata: list of route metadata dicts
        direction: "G" or "D"
        day_type: "H", "C", or "P"
        departure_time: "HH:MM"

    Returns:
        The note_id string (e.g. "-1") or None
    """
    if not notes_dict or not metadata:
        return None

    # Build candidate start-names for this direction letter
    candidate_starts: list[str] = []
    for m in metadata:
        d_int = m.get("direction")
        letter = "D" if d_int == 1 else "G"
        if letter != direction:
            continue
        dn = (m.get("direction_name") or "").upper().strip()
        if not dn:
            continue
        # direction_name is "STOP_A - STOP_B", departure is FROM STOP_A
        first_part = dn.split(" - ")[0].strip()
        if first_part:
            candidate_starts.append(first_part)

    # Try to find a matching HTML direction
    for html_dir, day_dict in notes_dict.items():
        html_dir_upper = html_dir.upper()
        matched = any(start in html_dir_upper for start in candidate_starts)
        if not matched:
            continue
        if day_type in day_dict and departure_time in day_dict[day_type]:
            return day_dict[day_type][departure_time]

    # Fallback: if time is unique across ALL directions for this day_type
    all_matches: list[str] = []
    for day_dict in notes_dict.values():
        if day_type in day_dict and departure_time in day_dict[day_type]:
            all_matches.append(day_dict[day_type][departure_time])
    if len(all_matches) == 1:
        return all_matches[0]

    return None
