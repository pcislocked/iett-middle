"""Unit tests for app/services/normalizers/*.

Fixture data is derived from real API captures; no network calls are made.
"""
from __future__ import annotations

from app.services.normalizers import arrivals, positions, route_stops, schedule, stops


# ===========================================================================
# arrivals
# ===========================================================================

class TestArrivalsFromNtcapiYbs:
    """from_ntcapi_ybs maps raw ybs dict → CanonicalArrival."""

    ITEM = {
        "hatkodu": "500T",
        "hattip": "4.LEVENT METRO",
        "hatadi": "TUZLA - 4.LEVENT",
        "dakika": "3",
        "saat": "3 dk",
        "kapino": "C-325",
        "son_konum": "29.0109,41.0819",   # LON,LAT — must be swapped!
        "son_hiz": "25",
        "son_konum_saati": "2026-03-01 14:22:00",
        "usb": "1",
        "wifi": "0",
        "klima": "1",
        "engelli": None,
    }

    def test_basic_fields(self) -> None:
        result = arrivals.from_ntcapi_ybs(self.ITEM)
        assert result["route_code"] == "500T"
        assert result["destination"] == "4.LEVENT METRO"
        assert result["eta_minutes"] == 3
        assert result["eta_raw"] == "3 dk"
        assert result["kapino"] == "C-325"

    def test_son_konum_lon_first_swap(self) -> None:
        """son_konum is 'lon,lat' — normalizer must swap to (lat, lon)."""
        result = arrivals.from_ntcapi_ybs(self.ITEM)
        assert abs(result["lat"] - 41.0819) < 1e-4, "lat should be 41.0819"
        assert abs(result["lon"] - 29.0109) < 1e-4, "lon should be 29.0109"

    def test_speed_kmh_mapped(self) -> None:
        result = arrivals.from_ntcapi_ybs(self.ITEM)
        assert result["speed_kmh"] == 25

    def test_amenities_flags(self) -> None:
        result = arrivals.from_ntcapi_ybs(self.ITEM)
        am = result["amenities"]
        assert am["usb"] is True
        assert am["wifi"] is False
        assert am["ac"] is True
        assert am["accessible"] is None   # None input → None

    def test_source_tag(self) -> None:
        result = arrivals.from_ntcapi_ybs(self.ITEM)
        assert result["_source"] == "ntcapi_ybs"

    def test_plate_is_none(self) -> None:
        """Plate is not in ybs response — normalizer always sets None."""
        result = arrivals.from_ntcapi_ybs(self.ITEM)
        assert result["plate"] is None

    def test_malformed_son_konum_returns_none(self) -> None:
        item = {**self.ITEM, "son_konum": "bad-data"}
        result = arrivals.from_ntcapi_ybs(item)
        assert result["lat"] is None
        assert result["lon"] is None

    def test_missing_hattip_falls_back_to_hatadi(self) -> None:
        item = {**self.ITEM, "hattip": None}
        result = arrivals.from_ntcapi_ybs(item)
        assert result["destination"] == "TUZLA - 4.LEVENT"


class TestArrivalsFromIettHtml:
    """from_iett_html maps Arrival.model_dump() → CanonicalArrival (no position)."""

    ITEM = {
        "route_code": "500T",
        "destination": "4.LEVENT METRO",
        "eta_minutes": 5,
        "eta_raw": "5 dk",
        "kapino": None,
        "plate": None,
        "lat": None,
        "lon": None,
        "speed_kmh": None,
        "last_seen_ts": None,
        "amenities": None,
    }

    def test_core_fields(self) -> None:
        result = arrivals.from_iett_html(self.ITEM)
        assert result["route_code"] == "500T"
        assert result["eta_minutes"] == 5

    def test_no_position_data(self) -> None:
        result = arrivals.from_iett_html(self.ITEM)
        assert result["lat"] is None
        assert result["lon"] is None
        assert result["speed_kmh"] is None

    def test_source_tag(self) -> None:
        result = arrivals.from_iett_html(self.ITEM)
        assert result["_source"] == "iett_html"


