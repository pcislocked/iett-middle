"""Pure parser unit tests — no HTTP, no HA required."""
from __future__ import annotations

from tests.conftest import (
    ALL_STOPS_XML,
    ANNOUNCEMENTS_XML,
    ARRIVALS_HTML,
    FLEET_ALL_XML,
    GARAGE_XML,
    ROUTE_FLEET_EMPTY_XML,
    ROUTE_FLEET_XML,
    ROUTE_METADATA_JSON,
    ROUTE_SEARCH_JSON,
    ROUTE_STOPS_HTML,
    ROUTE_STOPS_XML,
    ROUTES_BY_STATION_HTML,
    SCHEDULE_XML,
    SEARCH_JSON,
    STOP_DETAIL_XML,
)
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
    parse_route_stops_xml,
    parse_routes_from_html,
    parse_search_results,
    parse_stop_arrivals_html,
    parse_stop_detail_xml,
)


# ---------------------------------------------------------------------------
# Fleet parsers
# ---------------------------------------------------------------------------

class TestParseAllFleet:
    def test_returns_list(self):
        buses = parse_all_fleet_xml(FLEET_ALL_XML)
        assert isinstance(buses, list)
        assert len(buses) == 1

    def test_field_mapping(self):
        bus = parse_all_fleet_xml(FLEET_ALL_XML)[0]
        assert bus.kapino == "A-001"
        assert bus.plate == "34 HO 1000"
        assert abs(bus.latitude - 41.1073516666667) < 0.0001
        assert abs(bus.longitude - 29.0155733333333) < 0.0001
        assert bus.speed == 0
        assert bus.last_seen == "00:19:57"

    def test_empty_result_tag(self):
        # Empty fleet response should return empty list
        result = parse_all_fleet_xml(
            "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
            "<soap:Body><GetFiloAracKonum_jsonResponse xmlns='http://tempuri.org/'>"
            "<GetFiloAracKonum_jsonResult>[]</GetFiloAracKonum_jsonResult>"
            "</GetFiloAracKonum_jsonResponse></soap:Body></soap:Envelope>"
        )
        assert result == []


class TestParseRouteFleet:
    def test_returns_list(self):
        buses = parse_route_fleet_xml(ROUTE_FLEET_XML)
        assert len(buses) == 1

    def test_field_mapping(self):
        bus = parse_route_fleet_xml(ROUTE_FLEET_XML)[0]
        assert bus.kapino == "C-325"
        assert bus.route_code == "500T"
        assert bus.route_name == "TUZLA ŞİFA MAHALLESİ - 4. LEVENT METRO"
        assert bus.direction == "ŞİFA SONDURAK"
        assert bus.nearest_stop == "113333"
        assert bus.plate is None  # not present in route-fleet endpoint

    def test_empty_response(self):
        assert parse_route_fleet_xml(ROUTE_FLEET_EMPTY_XML) == []


# ---------------------------------------------------------------------------
# Arrivals HTML
# ---------------------------------------------------------------------------

class TestParseArrivals:
    def test_returns_two_arrivals(self):
        arrivals = parse_stop_arrivals_html(ARRIVALS_HTML)
        assert len(arrivals) == 2

    def test_first_arrival_fields(self):
        a = parse_stop_arrivals_html(ARRIVALS_HTML)[0]
        assert a.route_code == "500T"
        assert a.eta_minutes == 4
        assert "4.LEVENT METRO" in a.destination

    def test_skips_header(self):
        # The content-header div must not produce an arrival
        arrivals = parse_stop_arrivals_html(ARRIVALS_HTML)
        route_codes = [a.route_code for a in arrivals]
        assert "Duraktan" not in route_codes

    def test_empty_html(self):
        assert parse_stop_arrivals_html("") == []


# ---------------------------------------------------------------------------
# Routes from stop HTML
# ---------------------------------------------------------------------------

class TestParseRoutesFromHtml:
    def test_returns_set(self):
        routes = parse_routes_from_html(ROUTES_BY_STATION_HTML)
        assert "14M" in routes
        assert "15TY" in routes

    def test_empty(self):
        assert parse_routes_from_html("") == set()


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

class TestParseSchedule:
    def test_basic(self):
        deps = parse_route_schedule_xml(SCHEDULE_XML)
        assert len(deps) == 1
        d = deps[0]
        assert d.route_code == "500T"
        assert d.direction == "D"
        assert d.day_type == "H"
        assert d.departure_time == "05:55"


# ---------------------------------------------------------------------------
# Announcements
# ---------------------------------------------------------------------------

class TestParseAnnouncements:
    def test_basic(self):
        anns = parse_announcements_xml(ANNOUNCEMENTS_XML)
        assert len(anns) == 1
        a = anns[0]
        assert a.route_code == "500T"
        assert "TRAFİK" in a.message


# ---------------------------------------------------------------------------
# Route stops (pure XML — not soap-json)
# ---------------------------------------------------------------------------

