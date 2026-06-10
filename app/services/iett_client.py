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
from app.models.route import (
    Announcement,
    RouteMetadata,
    RouteSearchResult,
    ScheduledDeparture,
)
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
    parse_route_stops_html,
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


_global_mobiett: Any = None


class IettClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        global _global_mobiett
        self._session = session
        if _global_mobiett is None or _global_mobiett._session.closed:
            from app.services.mobiett_client import MobiettClient

            _global_mobiett = MobiettClient(self._session)
        self.mobiett = _global_mobiett

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

    async def _get_json(
        self, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
        from app.services.iett_parser import parse_mobiett_buses

        async def fetch_soap():
            xml = await self._soap_post(
                f"{settings.iett_soap_base}/FiloDurum/SeferGerceklesme.asmx",
                f'<GetHatOtoKonum_json xmlns="http://tempuri.org/"><HatKodu>{hat_kodu}</HatKodu></GetHatOtoKonum_json>',
                '"http://tempuri.org/GetHatOtoKonum_json"',
            )
            return parse_route_fleet_xml(xml)

        async def fetch_json():
            data = await self.mobiett.get_live_fleet(hat_kodu)
            return parse_mobiett_buses(data)

        soap_task = asyncio.create_task(fetch_soap())
        json_task = asyncio.create_task(fetch_json())

        results = await asyncio.gather(soap_task, json_task, return_exceptions=True)
        soap_res = results[0]
        json_res = results[1]

        buses: dict[str, BusPosition] = {}

        if not isinstance(json_res, Exception):
            for b in json_res:
                if b.kapino:
                    buses[b.kapino] = b

        if not isinstance(soap_res, Exception):
            for b in soap_res:
                if not b.kapino:
                    continue
                if b.kapino in buses:
                    buses[b.kapino] = buses[b.kapino].model_copy(
                        update={"plate": b.plate}
                    )
                else:
                    buses[b.kapino] = b

        if isinstance(json_res, Exception) and isinstance(soap_res, Exception):
            raise json_res

        return list(buses.values())

    # ------------------------------------------------------------------
    # Stops
    # ------------------------------------------------------------------

    async def get_stop_arrivals(self, dcode: str) -> list[Arrival]:
        """Real-time ETAs at a stop (HTML endpoint). Propagates IettApiError on failure."""
        html = await self._get_text(
            f"{settings.iett_rest_base}/tr/RouteStation/GetStationInfo",
            params={"dcode": dcode, "langid": "1"},
        )
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
        """Search stops by name."""
        query = query.upper()
        if not query.endswith("%"):
            query = query + "%"

        res = await self.mobiett._post_service(
            "mainGetBusStop_basic_search", {"HATYONETIM.DURAK.ADI": query}
        )
        if not isinstance(res, list) or not res:
            res = await self.mobiett._post_service(
                "mainGetBusStop_basic_search", {"HATYONETIM.DURAK.DURAK_KODU": query}
            )
        if not isinstance(res, list):
            res = []

        return [
            StopSearchResult(
                dcode=str(r.get("DURAK_DURAK_KODU", "")), name=r.get("DURAK_ADI", "")
            )
            for r in res
            if r.get("DURAK_DURAK_KODU")
        ]

    async def get_stop_detail(self, dcode: str) -> StopDetail | None:
        """Stop name + coordinates via SOAP or JSON fallback.

        Falls back to the in-memory stop index for coordinates when the SOAP
        response omits or zeroes them out.
        """
        from app.deps import get_stop_coords  # noqa: PLC0415
        from app.services.iett_parser import parse_mobiett_stop

        async def fetch_soap():
            xml = await self._soap_post(
                f"{settings.iett_soap_base}/UlasimAnaVeri/HatDurakGuzergah.asmx",
                f'<GetDurak_json xmlns="http://tempuri.org/"><DurakKodu>{dcode}</DurakKodu></GetDurak_json>',
                '"http://tempuri.org/GetDurak_json"',
            )
            return parse_stop_detail_xml(xml, dcode)

        async def fetch_json():
            data = await self.mobiett.get_stop_detail(dcode)
            if data:
                return parse_mobiett_stop(data)
            return None

        soap_task = asyncio.create_task(fetch_soap())
        json_task = asyncio.create_task(fetch_json())

        results = await asyncio.gather(soap_task, json_task, return_exceptions=True)
        soap_res = results[0]
        json_res = results[1]

        soap_valid = not isinstance(soap_res, Exception) and soap_res
        json_valid = not isinstance(json_res, Exception) and json_res

        detail = None
        if soap_valid and json_valid:
            soap_has_coords = soap_res.latitude not in (
                None,
                0.0,
            ) and soap_res.longitude not in (None, 0.0)
            json_has_coords = json_res.latitude not in (
                None,
                0.0,
            ) and json_res.longitude not in (None, 0.0)
            if json_has_coords and not soap_has_coords:
                detail = json_res
            else:
                detail = soap_res
        elif soap_valid:
            detail = soap_res
        elif json_valid:
            detail = json_res

        if isinstance(json_res, Exception) and isinstance(soap_res, Exception):
            logger.error(
                f"get_stop_detail failed for {dcode}: SOAP={soap_res}, JSON={json_res}"
            )
            raise IettApiError(
                f"Both SOAP and JSON failed for stop {dcode}"
            ) from json_res

        if detail is not None and (
            detail.latitude in (None, 0.0) or detail.longitude in (None, 0.0)
        ):
            coords = get_stop_coords(dcode)
            if coords:
                detail = detail.model_copy(
                    update={"latitude": coords[0], "longitude": coords[1]}
                )
        return detail

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
        query = query.upper()
        if not query.endswith("%"):
            query = query + "%"

        res = await self.mobiett._post_service(
            "mainGetLine_basic_search", {"HATYONETIM.HAT.HAT_KODU": query}
        )
        if not isinstance(res, list) or not res:
            res = await self.mobiett._post_service(
                "mainGetLine_basic_search", {"HATYONETIM.HAT.HAT_ADI": query}
            )
        if not isinstance(res, list):
            res = []

        return [
            RouteSearchResult(
                hat_kodu=r.get("HAT_HAT_KODU", ""), name=r.get("HAT_HAT_ADI", "")
            )
            for r in res
            if r.get("HAT_HAT_KODU")
        ]

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
        """Ordered stop list for a route (scrapes GetStationForRoute HTML).

        Coordinates are filled from the in-memory stop index when available;
        they are ``None`` for stops not yet indexed (e.g. before startup
        finishes).  The API layer (``routes.py``) guards against poisoning
        the long-lived cache: results are only stored when *all* stops carry
        valid coordinates, so coord-less responses are never persisted.
        """
        from app.deps import get_stop_coords  # noqa: PLC0415

        html = await self._get_text(
            f"{settings.iett_rest_base}/tr/RouteStation/GetStationForRoute",
            params={"hatkod": hat_kodu, "hatstart": "X", "hatend": "Y", "langid": "1"},
        )
        raw = parse_route_stops_html(html, hat_kodu)
        result: list[RouteStop] = []
        for s in raw:
            coords = get_stop_coords(s["stop_code"])
            result.append(
                RouteStop(
                    **s,
                    latitude=coords[0] if coords else None,
                    longitude=coords[1] if coords else None,
                )
            )
        return result

    async def get_route_schedule(self, hat_kodu: str) -> list[ScheduledDeparture]:
        """Planned departure times for a route."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/UlasimAnaVeri/PlanlananSeferSaati.asmx",
            f'<GetPlanlananSeferSaati_json xmlns="http://tempuri.org/"><HatKodu>{hat_kodu}</HatKodu></GetPlanlananSeferSaati_json>',
            '"http://tempuri.org/GetPlanlananSeferSaati_json"',
        )
        return parse_route_schedule_xml(xml)

    async def get_announcements(
        self, hat_kodu: str | None = None
    ) -> list[Announcement]:
        """Active disruption announcements. Optionally filter by route."""
        xml = await self._soap_post(
            f"{settings.iett_soap_base}/UlasimDinamikVeri/Duyurular.asmx",
            '<GetDuyurular_json xmlns="http://tempuri.org/"/>',
            '"http://tempuri.org/GetDuyurular_json"',
        )
        announcements = parse_announcements_xml(xml)
        if hat_kodu:
            hat_upper = hat_kodu.upper().strip()
            announcements = [
                a for a in announcements if a.route_code.upper().strip() == hat_upper
            ]
        return announcements