# ===========================================================================
# positions
# ===========================================================================

class TestPositionsFromIettSoapFleet:
    """from_iett_soap_fleet maps CAPITALISED fleet keys → CanonicalBusPosition."""

    ITEM = {
        "KapiNo": "A-001",
        "Plaka": "34 HO 1000",
        "Enlem": "41.1073",
        "Boylam": "29.0155",
        "Hiz": "30",
        "Saat": "00:19:57",
        "HatKodu": "500T",
    }

    def test_basic_fields(self) -> None:
        result = positions.from_iett_soap_fleet(self.ITEM)
        assert result["kapino"] == "A-001"
        assert result["plate"] == "34 HO 1000"
        assert abs(result["lat"] - 41.1073) < 1e-4
        assert abs(result["lon"] - 29.0155) < 1e-4
        assert result["speed_kmh"] == 30
        assert result["last_seen"] == "00:19:57"
        assert result["route_code"] == "500T"

    def test_speed_turkish_i_variant(self) -> None:
        """Hız (with dotless ı) must also be recognised."""
        item = {**self.ITEM}
        del item["Hiz"]
        item["H\u0131z"] = "45"    # Hız
        result = positions.from_iett_soap_fleet(item)
        assert result["speed_kmh"] == 45

    def test_hatkodu_fallback_capitalised(self) -> None:
        item = {**self.ITEM, "HATKODU": "14M"}
        del item["HatKodu"]
        result = positions.from_iett_soap_fleet(item)
        assert result["route_code"] == "14M"

    def test_source_tag(self) -> None:
        result = positions.from_iett_soap_fleet(self.ITEM)
        assert result["_source"] == "iett_soap_fleet"


class TestPositionsFromIettSoapRouteFleet:
    """from_iett_soap_route_fleet maps lowercase route-fleet keys."""

    ITEM = {
        "kapino": "C-325",
        "enlem": "41.0819",
        "boylam": "29.0109",
        "son_konum_zamani": "2026-02-27 00:05:54",
        "hatkodu": "500T",
        "yon": "D",
        "yakinDurakKodu": "113333",
    }

    def test_basic_fields(self) -> None:
        result = positions.from_iett_soap_route_fleet(self.ITEM)
        assert result["kapino"] == "C-325"
        assert abs(result["lat"] - 41.0819) < 1e-4
        assert abs(result["lon"] - 29.0109) < 1e-4
        assert result["last_seen"] == "2026-02-27 00:05:54"
        assert result["route_code"] == "500T"
        assert result["direction"] == "D"
        assert result["nearest_stop_code"] == "113333"

    def test_no_plate_or_speed(self) -> None:
        result = positions.from_iett_soap_route_fleet(self.ITEM)
        assert result["plate"] is None
        assert result["speed_kmh"] is None

    def test_source_tag(self) -> None:
        result = positions.from_iett_soap_route_fleet(self.ITEM)
        assert result["_source"] == "iett_soap_route_fleet"


# ===========================================================================
# route_stops
# ===========================================================================