class TestParseRouteStops:
    def test_basic(self):
        stops = parse_route_stops_xml(ROUTE_STOPS_XML)
        assert len(stops) == 1
        s = stops[0]
        assert s.stop_code == "301341"
        assert s.stop_name == "4.LEVENT METRO"
        # XKOORDINATI=29.007309 (lon), YKOORDINATI=41.084170 (lat)
        assert abs(s.latitude - 41.084170) < 0.0001
        assert abs(s.longitude - 29.007309) < 0.0001
        assert s.district == "Sisli"


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------

class TestParseSearchResults:
    def test_filters_to_stops_only(self):
        results = parse_search_results(SEARCH_JSON)
        # Route "14M" entry has /RouteDetail path — must be excluded
        assert len(results) == 1
        assert results[0]["dcode"] == "220602"

    def test_empty_list(self):
        assert parse_search_results({"list": []}) == []


# ---------------------------------------------------------------------------
# Route search results
# ---------------------------------------------------------------------------

class TestParseRouteSearchResults:
    def test_filters_to_routes_only(self):
        results = parse_route_search_results(ROUTE_SEARCH_JSON)
        assert len(results) == 1
        assert results[0]["hat_kodu"] == "500T"

    def test_excludes_html_code_entries(self):
        # Entries where Code contains HTML (<img>) are stops, must be excluded
        results = parse_route_search_results(ROUTE_SEARCH_JSON)
        assert all("<" not in r["hat_kodu"] for r in results)

    def test_empty(self):
        assert parse_route_search_results({"list": []}) == []


# ---------------------------------------------------------------------------
# Route metadata
# ---------------------------------------------------------------------------

class TestParseRouteMetadata:
    def test_returns_both_directions(self):
        results = parse_route_metadata_json(ROUTE_METADATA_JSON)
        assert len(results) == 2

    def test_field_mapping(self):
        r = parse_route_metadata_json(ROUTE_METADATA_JSON)[0]
        assert r["variant_code"] == "500T_D_D0"
        assert r["direction"] == 0
        assert r["depar_no"] == 1
        assert "4. LEVENT METRO" in r["direction_name"]

    def test_accepts_single_dict(self):
        results = parse_route_metadata_json(ROUTE_METADATA_JSON[0])
        assert len(results) == 1

    def test_empty_list(self):
        assert parse_route_metadata_json([]) == []


# ---------------------------------------------------------------------------
# Garages
# ---------------------------------------------------------------------------

class TestParseGarages:
    def test_returns_both_garages(self):
        garages = parse_garages_xml(GARAGE_XML)
        assert len(garages) == 2

    def test_field_mapping(self):
        g = parse_garages_xml(GARAGE_XML)[0]
        assert g.name == "IKITELLI GARAJ"
        assert g.code == "IKT"
        assert abs(g.latitude - 41.0620) < 0.001
        assert abs(g.longitude - 28.7980) < 0.001

    def test_empty_result(self):
        xml = (
            "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
            "<soap:Body><GetGaraj_jsonResponse xmlns='http://tempuri.org/'>"
            "<GetGaraj_jsonResult>[]</GetGaraj_jsonResult>"
            "</GetGaraj_jsonResponse></soap:Body></soap:Envelope>"
        )
        assert parse_garages_xml(xml) == []

    def test_skips_missing_coords(self):
        xml = (
            "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
            "<soap:Body><GetGaraj_jsonResponse xmlns='http://tempuri.org/'>"
            '<GetGaraj_jsonResult>[{"GarajAdi":"NO COORDS"}]</GetGaraj_jsonResult>'
            "</GetGaraj_jsonResponse></soap:Body></soap:Envelope>"
        )
        assert parse_garages_xml(xml) == []


# ---------------------------------------------------------------------------
# Stop detail (single stop)
# ---------------------------------------------------------------------------

class TestParseStopDetail:
    def test_returns_stop(self):
        detail = parse_stop_detail_xml(STOP_DETAIL_XML, "220602")
        assert detail is not None
        assert detail.dcode == "220602"
        assert detail.name == "AHMET MİTHAT EFENDİ"

    def test_coords_from_wkt(self):
        detail = parse_stop_detail_xml(STOP_DETAIL_XML, "220602")
        assert detail is not None
        assert detail.latitude is not None
        assert detail.longitude is not None
        assert abs(detail.latitude - 41.1234) < 0.001
        assert abs(detail.longitude - 29.0871) < 0.001

    def test_returns_none_on_empty(self):
        xml = (
            "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
            "<soap:Body><GetDurak_jsonResponse xmlns='http://tempuri.org/'>"
            "<GetDurak_jsonResult>[]</GetDurak_jsonResult>"
            "</GetDurak_jsonResponse></soap:Body></soap:Envelope>"
        )
        assert parse_stop_detail_xml(xml, "000") is None


# ---------------------------------------------------------------------------
# All stops bulk dump
# ---------------------------------------------------------------------------

