"""Pure parser unit tests — no HTTP, no HA required."""
from __future__ import annotations

from tests.conftest import (
    ANNOUNCEMENTS_XML,
    ARRIVALS_HTML,
    FLEET_ALL_XML,
    ROUTE_FLEET_EMPTY_XML,
    ROUTE_FLEET_XML,
    ROUTE_STOPS_XML,
    ROUTES_BY_STATION_HTML,
    SCHEDULE_XML,
    SEARCH_JSON,
)
from app.services.iett_parser import (
    parse_all_fleet_xml,
    parse_announcements_xml,
    parse_route_fleet_xml,
    parse_route_schedule_xml,
    parse_route_stops_xml,
    parse_routes_from_html,
    parse_search_results,
    parse_stop_arrivals_html,
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
