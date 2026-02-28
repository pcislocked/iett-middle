"""Pure parsing functions — no async, no HTTP, no HA.

All functions take raw text (XML/HTML string) and return typed lists.
"""
from __future__ import annotations

import json
import re
from typing import Any, cast
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from app.models.bus import Arrival, BusPosition
from app.models.garage import Garage
from app.models.route import Announcement, ScheduledDeparture
from app.models.stop import NearbyStop, RouteStop, StopDetail

_TEMPURI = "http://tempuri.org/"

# Kapı no (internal bus ID) pattern: one or more capital letters, dash, one or more digits.
# Covers all observed formats: A-001, C-325, C-123456, M-999, etc. (not vehicle license plates)
_KAPINO_RE = re.compile(r'\b[A-Z]+-\d+\b')


def _extract_soap_json(xml_text: str, result_tag: str) -> list[dict[str, Any]]:
    """Extract and JSON-parse the payload embedded inside a SOAP XML element."""
    root = ET.fromstring(xml_text)
    el = root.find(f".//{{{_TEMPURI}}}{result_tag}")
    if el is None or not el.text:
        return []
    try:
        data: Any = json.loads(el.text)
    except json.JSONDecodeError:
        return []
    return cast(list[dict[str, Any]], data) if isinstance(data, list) else []


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
            # IETT API uses Turkish dotless-ı: key may be "Hız", "HIZ", or "Hiz"
            speed_raw = next(
                (r[k] for k in ("Hiz", "H\u0131z", "HIZ", "hiz", "h\u0131z") if k in r),
                None,
            )
            speed = int(float(speed_raw)) if speed_raw is not None else None
            result.append(
                BusPosition(
                    kapino=r.get("KapiNo", ""),
                    plate=r.get("Plaka"),
                    latitude=float(r.get("Enlem", 0)),
                    longitude=float(r.get("Boylam", 0)),
                    speed=speed,
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
        kapino_m = _KAPINO_RE.search(item.get_text(" ", strip=True))
        result.append(
            Arrival(
                route_code=route_el.text.strip(),
                destination=p.text.replace(b.text, "").strip(),
                eta_minutes=int(eta_match.group(1)) if eta_match else None,
                eta_raw=b.text.strip(),
                kapino=kapino_m.group(0) if kapino_m else None,
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
    _day_norm = {"I": "H", "\u0130": "H", "i": "H", "\u0131": "H"}  # İş günü → H (Hafta içi)
    for r in records:
        try:
            raw_day: str = str(r.get("SGUNTIPI") or "")
            result.append(
                ScheduledDeparture(
                    route_code=r.get("SHATKODU", ""),
                    route_name=r.get("HATADI", ""),
                    route_variant=r.get("SGUZERAH", ""),
                    direction=r.get("SYON", ""),
                    day_type=_day_norm.get(raw_day, raw_day),
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

def _findtext_multi(el: ET.Element, *candidates: str) -> str | None:
    """Return the first non-empty text from a list of candidate tag names (tries bare and ns-prefixed)."""
    for tag in candidates:
        for variant in (tag, f"{{{_TEMPURI}}}{tag}"):
            val = el.findtext(variant)
            if val and val.strip():
                return val.strip()
    return None


def parse_route_stops_xml(xml_text: str) -> list[RouteStop]:
    """Parse DurakDetay_GYY SOAP response.

    Returns real XML (NewDataSet/Table), NOT JSON-in-XML.
    The .NET DataSet serializer may or may not propagate the tempuri
    namespace into child elements, so we try both variants.
    Coordinate fields vary: XKOORDINATI/YKOORDINATI or XKOORT/YKOORT.
    X = longitude, Y = latitude.
    """
    root = ET.fromstring(xml_text)
    ns = _TEMPURI
    # Collect Table elements — try ns-prefixed first, then bare
    tables = list(root.iter(f"{{{ns}}}Table")) or list(root.iter("Table"))
    result: list[RouteStop] = []
    for table in tables:
        try:
            x = _findtext_multi(table, "XKOORDINATI", "XKOORT", "CX")   # longitude
            y = _findtext_multi(table, "YKOORDINATI", "YKOORT", "CY")   # latitude
            if not x or not y:
                continue
            result.append(
                RouteStop(
                    route_code=_findtext_multi(table, "HATKODU") or "",
                    direction=_findtext_multi(table, "YON") or "",
                    sequence=int(_findtext_multi(table, "SIRANO", "SIRA") or 0),
                    stop_code=_findtext_multi(table, "DURAKKODU") or "",
                    stop_name=_findtext_multi(table, "DURAKADI") or "",
                    latitude=float(y),
                    longitude=float(x),
                    district=_findtext_multi(table, "ILCEADI"),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


# ---------------------------------------------------------------------------
# 6.5. Route stops (HTML — GetStationForRoute)
# ---------------------------------------------------------------------------

def parse_route_stops_html(html: str, hat_kodu: str = "") -> list[dict[str, Any]]:
    """Parse GetStationForRoute HTML fragment.

    Returns a single flat list of stop dicts collected from both direction
    columns in document order.  Each dict includes a ``direction`` field
    indicating which column (departure terminal name) the stop belongs to.
    Coordinates are NOT in the HTML — the caller (IettClient) enriches them
    from the in-memory stop index.

    Each returned dict matches the RouteStop model fields, minus lat/lon.
    """
    soup = BeautifulSoup(html, "html.parser")
    result: list[dict[str, Any]] = []
    for col in soup.select("div.col-md-6"):
        header_el = col.select_one("div.line-pass-header")
        if not header_el:
            continue
        header_text = header_el.get_text(strip=True)
        # "ŞAHİNKAYA GARAJI KALKIŞ" → "ŞAHİNKAYA GARAJI"
        direction = header_text.removesuffix(" KALKIŞ").strip()
        for item in col.select("div.line-pass-item"):
            a = item.select_one("a[href]")
            p = item.select_one("p")
            if not a or not p:
                continue
            href = str(a.get("href", ""))
            dkod_match = re.search(r"dkod=(\d+)", href)
            if not dkod_match:
                continue
            stop_code = dkod_match.group(1)
            span = p.select_one("span")
            district: str | None = None
            if span:
                district = span.get_text(strip=True).lstrip("- ").strip() or None
                span.decompose()
            full_text = p.get_text(strip=True)  # "1. STOP NAME"
            seq_match = re.match(r"^(\d+)\.\s*(.*)", full_text)
            if not seq_match:
                continue
            result.append(
                {
                    "route_code": hat_kodu,
                    "direction": direction,
                    "sequence": int(seq_match.group(1)),
                    "stop_code": stop_code,
                    "stop_name": seq_match.group(2).strip(),
                    "district": district,
                }
            )
    return result


# ---------------------------------------------------------------------------
# 7. Stop search (JSON — already dict from caller)
# ---------------------------------------------------------------------------

def parse_search_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
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


def parse_route_search_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse GetSearchItems JSON response — returns routes only.

    Routes have Path=/RouteDetail and Stationcode=0.
    The route code is in the Code field (not wrapped in HTML for routes).
    """
    results: list[dict[str, Any]] = []
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

# ---------------------------------------------------------------------------
# 9. Garages (GetGaraj_json — HatDurakGuzergah.asmx)
# ---------------------------------------------------------------------------

def _coord_float(el: dict[str, Any], *keys: str) -> float | None:
    """Try multiple key names and return first parseable float."""
    for k in keys:
        raw = el.get(k)
        if raw is not None:
            try:
                v = float(str(raw).replace(',', '.'))
                if v != 0.0:
                    return v
            except ValueError:
                continue
    return None


def parse_garages_xml(xml_text: str) -> list[Garage]:
    """Parse GetGaraj_json SOAP response."""
    records = _extract_soap_json(xml_text, "GetGaraj_jsonResult")
    result: list[Garage] = []
    for r in records:
        try:
            # Try various name key conventions
            name = (
                r.get("GarajAdi") or r.get("GarajAd") or r.get("GARAJ_ADI")
                or r.get("Adi") or ""
            ).strip()
            if not name:
                continue
            code = (
                r.get("GarajKodu") or r.get("GarajNo") or r.get("GARAJ_KODU") or None
            )
            if code:
                code = str(code).strip() or None
            # Coordinates: IETT sometimes uses Boylam/Enlem, sometimes KoordinatX/Y
            # X=longitude, Y=latitude
            lon = _coord_float(r, "KoordinatX", "Boylam", "X", "boylam")
            lat = _coord_float(r, "KoordinatY", "Enlem", "Y", "enlem")
            if lat is None or lon is None:
                continue
            result.append(Garage(code=code, name=name, latitude=lat, longitude=lon))
        except (TypeError, ValueError):
            continue
    return result


# ---------------------------------------------------------------------------
# 10. Stop detail (GetDurak_json — HatDurakGuzergah.asmx)
# ---------------------------------------------------------------------------

def parse_stop_detail_xml(xml_text: str, dcode: str) -> StopDetail | None:
    """Parse GetDurak_json SOAP response into a StopDetail."""
    records = _extract_soap_json(xml_text, "GetDurak_jsonResult")
    if not records:
        return None
    r = records[0]
    try:
        name = (
            r.get("DurakAdi") or r.get("DURAK_ADI") or r.get("Ad")
            or r.get("SDURAKADI") or ""
        ).strip()
        if not name:
            return None
        # Some responses use WKT KOORDINAT, others use separate KoordinatX/Y
        coord_str: str = str(r.get("KOORDINAT", ""))
        wkt_m = _POINT_RE.match(coord_str)
        if wkt_m:
            lon = float(wkt_m.group(1))
            lat = float(wkt_m.group(2))
        else:
            lon = _coord_float(r, "KoordinatX", "Boylam", "X", "boylam")
            lat = _coord_float(r, "KoordinatY", "Enlem", "Y", "enlem")
        return StopDetail(
            dcode=dcode,
            name=name,
            latitude=lat,
            longitude=lon,
            direction=((r.get("SYON") or "").strip() or None),
        )
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 11. All stops bulk dump (GetDurak_json — empty DurakKodu)
# ---------------------------------------------------------------------------

_POINT_RE = re.compile(r"POINT \(([\d.]+) ([\d.]+)\)")


def parse_all_stops_json(xml_text: str) -> list[NearbyStop]:
    """Parse GetDurak_json (empty DurakKodu) → full stop catalogue.

    Each record has:
      SDURAKKODU  – int stop code
      SDURAKADI   – str stop name
      KOORDINAT   – WKT "POINT (lon lat)"
      ILCEADI     – district name
      SYON        – str direction/terminus label (optional, may be absent or blank)
    """
    records = _extract_soap_json(xml_text, "GetDurak_jsonResult")
    result: list[NearbyStop] = []
    for r in records:
        try:
            coord_str = r.get("KOORDINAT", "")
            m = _POINT_RE.match(coord_str)
            if not m:
                continue
            lon = float(m.group(1))
            lat = float(m.group(2))
            stop_code = str(r.get("SDURAKKODU", "")).strip()
            if not stop_code:
                continue
            result.append(
                NearbyStop(
                    stop_code=stop_code,
                    stop_name=(r.get("SDURAKADI") or "").strip(),
                    latitude=lat,
                    longitude=lon,
                    district=(r.get("ILCEADI") or None),
                    direction=((r.get("SYON") or "").strip() or None),
                )
            )
        except (TypeError, ValueError):
            continue
    return result


# ---------------------------------------------------------------------------
# 12. Route metadata (GetAllRoute — JSON list)
# ---------------------------------------------------------------------------

def parse_route_metadata_json(raw: list[Any] | dict[str, Any]) -> list[dict[str, Any]]:
    """Parse GetAllRoute JSON response.

    Confirmed live fields (2026-02-27):
      GUZERGAH_ADI, GUZERGAH_GUZERGAH_KODU, GUZERGAH_YON,
      GUZERGAH_DEPAR_NO, GUZERGAH_GUZERGAH_ADI
    HAT_HAT_ADI and HAT_HAT_KODU are always null — ignore.
    """
    if isinstance(raw, dict):
        raw = [raw]
    results: list[dict[str, Any]] = []
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