class TestParseAllStops:
    def test_returns_all_three(self):
        stops = parse_all_stops_json(ALL_STOPS_XML)
        assert len(stops) == 3

    def test_field_mapping(self):
        stops = parse_all_stops_json(ALL_STOPS_XML)
        levent = next(s for s in stops if s.stop_code == "301341")
        assert levent.stop_name == "4.LEVENT METRO"
        assert abs(levent.latitude - 41.0842) < 0.001
        assert abs(levent.longitude - 29.0073) < 0.001
        assert levent.district == "Sisli"
        assert levent.direction is None  # SYON is null for this stop

    def test_direction_field(self):
        stops = parse_all_stops_json(ALL_STOPS_XML)
        oktay = next(s for s in stops if s.stop_code == "100022")
        assert oktay.direction == "BEYLIKDÜZÜ"
        menekse = next(s for s in stops if s.stop_code == "100151")
        assert menekse.direction == "AVCILAR"

    def test_skips_missing_point(self):
        xml = (
            "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
            "<soap:Body><GetDurak_jsonResponse xmlns='http://tempuri.org/'>"
            '<GetDurak_jsonResult>[{"SDURAKKODU":1,"SDURAKADI":"X","KOORDINAT":"INVALID","ILCEADI":"Y"}]</GetDurak_jsonResult>'
            "</GetDurak_jsonResponse></soap:Body></soap:Envelope>"
        )
        assert parse_all_stops_json(xml) == []

    def test_empty_response(self):
        xml = (
            "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
            "<soap:Body><GetDurak_jsonResponse xmlns='http://tempuri.org/'>"
            "<GetDurak_jsonResult>[]</GetDurak_jsonResult>"
            "</GetDurak_jsonResponse></soap:Body></soap:Envelope>"
        )
        assert parse_all_stops_json(xml) == []


# ---------------------------------------------------------------------------
# Schedule day_type normalisation
# ---------------------------------------------------------------------------

class TestScheduleDayTypeNorm:
    def test_i_normalised_to_h(self):
        xml = (
            "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
            "<soap:Body><GetPlanlananSeferSaati_jsonResponse xmlns='http://tempuri.org/'>"
            '<GetPlanlananSeferSaati_jsonResult>[{"SHATKODU":"14","HATADI":"X","SGUZERAH":"14_D","SYON":"D","SGUNTIPI":"I","GUZERGAH_ISARETI":null,"SSERVISTIPI":"X","DT":"06:00"}]</GetPlanlananSeferSaati_jsonResult>'
            "</GetPlanlananSeferSaati_jsonResponse></soap:Body></soap:Envelope>"
        )
        deps = parse_route_schedule_xml(xml)
        assert deps[0].day_type == "H"

    def test_weekend_unchanged(self):
        xml = (
            "<soap:Envelope xmlns:soap='http://schemas.xmlsoap.org/soap/envelope/'>"
            "<soap:Body><GetPlanlananSeferSaati_jsonResponse xmlns='http://tempuri.org/'>"
            '<GetPlanlananSeferSaati_jsonResult>[{"SHATKODU":"14","HATADI":"X","SGUZERAH":"14_D","SYON":"D","SGUNTIPI":"C","GUZERGAH_ISARETI":null,"SSERVISTIPI":"X","DT":"08:00"}]</GetPlanlananSeferSaati_jsonResult>'
            "</GetPlanlananSeferSaati_jsonResponse></soap:Body></soap:Envelope>"
        )
        deps = parse_route_schedule_xml(xml)
        assert deps[0].day_type == "C"


# ---------------------------------------------------------------------------
# Route stops HTML (GetStationForRoute)
# ---------------------------------------------------------------------------

class TestParseRouteStopsHtml:
    def test_returns_both_directions(self):
        stops = parse_route_stops_html(ROUTE_STOPS_HTML, "15F")
        directions = {s["direction"] for s in stops}
        assert "ŞAHİNKAYA GARAJI" in directions
        assert "KADIKÖY" in directions

    def test_total_stop_count(self):
        stops = parse_route_stops_html(ROUTE_STOPS_HTML, "15F")
        assert len(stops) == 3  # 2 in dir-1, 1 in dir-2

    def test_first_stop_fields(self):
        s = parse_route_stops_html(ROUTE_STOPS_HTML, "15F")[0]
        assert s["route_code"] == "15F"
        assert s["stop_code"] == "262541"
        assert s["stop_name"] == "ŞAHİNKAYA GARAJI"
        assert s["sequence"] == 1
        assert s["district"] == "Beykoz"
        assert s["direction"] == "ŞAHİNKAYA GARAJI"

    def test_no_lat_lon_in_output(self):
        stops = parse_route_stops_html(ROUTE_STOPS_HTML)
        assert "latitude" not in stops[0]
        assert "longitude" not in stops[0]

    def test_empty_html(self):
        assert parse_route_stops_html("") == []
