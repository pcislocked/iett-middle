"""Tests for app.services.ntcapi_client."""
from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator
from unittest.mock import patch

import aiohttp
import pytest
from aioresponses import aioresponses

from app.services.ntcapi_client import (
    NtcApiError,
    _parse_son_konum,
    _safe_int,
    get_bus_location,
    get_nearby_stops,
    get_route_buses_ybs,
    get_route_metadata,
    get_route_stops,
    get_stop_arrivals,
    get_timetable,
)
import app.services.ntcapi_client as _ntc_mod


_TOKEN_URL = "https://ntcapi.iett.istanbul/oauth2/v2/auth"
_SERVICE_URL = "https://ntcapi.iett.istanbul/service"


@pytest.fixture()
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.ThreadedResolver() if sys.platform == "win32" else None
    )
    s = aiohttp.ClientSession(connector=connector)
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def reset_token() -> None:
    """Clear cached token before each test."""
    _ntc_mod._token = None
    _ntc_mod._token_expiry = 0.0


def _mock_token(m: aioresponses) -> None:
    """Register a successful token endpoint response."""
    m.post(  # type: ignore[reportUnknownMemberType]
        _TOKEN_URL,
        payload={"access_token": "test-token", "expires_in": 3600},
    )


# ---------------------------------------------------------------------------
# _safe_int / _parse_son_konum — pure helpers
# ---------------------------------------------------------------------------

class TestSafeInt:
    def test_valid_int(self) -> None:
        assert _safe_int(42) == 42

    def test_string_int(self) -> None:
        assert _safe_int("7") == 7

    def test_none_returns_none(self) -> None:
        assert _safe_int(None) is None

    def test_non_numeric_returns_none(self) -> None:
        assert _safe_int("abc") is None


class TestParseSonKonum:
    def test_valid_lon_lat_string(self) -> None:
        lat, lon = _parse_son_konum("29.015,41.107")
        assert lon == 29.015
        assert lat == 41.107

    def test_none_returns_none_pair(self) -> None:
        assert _parse_son_konum(None) == (None, None)

    def test_empty_string_returns_none_pair(self) -> None:
        assert _parse_son_konum("") == (None, None)

    def test_malformed_returns_none_pair(self) -> None:
        assert _parse_son_konum("not-a-number") == (None, None)

    def test_missing_second_part_returns_none_pair(self) -> None:
        assert _parse_son_konum("29.015") == (None, None)


# ---------------------------------------------------------------------------
# Token cache
# ---------------------------------------------------------------------------

class TestEnsureToken:
    async def test_fetches_token_on_cold_start(
        self, session: aiohttp.ClientSession
    ) -> None:
        with aioresponses() as m:
            _mock_token(m)
            m.post(  # type: ignore[reportUnknownMemberType]
                _SERVICE_URL,
                payload=[],
            )
            await get_stop_arrivals("301341", session)
        assert _ntc_mod._token == "test-token"

    async def test_reuses_cached_token(self, session: aiohttp.ClientSession) -> None:
        _ntc_mod._token = "cached-token"
        _ntc_mod._token_expiry = time.time() + 3600

        with aioresponses() as m:
            m.post(_SERVICE_URL, payload=[])  # type: ignore[reportUnknownMemberType]
            await get_stop_arrivals("301341", session)
        # Token endpoint was NOT called — still the cached value
        assert _ntc_mod._token == "cached-token"

    async def test_token_fetch_failure_raises(self, session: aiohttp.ClientSession) -> None:
        with aioresponses() as m:
            m.post(_TOKEN_URL, status=401, payload={"error": "unauthorized"})  # type: ignore[reportUnknownMemberType]
            with pytest.raises(NtcApiError, match="Token fetch failed 401"):
                await get_stop_arrivals("301341", session)

    async def test_uses_expire_date_field(self, session: aiohttp.ClientSession) -> None:
        future_ms = (time.time() + 7200) * 1000
        with aioresponses() as m:
            m.post(  # type: ignore[reportUnknownMemberType]
                _TOKEN_URL,
                payload={"access_token": "tok2", "expire_date": int(future_ms)},
            )
            m.post(_SERVICE_URL, payload=[])  # type: ignore[reportUnknownMemberType]
            await get_stop_arrivals("301341", session)
        assert _ntc_mod._token == "tok2"
        assert _ntc_mod._token_expiry == pytest.approx(future_ms / 1000, rel=1e-3)


# ---------------------------------------------------------------------------
# _call_service — error path
# ---------------------------------------------------------------------------