class TestRouteStopsFromNtcapiRouteRaw:
    """from_ntcapi_route maps raw mainGetRoute dict (GUZERGAH_* keys)."""

    ITEM = {
        "GUZERGAH_GUZERGAH_KODU": "500T_G_D0",
        "GUZERGAH_YON": "119",
        "GUZERGAH_SEGMENT_SIRA": 1,
        "DURAK_DURAK_KODU": "220602",
        "DURAK_ADI": "KADIKÖY VAPUR",
        "DURAK_GEOLOC": {"x": 29.023, "y": 40.989},
        "ILCELER_ILCEADI": "Kadıköy",
    }

    def test_basic_fields(self) -> None:
        result = route_stops.from_ntcapi_route(self.ITEM)
        assert result["route_code"] == "500T_G_D0"
        assert result["sequence"] == 1
        assert result["stop_code"] == "220602"
        assert result["stop_name"] == "KADIKÖY VAPUR"
        assert result["district"] == "Kadıköy"

    def test_yon_119_maps_to_G(self) -> None:
        result = route_stops.from_ntcapi_route(self.ITEM)
        assert result["direction"] == "G"

    def test_yon_120_maps_to_D(self) -> None:
        item = {**self.ITEM, "GUZERGAH_YON": "120"}
        result = route_stops.from_ntcapi_route(item)
        assert result["direction"] == "D"

    def test_geoloc_x_is_lon_y_is_lat(self) -> None:
        result = route_stops.from_ntcapi_route(self.ITEM)
        assert abs(result["lat"] - 40.989) < 1e-4
        assert abs(result["lon"] - 29.023) < 1e-4

    def test_source_tag(self) -> None:
        result = route_stops.from_ntcapi_route(self.ITEM)
        assert result["_source"] == "ntcapi_route"


class TestRouteStopsFromNtcapiRouteProcessed:
    """from_ntcapi_route_processed maps pre-mapped ntcapi_client dicts."""

    ITEM = {
        "route_code": "500T_G_D0",
        "stop_code": "220602",
        "stop_name": "KADIKÖY VAPUR",
        "sequence": 1,
        "lat": 40.989,
        "lon": 29.023,
        "district": "Kadıköy",
        "direction_letter": "G",
    }

    def test_basic_fields(self) -> None:
        result = route_stops.from_ntcapi_route_processed(self.ITEM)
        assert result["route_code"] == "500T_G_D0"
        assert result["direction"] == "G"
        assert result["sequence"] == 1
        assert result["stop_code"] == "220602"
        assert abs(result["lat"] - 40.989) < 1e-4

    def test_source_tag(self) -> None:
        result = route_stops.from_ntcapi_route_processed(self.ITEM)
        assert result["_source"] == "ntcapi_route"


# ===========================================================================
# schedule
# ===========================================================================

class TestScheduleFromNtcapiTimetable:
    """from_ntcapi_timetable maps raw K_ORER_* keys."""

    ITEM = {
        "GUZERGAH_HAT_KODU": "14M",
        "K_ORER_SGUZERGAH": "14M_G_D0",
        "K_ORER_SYON": "G",
        "K_ORER_SGUNTIPI": "C",
        "K_ORER_SSERVISTIPI": "OAŞ",
        "K_ORER_DTSAATGIDIS": "2026-03-01 05:45:00",
        "K_ORER_SHAREKETTIPI": "A",
    }

    def test_basic_fields(self) -> None:
        result = schedule.from_ntcapi_timetable(self.ITEM)
        assert result["route_code"] == "14M"
        assert result["route_name"] == "14M"   # no separate name — route_code reused
        assert result["route_variant"] == "14M_G_D0"
        assert result["direction"] == "G"
        assert result["service_type"] == "OAŞ"

    def test_departure_time_extracted_as_hhmm(self) -> None:
        result = schedule.from_ntcapi_timetable(self.ITEM)
        assert result["departure_time"] == "05:45"

    def test_day_type_C_unchanged(self) -> None:
        result = schedule.from_ntcapi_timetable(self.ITEM)
        assert result["day_type"] == "C"

    def test_day_type_I_normalised_to_H(self) -> None:
        item = {**self.ITEM, "K_ORER_SGUNTIPI": "I"}
        result = schedule.from_ntcapi_timetable(item)
        assert result["day_type"] == "H"

    def test_day_type_dotted_I_normalised_to_H(self) -> None:
        """Turkish capital İ (U+0130) must also normalise to 'H'."""
        item = {**self.ITEM, "K_ORER_SGUNTIPI": "\u0130"}   # İ
        result = schedule.from_ntcapi_timetable(item)
        assert result["day_type"] == "H"

    def test_day_type_P_unchanged(self) -> None:
        item = {**self.ITEM, "K_ORER_SGUNTIPI": "P"}
        result = schedule.from_ntcapi_timetable(item)
        assert result["day_type"] == "P"

    def test_malformed_datetime_returns_none(self) -> None:
        item = {**self.ITEM, "K_ORER_DTSAATGIDIS": "bad"}
        result = schedule.from_ntcapi_timetable(item)
        assert result["departure_time"] is None

    def test_source_tag(self) -> None:
        result = schedule.from_ntcapi_timetable(self.ITEM)
        assert result["_source"] == "ntcapi_timetable"


