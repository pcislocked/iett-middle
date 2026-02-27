"""Pure parsing functions — no async, no HTTP, no HA.

All functions take raw text (XML/HTML string) and return typed lists.
"""
from __future__ import annotations

import json
import re
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from app.models.bus import Arrival, BusPosition
from app.models.route import Announcement, ScheduledDeparture
from app.models.stop import RouteStop

_TEMPURI = "http://tempuri.org/"


def _extract_soap_json(xml_text: str, result_tag: str) -> list[dict]:
    """Extract and JSON-parse the payload embedded inside a SOAP XML element."""
    root = ET.fromstring(xml_text)
    el = root.find(f".//{{{_TEMPURI}}}{result_tag}")
    if el is None or not el.text:
        return []
    try:
        data = json.loads(el.text)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


# ---------------------------------------------------------------------------
# 1. Fleet parsers
# ---------------------------------------------------------------------------

def parse_all_fleet_xml(xml_text: str) -> list[BusPosition]:
    """Parse GetFiloAracKonum_json SOAP response.

    Keys are CAPITALISED in this endpoint.
    """
    records = _extract_soap_json(xml_text, "GetFiloAracKonum_jsonResult")
    result: list[BusPosition] = []
    for r in records:
        try:
            result.append(
                BusPosition(
                    kapino=r.get("KapiNo", ""),
                    plate=r.get("Plaka"),
                    latitude=float(r.get("Enlem", 0)),
                    longitude=float(r.get("Boylam", 0)),
                    speed=int(float(r.get("Hiz", 0))),
                    operator=r.get("Operator"),
                    last_seen=r.get("Saat", ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


def parse_route_fleet_xml(xml_text: str) -> list[BusPosition]:
    """Parse GetHatOtoKonum_json SOAP response.

    Keys are lowercase in this endpoint (different from all-fleet!).
    """
    records = _extract_soap_json(xml_text, "GetHatOtoKonum_jsonResult")
    result: list[BusPosition] = []
    for r in records:
        try:
            result.append(
                BusPosition(
                    kapino=r.get("kapino", ""),
                    latitude=float(r.get("enlem", 0)),
                    longitude=float(r.get("boylam", 0)),
                    last_seen=r.get("son_konum_zamani", ""),
                    route_code=r.get("hatkodu"),
                    route_name=r.get("hatad"),
                    direction=r.get("yon"),
                    nearest_stop=r.get("yakinDurakKodu"),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


# ---------------------------------------------------------------------------
# 2. Stop arrivals (HTML)
# ---------------------------------------------------------------------------

def parse_stop_arrivals_html(html: str) -> list[Arrival]:
    """Parse GetStationInfo HTML fragment into Arrival list."""
    soup = BeautifulSoup(html, "html.parser")
    result: list[Arrival] = []
    for item in soup.select("div.line-item div.content:not(.content-header)"):
        route_el = item.select_one("span")
        b = item.select_one("b")
        p = item.select_one("p")
        if not route_el or not b or not p:
            continue
        eta_match = re.search(r"(\d+)\s*dk", b.text)
        result.append(
            Arrival(
                route_code=route_el.text.strip(),
                destination=p.text.replace(b.text, "").strip(),
                eta_minutes=int(eta_match.group(1)) if eta_match else None,
                eta_raw=b.text.strip(),
            )
        )
    return result


# ---------------------------------------------------------------------------
# 3. Routes through a stop (HTML)
# ---------------------------------------------------------------------------

def parse_routes_from_html(html: str) -> set[str]:
    """Parse GetRouteByStation HTML fragment — returns set of route codes."""
    soup = BeautifulSoup(html, "html.parser")
    return {
        span.text.strip()
        for item in soup.select("div.line-item")
        for span in item.select("a > span:first-child")
    }


# ---------------------------------------------------------------------------
# 4. Schedule
# ---------------------------------------------------------------------------

def parse_route_schedule_xml(xml_text: str) -> list[ScheduledDeparture]:
    """Parse GetPlanlananSeferSaati_json SOAP response."""
    records = _extract_soap_json(xml_text, "GetPlanlananSeferSaati_jsonResult")
    result: list[ScheduledDeparture] = []
    for r in records:
        try:
            result.append(
                ScheduledDeparture(
                    route_code=r.get("SHATKODU", ""),
                    route_name=r.get("HATADI", ""),
                    route_variant=r.get("SGUZERAH", ""),
                    direction=r.get("SYON", ""),
                    day_type=r.get("SGUNTIPI", ""),
                    service_type=r.get("SSERVISTIPI", ""),
                    departure_time=r.get("DT", ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


# ---------------------------------------------------------------------------
# 5. Announcements
# ---------------------------------------------------------------------------

def parse_announcements_xml(xml_text: str) -> list[Announcement]:
    """Parse GetDuyurular_json SOAP response."""
    records = _extract_soap_json(xml_text, "GetDuyurular_jsonResult")
    result: list[Announcement] = []
    for r in records:
        try:
            result.append(
                Announcement(
                    route_code=r.get("HATKODU", ""),
                    route_name=r.get("HAT", ""),
                    type=r.get("TIP", ""),
                    updated_at=r.get("GUNCELLEME_SAATI", ""),
                    message=r.get("MESAJ", ""),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


# ---------------------------------------------------------------------------
# 6. Route stops (pure XML — NOT soap-wrapped JSON)
# ---------------------------------------------------------------------------

def parse_route_stops_xml(xml_text: str) -> list[RouteStop]:
    """Parse DurakDetay_GYY SOAP response.

    This endpoint returns real XML (NewDataSet/Table), NOT JSON-in-XML.
    XKOORDINATI = longitude, YKOORDINATI = latitude (confusingly named!).
    All child elements inherit xmlns="http://tempuri.org/" from the wrapper,
    so we must prefix every tag lookup with the namespace.
    """
    root = ET.fromstring(xml_text)
    ns = _TEMPURI
    result: list[RouteStop] = []
    for table in root.iter(f"{{{ns}}}Table"):
        try:
            x = table.findtext(f"{{{ns}}}XKOORDINATI")  # = longitude
            y = table.findtext(f"{{{ns}}}YKOORDINATI")  # = latitude
            if not x or not y:
                continue
            result.append(
                RouteStop(
                    route_code=table.findtext(f"{{{ns}}}HATKODU") or "",
                    direction=table.findtext(f"{{{ns}}}YON") or "",
                    sequence=int(table.findtext(f"{{{ns}}}SIRANO") or 0),
                    stop_code=table.findtext(f"{{{ns}}}DURAKKODU") or "",
                    stop_name=table.findtext(f"{{{ns}}}DURAKADI") or "",
                    latitude=float(y),
                    longitude=float(x),
                    district=table.findtext(f"{{{ns}}}ILCEADI"),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


# ---------------------------------------------------------------------------
# 7. Stop search (JSON — already dict from caller)
# ---------------------------------------------------------------------------

def parse_search_results(raw: dict) -> list[dict]:
    """Parse GetSearchItems JSON response — returns stops only."""
    return [
        {
            "dcode": str(item["Stationcode"]),
            "name": item["Name"],
            "path": item.get("Path"),
        }
        for item in raw.get("list", [])
        if "StationDetail" in (item.get("Path") or "")
    ]


def parse_route_search_results(raw: dict) -> list[dict]:
    """Parse GetSearchItems JSON response — returns routes only.

    Routes have Path=/RouteDetail and Stationcode=0.
    The route code is in the Code field (not wrapped in HTML for routes).
    """
    results = []
    for item in raw.get("list", []):
        path = item.get("Path") or ""
        if "RouteDetail" not in path:
            continue
        # Extract hat_kodu from Path: /RouteDetail?hkod=500T&...
        hat_kodu = item.get("Code", "").strip()
        # Code may contain HTML for stops but is plain text for routes
        if "<" in hat_kodu:
            continue
        results.append({
            "hat_kodu": hat_kodu,
            "name": item.get("Name", ""),
        })
    return results


# ---------------------------------------------------------------------------
# 8. Route metadata (GetAllRoute — JSON list)
# ---------------------------------------------------------------------------

def parse_route_metadata_json(raw: list | dict) -> list[dict]:
    """Parse GetAllRoute JSON response.

    Confirmed live fields (2026-02-27):
      GUZERGAH_ADI, GUZERGAH_GUZERGAH_KODU, GUZERGAH_YON,
      GUZERGAH_DEPAR_NO, GUZERGAH_GUZERGAH_ADI
    HAT_HAT_ADI and HAT_HAT_KODU are always null — ignore.
    """
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    results = []
    for r in raw:
        try:
            results.append({
                "direction_name": (r.get("GUZERGAH_GUZERGAH_ADI") or "").strip(),
                "full_name": (r.get("GUZERGAH_ADI") or "").strip(),
                "variant_code": r.get("GUZERGAH_GUZERGAH_KODU") or "",
                "direction": int(r.get("GUZERGAH_YON") or 0),
                "depar_no": int(r.get("GUZERGAH_DEPAR_NO") or 0),
            })
        except (TypeError, ValueError):
            continue
    return results