class TestCallService:
    async def test_non_200_service_raises(self, session: aiohttp.ClientSession) -> None:
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, status=500, payload={"msg": "err"})  # type: ignore[reportUnknownMemberType]
            with pytest.raises(NtcApiError, match="Service call 'ybs' failed 500"):
                await get_stop_arrivals("301341", session)


# ---------------------------------------------------------------------------
# get_stop_arrivals
# ---------------------------------------------------------------------------

class TestGetStopArrivals:
    async def test_returns_valid_items(self, session: aiohttp.ClientSession) -> None:
        raw = [
            {"hatkodu": "500T", "saat": "10:05"},
            {"hatkodu": "14M",  "saat": "10:07"},
        ]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_stop_arrivals("301341", session)
        assert len(result) == 2

    async def test_skips_non_dict_items(self, session: aiohttp.ClientSession) -> None:
        raw = ["not-a-dict", {"hatkodu": "500T", "saat": "10:05"}]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_stop_arrivals("301341", session)
        assert len(result) == 1

    async def test_skips_items_with_no_route_or_time(
        self, session: aiohttp.ClientSession
    ) -> None:
        raw = [{"some_field": "x"}]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_stop_arrivals("301341", session)
        assert result == []


# ---------------------------------------------------------------------------
# get_bus_location
# ---------------------------------------------------------------------------

class TestGetBusLocation:
    async def test_found_returns_dict(self, session: aiohttp.ClientSession) -> None:
        raw = [{
            "K_ARAC_KAPINUMARASI": "A-001",
            "K_ARAC_PLAKA": "34HO1000",
            "H_OTOBUSKONUM_ENLEM": "41.107",
            "H_OTOBUSKONUM_BOYLAM": "29.015",
            "H_OTOBUSKONUM_HIZ": "0",
            "H_OTOBUSKONUM_KAYITZAMANI": "2026-03-02T00:00:00",
        }]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_bus_location("A-001", session)
        assert result is not None
        assert result["kapino"] == "A-001"
        assert result["plate"] == "34HO1000"

    async def test_empty_response_returns_none(self, session: aiohttp.ClientSession) -> None:
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=[])  # type: ignore[reportUnknownMemberType]
            result = await get_bus_location("X-999", session)
        assert result is None


# ---------------------------------------------------------------------------
# get_route_metadata
# ---------------------------------------------------------------------------

class TestGetRouteMetadata:
    async def test_deduplicates_variant_codes(self, session: aiohttp.ClientSession) -> None:
        raw = [
            {"GUZERGAH_GUZERGAH_KODU": "500T_G_D0", "GUZERGAH_YON": 119,
             "GUZERGAH_GUZERGAH_ADI": "TUZLA", "GUZERGAH_DEPAR_NO": 1, "HAT_ID": 42},
            {"GUZERGAH_GUZERGAH_KODU": "500T_G_D0", "GUZERGAH_YON": 119,
             "GUZERGAH_GUZERGAH_ADI": "TUZLA", "GUZERGAH_DEPAR_NO": 1, "HAT_ID": 42},
            {"GUZERGAH_GUZERGAH_KODU": "500T_D_D0", "GUZERGAH_YON": 120,
             "GUZERGAH_GUZERGAH_ADI": "LEVENT", "GUZERGAH_DEPAR_NO": 2, "HAT_ID": 42},
        ]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_route_metadata("500T", session)
        assert len(result) == 2
        directions = [r["direction"] for r in result]
        assert 0 in directions  # G
        assert 1 in directions  # D


# ---------------------------------------------------------------------------
# get_route_stops
# ---------------------------------------------------------------------------

