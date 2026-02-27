"""Raw HTTP client for all IETT and IBB APIs.

Each public method returns parsed model objects.
Raises IettApiError on any HTTP or parsing failure.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from app.config import settings
from app.models.bus import Arrival, BusPosition
from app.models.garage import Garage
from app.models.route import Announcement, RouteMetadata, RouteSearchResult, ScheduledDeparture
from app.models.stop import NearbyStop, RouteStop, StopDetail, StopSearchResult
from app.services.iett_parser import (
    parse_all_fleet_xml,
    parse_all_stops_json,
    parse_announcements_xml,
    parse_garages_xml,
    parse_route_fleet_xml,
    parse_route_metadata_json,
    parse_route_schedule_xml,
    parse_route_search_results,
    parse_route_stops_xml,
    parse_routes_from_html,
    parse_search_results,
    parse_stop_arrivals_html,
    parse_stop_detail_xml,
)

logger = logging.getLogger(__name__)

_SOAP_ENV = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
    ' xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soap:Body>{body}</soap:Body></soap:Envelope>"
)


class IettApiError(Exception):
    """Raised when an IETT API call fails."""


class IettClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _soap_post(self, url: str, body: str, action: str) -> str:
        envelope = _SOAP_ENV.format(body=body)
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": action,
        }
        try:
            async with self._session.post(
                url,
                data=envelope.encode("utf-8"),
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except Exception as exc:
            raise IettApiError(f"SOAP POST to {url} failed: {exc}") from exc

    async def _get_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        try:
            async with self._session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.text()
        except Exception as exc:
            raise IettApiError(f"GET {url} failed: {exc}") from exc

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
        except Exception as exc:
            raise IettApiError(f"GET {url} failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Fleet
    # ------------------------------------------------------------------

    async def get_all_buses(self) -> list[BusPosition]:
        """All ~7,000 active Istanbul buses (fleet snapshot)."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/FiloDurum/SeferGerceklesme.asmx",
            '<GetFiloAracKonum_json xmlns="http://tempuri.org/"/>',
            '"http://tempuri.org/GetFiloAracKonum_json"',
        )
        return parse_all_fleet_xml(xml)

    async def get_route_buses(self, hat_kodu: str) -> list[BusPosition]:
        """Live positions of all buses on a specific route."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/FiloDurum/SeferGerceklesme.asmx",
            f'<GetHatOtoKonum_json xmlns="http://tempuri.org/"><HatKodu>{hat_kodu}</HatKodu></GetHatOtoKonum_json>',
            '"http://tempuri.org/GetHatOtoKonum_json"',
        )
        return parse_route_fleet_xml(xml)

    # ------------------------------------------------------------------
    # Stops
    # ------------------------------------------------------------------

    async def get_stop_arrivals(self, dcode: str) -> list[Arrival]:
        """Real-time ETAs at a stop (HTML endpoint)."""
        try:
            html = await self._get_text(
                f"{settings.iett_rest_base}/tr/RouteStation/GetStationInfo",
                params={"dcode": dcode, "langid": "1"},
            )
        except IettApiError:
            # GetStationInfo returns 500 for some codes gracefully
            logger.warning("GetStationInfo returned error for dcode=%s", dcode)
            return []
        return parse_stop_arrivals_html(html)

    async def get_routes_at_stop(self, dcode: str) -> set[str]:
        """All route codes that stop at a given stop."""
        html = await self._get_text(
            f"{settings.iett_rest_base}/tr/RouteStation/GetRouteByStation",
            params={"dcode": dcode, "langid": "1"},
        )
        return parse_routes_from_html(html)

    async def get_stop_arrivals_via(
        self, dcode_origin: str, dcode_via: str
    ) -> list[Arrival]:
        """Arrivals at origin that will also pass through via stop."""
        routes_origin, routes_via = await asyncio.gather(
            self.get_routes_at_stop(dcode_origin),
            self.get_routes_at_stop(dcode_via),
        )
        common = routes_origin & routes_via
        all_arrivals = await self.get_stop_arrivals(dcode_origin)
        return [a for a in all_arrivals if a.route_code in common]

    async def search_stops(self, query: str) -> list[StopSearchResult]:
        """Search stops by name. Returns stop dcode + name."""
        raw = await self._get_json(
            f"{settings.iett_rest_base}/tr/RouteStation/GetSearchItems",
            params={"key": query, "langid": "1"},
        )
        return [StopSearchResult(**item) for item in parse_search_results(raw)]

    async def get_stop_detail(self, dcode: str) -> StopDetail | None:
        """Stop name + coordinates via GetDurak_json (single SOAP call)."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/UlasimAnaVeri/HatDurakGuzergah.asmx",
            f'<GetDurak_json xmlns="http://tempuri.org/"><DurakKodu>{dcode}</DurakKodu></GetDurak_json>',
            '"http://tempuri.org/GetDurak_json"',
        )
        return parse_stop_detail_xml(xml, dcode)

    async def get_all_stops(self) -> list[NearbyStop]:
        """All ~15 k IETT stops with coordinates (GetDurak_json, empty DurakKodu)."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/UlasimAnaVeri/HatDurakGuzergah.asmx",
            '<GetDurak_json xmlns="http://tempuri.org/"><DurakKodu></DurakKodu></GetDurak_json>',
            '"http://tempuri.org/GetDurak_json"',
        )
        return parse_all_stops_json(xml)

    async def get_garages(self) -> list[Garage]:
        """All IETT bus garage locations via GetGaraj_json."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/UlasimAnaVeri/HatDurakGuzergah.asmx",
            '<GetGaraj_json xmlns="http://tempuri.org/"/>',
            '"http://tempuri.org/GetGaraj_json"',
        )
        return parse_garages_xml(xml)

    async def search_routes(self, query: str) -> list[RouteSearchResult]:
        """Search routes by name or code. Returns hat_kodu + name."""
        raw = await self._get_json(
            f"{settings.iett_rest_base}/tr/RouteStation/GetSearchItems",
            params={"key": query, "langid": "1"},
        )
        return [RouteSearchResult(**item) for item in parse_route_search_results(raw)]

    async def get_route_metadata(self, hat_kodu: str) -> list[RouteMetadata]:
        """Route variant metadata (direction names, variant codes) via GetAllRoute."""
        raw = await self._get_json(
            f"{settings.iett_rest_base}/tr/RouteStation/GetAllRoute",
            params={"rcode": hat_kodu},
        )
        return [RouteMetadata(**item) for item in parse_route_metadata_json(raw)]

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    async def get_route_stops(self, hat_kodu: str) -> list[RouteStop]:
        """Ordered stop list for a route (SOAP)."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/ibb/ibb.asmx",
            f'<DurakDetay_GYY xmlns="http://tempuri.org/"><HatKodu>{hat_kodu}</HatKodu></DurakDetay_GYY>',
            '"http://tempuri.org/DurakDetay_GYY"',
        )
        return parse_route_stops_xml(xml)

    async def get_route_schedule(self, hat_kodu: str) -> list[ScheduledDeparture]:
        """Planned departure times for a route."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/UlasimAnaVeri/PlanlananSeferSaati.asmx",
            f'<GetPlanlananSeferSaati_json xmlns="http://tempuri.org/"><HatKodu>{hat_kodu}</HatKodu></GetPlanlananSeferSaati_json>',
            '"http://tempuri.org/GetPlanlananSeferSaati_json"',
        )
        return parse_route_schedule_xml(xml)

    async def get_announcements(self, hat_kodu: str | None = None) -> list[Announcement]:
        """Active disruption announcements. Optionally filter by route."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/UlasimDinamikVeri/Duyurular.asmx",
            '<GetDuyurular_json xmlns="http://tempuri.org/"/>',
            '"http://tempuri.org/GetDuyurular_json"',
        )
        announcements = parse_announcements_xml(xml)
        if hat_kodu:
            hat_upper = hat_kodu.upper().strip()
            announcements = [a for a in announcements if a.route_code.upper().strip() == hat_upper]
        return announcements
