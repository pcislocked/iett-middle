"""HTTP client tests — all HTTP mocked with aioresponses."""
from __future__ import annotations

import re
import sys
from collections.abc import AsyncIterator

import pytest
from aioresponses import aioresponses

from app.services.iett_client import IettApiError, IettClient
from app.models.bus import BusPosition, Arrival
from app.models.garage import Garage
from app.models.stop import NearbyStop, RouteStop, StopDetail
from app.models.route import Announcement, RouteMetadata, RouteSearchResult, ScheduledDeparture
from tests.conftest import (
    ALL_STOPS_XML,
    ARRIVALS_HTML,
    FLEET_ALL_XML,
    GARAGE_XML,
    ROUTE_FLEET_XML,
    ROUTE_METADATA_JSON,
    ROUTE_SEARCH_JSON,
    ROUTE_STOPS_HTML,
    ROUTES_BY_STATION_HTML,
    SCHEDULE_XML,
    ANNOUNCEMENTS_XML,
    SEARCH_JSON,
    STOP_DETAIL_XML,
)

FLEET_URL = "https://api.ibb.gov.tr/iett/FiloDurum/SeferGerceklesme.asmx"
HAT_DURAK_URL = "https://api.ibb.gov.tr/iett/UlasimAnaVeri/HatDurakGuzergah.asmx"
ARRIVALS_URL = re.compile(r"https://iett\.istanbul/tr/RouteStation/GetStationInfo.*")
ROUTES_AT_STOP_URL = re.compile(r"https://iett\.istanbul/tr/RouteStation/GetRouteByStation.*")
SCHEDULE_URL = "https://api.ibb.gov.tr/iett/UlasimAnaVeri/PlanlananSeferSaati.asmx"
ANNOUNCEMENTS_URL = "https://api.ibb.gov.tr/iett/UlasimDinamikVeri/Duyurular.asmx"
ROUTE_STATION_FOR_ROUTE_URL = re.compile(r"https://iett\.istanbul/tr/RouteStation/GetStationForRoute.*")
SEARCH_URL = re.compile(r"https://iett\.istanbul/tr/RouteStation/GetSearchItems.*")
ALL_ROUTE_URL = re.compile(r"https://iett\.istanbul/tr/RouteStation/GetAllRoute.*")


@pytest.fixture()
async def client() -> AsyncIterator[IettClient]:
    import aiohttp
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver() if sys.platform == "win32" else None)
    session = aiohttp.ClientSession(connector=connector)
    yield IettClient(session)
    await session.close()