class TestGetRouteStops:
    async def test_returns_sorted_stops(self, session: aiohttp.ClientSession) -> None:
        raw = [
            {"GUZERGAH_GUZERGAH_KODU": "500T_G_D0", "DURAK_DURAK_KODU": "111",
             "DURAK_ADI": "STOP_A", "GUZERGAH_SEGMENT_SIRA": 2,
             "DURAK_GEOLOC": {"y": 41.1, "x": 29.0}, "ILCELER_ILCEADI": "Kadikoy"},
            {"GUZERGAH_GUZERGAH_KODU": "500T_G_D0", "DURAK_DURAK_KODU": "222",
             "DURAK_ADI": "STOP_B", "GUZERGAH_SEGMENT_SIRA": 1,
             "DURAK_GEOLOC": {"y": 41.0, "x": 29.0}, "ILCELER_ILCEADI": "Atasehir"},
        ]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_route_stops("500T", "G", session)
        assert len(result) == 2
        assert result[0]["stop_code"] == "222"   # sequence 1 comes first
        assert result[1]["stop_code"] == "111"

    async def test_yon_letter_mapped_to_number(self, session: aiohttp.ClientSession) -> None:
        """'D' direction letter maps to yon '120' in the payload."""
        captured: list[dict] = []

        async def fake_call(session, alias, data):  # type: ignore[override]
            captured.append(data)
            return []

        with patch("app.services.ntcapi_client._call_service", side_effect=fake_call):
            await get_route_stops("500T", "D", session)

        assert captured[0]["HATYONETIM.GUZERGAH.YON"] == "120"

    async def test_empty_raw_returns_empty_list(self, session: aiohttp.ClientSession) -> None:
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=[])  # type: ignore[reportUnknownMemberType]
            result = await get_route_stops("500T", "G", session)
        assert result == []

    async def test_falls_back_to_largest_variant_when_no_d0(
        self, session: aiohttp.ClientSession
    ) -> None:
        raw = [
            {"GUZERGAH_GUZERGAH_KODU": "500T_G_D1", "DURAK_DURAK_KODU": "A",
             "DURAK_ADI": "X", "GUZERGAH_SEGMENT_SIRA": 1,
             "DURAK_GEOLOC": {}, "ILCELER_ILCEADI": None},
            {"GUZERGAH_GUZERGAH_KODU": "500T_G_D2", "DURAK_DURAK_KODU": "B",
             "DURAK_ADI": "Y", "GUZERGAH_SEGMENT_SIRA": 1,
             "DURAK_GEOLOC": {}, "ILCELER_ILCEADI": None},
            {"GUZERGAH_GUZERGAH_KODU": "500T_G_D2", "DURAK_DURAK_KODU": "C",
             "DURAK_ADI": "Z", "GUZERGAH_SEGMENT_SIRA": 2,
             "DURAK_GEOLOC": {}, "ILCELER_ILCEADI": None},
        ]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_route_stops("500T", "G", session)
        # D2 has 2 stops vs D1's 1 — should pick D2
        assert len(result) == 2


# ---------------------------------------------------------------------------
# get_route_buses_ybs
# ---------------------------------------------------------------------------

class TestGetRouteBusesYbs:
    async def test_parses_positions(self, session: aiohttp.ClientSession) -> None:
        raw = [
            {"K_ARAC_KAPINUMARASI": "A-001", "ENLEM": "41.106", "BOYLAM": "29.015",
             "SISTEMSAATI": "00:01:00", "K_GUZERGAH_GUZERGAHKODU": "500T_G_D0",
             "H_GOREV_DURAK_GECIS_SIRANO": "5"},
            {"K_ARAC_KAPINUMARASI": "B-002", "ENLEM": "41.090", "BOYLAM": "29.010",
             "SISTEMSAATI": "00:02:00", "K_GUZERGAH_GUZERGAHKODU": "500T_D_D0",
             "H_GOREV_DURAK_GECIS_SIRANO": None},
        ]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_route_buses_ybs(42, "500T", session)
        assert len(result) == 2
        assert result[0].kapino == "A-001"
        assert result[0].direction_letter == "G"
        assert result[0].stop_sequence == 5
        assert result[1].stop_sequence is None

    async def test_skips_items_without_lat_lon(self, session: aiohttp.ClientSession) -> None:
        raw = [
            {"K_ARAC_KAPINUMARASI": "X-999"},  # no ENLEM/BOYLAM
        ]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_route_buses_ybs(42, "500T", session)
        assert result == []


# ---------------------------------------------------------------------------
# get_timetable
# ---------------------------------------------------------------------------

class TestGetTimetable:
    async def test_returns_raw_list(self, session: aiohttp.ClientSession) -> None:
        raw = [{"K_ORER_GIDIS_SAATI": "06:00"}, {"K_ORER_GIDIS_SAATI": "06:15"}]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_timetable("500T", session)
        assert result == raw


# ---------------------------------------------------------------------------
# get_nearby_stops
# ---------------------------------------------------------------------------

class TestGetNearbyStops:
    async def test_parses_stop_list(self, session: aiohttp.ClientSession) -> None:
        raw = [
            {"DURAK_DURAK_KODU": "301341", "DURAK_ADI": "LEVENT",
             "DURAK_GEOLOC": {"y": 41.08, "x": 29.01}, "DURAK_YON_BILGISI": "G"},
        ]
        with aioresponses() as m:
            _mock_token(m)
            m.post(_SERVICE_URL, payload=raw)  # type: ignore[reportUnknownMemberType]
            result = await get_nearby_stops(41.08, 29.01, 0.5, session)
        assert len(result) == 1
        assert result[0]["stop_code"] == "301341"
        assert result[0]["stop_name"] == "LEVENT"
        assert result[0]["direction"] == "G"
