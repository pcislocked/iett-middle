"""HTTP client tests — all HTTP mocked with aioresponses."""
from __future__ import annotations

import re
import sys
from collections.abc import AsyncIterator

import pytest
from aioresponses import aioresponses

from app.services.iett_client import IettApiError, IettClient
from app.models.bus import BusPosition, Arrival
from app.models.stop import RouteStop
from app.models.route import ScheduledDeparture, Announcement
from tests.conftest import (
    ARRIVALS_HTML,
    FLEET_ALL_XML,
    ROUTE_FLEET_XML,
    ROUTES_BY_STATION_HTML,
    SCHEDULE_XML,
    ANNOUNCEMENTS_XML,
    ROUTE_STOPS_XML,
    SEARCH_JSON,
)

FLEET_URL = "https://api.ibb.gov.tr/iett/FiloDurum/SeferGerceklesme.asmx"
ARRIVALS_URL = re.compile(r"https://iett\.istanbul/tr/RouteStation/GetStationInfo.*")
ROUTES_AT_STOP_URL = re.compile(r"https://iett\.istanbul/tr/RouteStation/GetRouteByStation.*")
SCHEDULE_URL = "https://api.ibb.gov.tr/iett/UlasimAnaVeri/PlanlananSeferSaati.asmx"
ANNOUNCEMENTS_URL = "https://api.ibb.gov.tr/iett/UlasimDinamikVeri/Duyurular.asmx"
STOPS_URL = "https://api.ibb.gov.tr/iett/ibb/ibb.asmx"
SEARCH_URL = re.compile(r"https://iett\.istanbul/tr/RouteStation/GetSearchItems.*")


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


class TestGetStopArrivalsVia:
    async def test_filters_to_common_routes(self, client: IettClient) -> None:
        # Both stops share 14M; origin also has 500T which via doesn't
        via_html = '<div class="line-list"><div class="line-item"><a href="#"><span>14M</span></a></div></div>'
        with aioresponses() as m:
            m.get(ROUTES_AT_STOP_URL, body=ROUTES_BY_STATION_HTML)   # type: ignore[misc]  # origin: 14M, 15TY
            m.get(ROUTES_AT_STOP_URL, body=via_html)                  # type: ignore[misc]  # via: 14M only
            m.get(ARRIVALS_URL, body=ARRIVALS_HTML)                   # type: ignore[misc]  # 500T + 14M
            arrivals: list[Arrival] = await client.get_stop_arrivals_via("220602", "216572")
        # Only 14M should survive the via filter
        assert all(a.route_code == "14M" for a in arrivals)


class TestGetRouteSchedule:
    async def test_returns_schedule(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(SCHEDULE_URL, body=SCHEDULE_XML)  # type: ignore[misc]
            deps: list[ScheduledDeparture] = await client.get_route_schedule("500T")
        assert deps[0].departure_time == "05:55"


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


class TestGetRouteStops:
    async def test_returns_stops(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.post(STOPS_URL, body=ROUTE_STOPS_XML)  # type: ignore[misc]
            stops: list[RouteStop] = await client.get_route_stops("500T")
        assert stops[0].stop_code == "301341"


class TestSearchStops:
    async def test_returns_stops_only(self, client: IettClient) -> None:
        with aioresponses() as m:
            m.get(SEARCH_URL, payload=SEARCH_JSON)  # type: ignore[misc]
            results = await client.search_stops("ahmet mithat")
        assert len(results) == 1
        assert results[0].dcode == "220602"