class TestGetAllBuses:
    async def test_returns_buses(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(FLEET_URL, body=FLEET_ALL_XML)  # type: ignore[misc]
            buses: list[BusPosition] = await client.get_all_buses()
        assert len(buses) == 1
        assert buses[0].kapino == "A-001"

    async def test_plate_parsed(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(FLEET_URL, body=FLEET_ALL_XML)  # type: ignore[misc]
            buses = await client.get_all_buses()
        assert buses[0].plate == "34 HO 1000"

    async def test_raises_on_network_error(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(FLEET_URL, exception=Exception("timeout"))  # type: ignore[misc]
            with pytest.raises(IettApiError):
                await client.get_all_buses()


class TestGetRouteBuses:
    async def test_returns_buses(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(FLEET_URL, body=ROUTE_FLEET_XML)  # type: ignore[misc]
            buses: list[BusPosition] = await client.get_route_buses("500T")
        assert buses[0].route_code == "500T"

    async def test_nearest_stop_parsed(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(FLEET_URL, body=ROUTE_FLEET_XML)  # type: ignore[misc]
            buses = await client.get_route_buses("500T")
        assert buses[0].nearest_stop == "113333"


class TestGetStopArrivals:
    async def test_returns_arrivals(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(ARRIVALS_URL, body=ARRIVALS_HTML)  # type: ignore[misc]
            arrivals: list[Arrival] = await client.get_stop_arrivals("220602")
        assert len(arrivals) == 2

    async def test_returns_empty_on_500(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(ARRIVALS_URL, status=500)  # type: ignore[misc]
            arrivals: list[Arrival] = await client.get_stop_arrivals("000000")
        assert arrivals == []


class TestGetRoutesAtStop:
    async def test_returns_route_set(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(ROUTES_AT_STOP_URL, body=ROUTES_BY_STATION_HTML)  # type: ignore[misc]
            routes: set[str] = await client.get_routes_at_stop("220602")
        assert "14M" in routes
        assert "15TY" in routes


class TestGetStopArrivalsVia:
    async def test_filters_to_common_routes(self, client: IettClient) -> None:
        via_html = '<div class="line-list"><div class="line-item"><a href="#"><span>14M</span></a></div></div>'
        with aioresponses() as m:
            m.get(ROUTES_AT_STOP_URL, body=ROUTES_BY_STATION_HTML)   # type: ignore[misc]
            m.get(ROUTES_AT_STOP_URL, body=via_html)                  # type: ignore[misc]
            m.get(ARRIVALS_URL, body=ARRIVALS_HTML)                   # type: ignore[misc]
            arrivals: list[Arrival] = await client.get_stop_arrivals_via("220602", "216572")
        assert all(a.route_code == "14M" for a in arrivals)

    async def test_empty_when_no_common_routes(self, client: IettClient) -> None:
        other_html = '<div class="line-list"><div class="line-item"><a href="#"><span>999Z</span></a></div></div>'
        with aioresponses() as m:
            m.get(ROUTES_AT_STOP_URL, body=ROUTES_BY_STATION_HTML)   # type: ignore[misc]
            m.get(ROUTES_AT_STOP_URL, body=other_html)                # type: ignore[misc]
            m.get(ARRIVALS_URL, body=ARRIVALS_HTML)                   # type: ignore[misc]
            arrivals = await client.get_stop_arrivals_via("220602", "999999")
        assert arrivals == []


class TestGetRouteSchedule:
    async def test_returns_schedule(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(SCHEDULE_URL, body=SCHEDULE_XML)  # type: ignore[misc]
            deps: list[ScheduledDeparture] = await client.get_route_schedule("500T")
        assert deps[0].departure_time == "05:55"

    async def test_raises_on_error(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(SCHEDULE_URL, status=503)  # type: ignore[misc]
            with pytest.raises(IettApiError):
                await client.get_route_schedule("500T")


class TestGetAnnouncements:
    async def test_returns_all(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(ANNOUNCEMENTS_URL, body=ANNOUNCEMENTS_XML)  # type: ignore[misc]
            anns: list[Announcement] = await client.get_announcements()
        assert len(anns) == 1

    async def test_filter_by_hat_kodu(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(ANNOUNCEMENTS_URL, body=ANNOUNCEMENTS_XML)  # type: ignore[misc]
            anns: list[Announcement] = await client.get_announcements("NOTEXIST")
        assert anns == []

    async def test_case_insensitive_filter(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(ANNOUNCEMENTS_URL, body=ANNOUNCEMENTS_XML)  # type: ignore[misc]
            anns = await client.get_announcements("500t")
        assert len(anns) == 1


class TestGetRouteStops:
    async def test_returns_stops(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(ROUTE_STATION_FOR_ROUTE_URL, body=ROUTE_STOPS_HTML)  # type: ignore[misc]
            stops: list[RouteStop] = await client.get_route_stops("15F")
        assert stops[0].stop_code == "262541"
        assert stops[0].route_code == "15F"
        assert stops[0].direction == "\u015eAH\u0130NKAYA GARAJI"
        assert stops[0].sequence == 1
        assert stops[0].latitude is None  # stop index not populated in unit tests

    async def test_raises_on_error(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(ROUTE_STATION_FOR_ROUTE_URL, exception=Exception("timeout"))  # type: ignore[misc]
            with pytest.raises(IettApiError):
                await client.get_route_stops("15F")


class TestSearchStops:
    async def test_returns_stops_only(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(SEARCH_URL, payload=SEARCH_JSON)  # type: ignore[misc]
            results = await client.search_stops("ahmet mithat")
        assert len(results) == 1
        assert results[0].dcode == "220602"

    async def test_raises_on_error(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(SEARCH_URL, exception=Exception("dns"))  # type: ignore[misc]
            with pytest.raises(IettApiError):
                await client.search_stops("xyz")


class TestSearchRoutes:
    async def test_returns_routes_only(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(SEARCH_URL, payload=ROUTE_SEARCH_JSON)  # type: ignore[misc]
            results: list[RouteSearchResult] = await client.search_routes("500T")
        assert len(results) == 1
        assert results[0].hat_kodu == "500T"


class TestGetRouteMetadata:
    async def test_returns_metadata(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(ALL_ROUTE_URL, payload=ROUTE_METADATA_JSON)  # type: ignore[misc]
            meta: list[RouteMetadata] = await client.get_route_metadata("500T")
        assert len(meta) == 2
        assert meta[0].variant_code == "500T_D_D0"

    async def test_raises_on_error(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(ALL_ROUTE_URL, status=502)  # type: ignore[misc]
            with pytest.raises(IettApiError):
                await client.get_route_metadata("500T")


class TestGetStopDetail:
    async def test_returns_detail(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(HAT_DURAK_URL, body=STOP_DETAIL_XML)  # type: ignore[misc]
            detail: StopDetail | None = await client.get_stop_detail("220602")
        assert detail is not None
        assert detail.name == "AHMET MİTHAT EFENDİ"
        assert detail.dcode == "220602"

    async def test_returns_none_when_not_found(self, client: IettClient) -> None:
        empty_xml = (
            "<?xml version='1.0' encoding='utf-8'?>"
            "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
            "<soap:Body><GetDurak_jsonResponse xmlns='http://tempuri.org/'>"
            "<GetDurak_jsonResult>[]</GetDurak_jsonResult>"
            "</GetDurak_jsonResponse></soap:Body></soap:Envelope>"
        )
        with aioresponses() as m:
            m.post(HAT_DURAK_URL, body=empty_xml)  # type: ignore[misc]
            detail = await client.get_stop_detail("000000")
        assert detail is None


class TestGetAllStops:
    async def test_returns_stops(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(HAT_DURAK_URL, body=ALL_STOPS_XML)  # type: ignore[misc]
            stops: list[NearbyStop] = await client.get_all_stops()
        assert len(stops) == 3

    async def test_wkt_coords_parsed(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(HAT_DURAK_URL, body=ALL_STOPS_XML)  # type: ignore[misc]
            stops = await client.get_all_stops()
        levent = next(s for s in stops if s.stop_code == "301341")
        assert abs(levent.latitude - 41.0842) < 0.001

    async def test_raises_on_error(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(HAT_DURAK_URL, exception=Exception("timeout"))  # type: ignore[misc]
            with pytest.raises(IettApiError):
                await client.get_all_stops()


class TestGetGarages:
    async def test_returns_garages(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(HAT_DURAK_URL, body=GARAGE_XML)  # type: ignore[misc]
            garages: list[Garage] = await client.get_garages()
        assert len(garages) == 2
        assert garages[0].name == "IKITELLI GARAJ"

    async def test_raises_on_error(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(HAT_DURAK_URL, exception=ConnectionError("refused"))  # type: ignore[misc]
            with pytest.raises(IettApiError):
                await client.get_garages()