class TestScheduleFromIettSoapSchedule:
    """from_iett_soap_schedule is a pass-through from ScheduledDeparture.model_dump()."""

    ITEM = {
        "route_code": "500T",
        "route_name": "TUZLA - LEVENT",
        "route_variant": "500T_D_D0",
        "direction": "D",
        "day_type": "H",
        "service_type": "ÖHO",
        "departure_time": "05:55",
    }

    def test_pass_through(self) -> None:
        result = schedule.from_iett_soap_schedule(self.ITEM)
        assert result["route_code"] == "500T"
        assert result["departure_time"] == "05:55"
        assert result["day_type"] == "H"

    def test_source_tag(self) -> None:
        result = schedule.from_iett_soap_schedule(self.ITEM)
        assert result["_source"] == "iett_soap_schedule"


# ===========================================================================
# stops
# ===========================================================================

class TestStopsFromNtcapiNearbyRaw:
    """from_ntcapi_nearby maps raw mainGetBusStopNearby dicts (DURAK_GEOLOC nested)."""

    ITEM = {
        "DURAK_DURAK_KODU": "301341",
        "DURAK_ADI": "KADIKÖY VAPUR",
        "DURAK_GEOLOC": {"x": 29.023, "y": 40.989},
        "DURAK_YON_BILGISI": "G",
        "ILCELER_ILCEADI": "Kadıköy",
        "DISTANCE": "120.5",
    }

    def test_basic_fields(self) -> None:
        result = stops.from_ntcapi_nearby(self.ITEM)
        assert result["stop_code"] == "301341"
        assert result["stop_name"] == "KADIKÖY VAPUR"
        assert result["direction"] == "G"
        assert result["district"] == "Kadıköy"

    def test_geoloc_y_is_lat_x_is_lon(self) -> None:
        result = stops.from_ntcapi_nearby(self.ITEM)
        assert abs(result["lat"] - 40.989) < 1e-4
        assert abs(result["lon"] - 29.023) < 1e-4

    def test_distance_parsed(self) -> None:
        result = stops.from_ntcapi_nearby(self.ITEM)
        assert abs(result["distance_m"] - 120.5) < 1e-2

    def test_source_tag(self) -> None:
        result = stops.from_ntcapi_nearby(self.ITEM)
        assert result["_source"] == "ntcapi_nearby"


class TestStopsFromNtcapiNearbyProcessed:
    """from_ntcapi_nearby_processed maps pre-processed ntcapi_client dicts."""

    ITEM = {
        "stop_code": "301341",
        "stop_name": "KADIKÖY VAPUR",
        "lat": 40.989,
        "lon": 29.023,
        "direction": "G",
    }

    def test_basic_fields(self) -> None:
        result = stops.from_ntcapi_nearby_processed(self.ITEM)
        assert result["stop_code"] == "301341"
        assert result["stop_name"] == "KADIKÖY VAPUR"
        assert result["direction"] == "G"
        assert abs(result["lat"] - 40.989) < 1e-4

    def test_district_and_distance_none(self) -> None:
        result = stops.from_ntcapi_nearby_processed(self.ITEM)
        assert result["district"] is None
        assert result["distance_m"] is None

    def test_source_tag(self) -> None:
        result = stops.from_ntcapi_nearby_processed(self.ITEM)
        assert result["_source"] == "ntcapi_nearby"
