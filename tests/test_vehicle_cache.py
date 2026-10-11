"""Unit tests for app.services.vehicle_cache."""

from __future__ import annotations

from app.services import vehicle_cache


class TestVehicleCache:
    def setup_method(self) -> None:
        vehicle_cache.clear_cache()

    def teardown_method(self) -> None:
        vehicle_cache.clear_cache()

    def test_upsert_and_get_by_kapino(self) -> None:
        vehicle_cache.upsert_vehicle("M-2001", "34 TP 2001", "hash_2001")
        # Direct kapino
        res = vehicle_cache.get_vehicle("M-2001")
        assert res is not None
        assert res["door_number"] == "M-2001"
        assert res["plate"] == "34 TP 2001"
        assert res["hash_value"] == "hash_2001"

        # Dash-agnostic kapino
        res2 = vehicle_cache.get_vehicle("M2001")
        assert res2 is not None
        assert res2["door_number"] == "M-2001"

        # Space and dash agnostic plate
        res3 = vehicle_cache.get_vehicle("34TP2001")
        assert res3 is not None
        assert res3["door_number"] == "M-2001"

    def test_get_hash_dynamic_no_cache(self) -> None:
        vehicle_cache.upsert_vehicle("C-1753", "34 HO 1753", "fresh_hash")
        # ARAC dynamic hashes are single-use tokens; get_hash always returns None
        assert vehicle_cache.get_hash("C-1753", max_age=60.0) is None
        assert vehicle_cache.get_hash("C-1753", max_age=0.001) is None

    def test_batch_upsert_and_search(self) -> None:
        items = [
            {"doorNumber": "O-3001", "plate": "34 OA 3001", "value": "h1"},
            {"doorNumber": "O-3002", "plate": "34 OA 3002", "value": "h2"},
            {"doorNumber": "K-1234", "plate": "34 KA 1234", "value": "h3"},
        ]
        vehicle_cache.upsert_vehicles_batch(items)

        search_res = vehicle_cache.search_vehicles("300", limit=10)
        assert len(search_res) == 2
        doors = [r["door_number"] for r in search_res]
        assert "O-3001" in doors
        assert "O-3002" in doors

    def test_invalid_query_returns_none_or_empty(self) -> None:
        assert vehicle_cache.get_vehicle("") is None
        assert vehicle_cache.get_vehicle("---") is None
        assert vehicle_cache.get_vehicle("NONEXISTENT") is None
        assert vehicle_cache.get_hash("") is None
        assert vehicle_cache.get_hash("NONEXISTENT") is None
        assert vehicle_cache.search_vehicles("") == []
        assert vehicle_cache.search_vehicles("   ") == []

    def test_batch_upsert_empty_or_invalid_items(self) -> None:
        vehicle_cache.upsert_vehicles_batch([])
        vehicle_cache.upsert_vehicles_batch(
            [{}, {"doorNumber": ""}, {"doorNumber": "---"}, {"plate": "34 ONLY"}]
        )
        assert vehicle_cache.search_vehicles("ONLY") == []

    def test_upsert_vehicle_conflict_and_hash_none(self) -> None:
        # Upsert with empty clean string
        vehicle_cache.upsert_vehicle("---")
        assert vehicle_cache.get_vehicle("---") is None

        # Insert without hash
        vehicle_cache.upsert_vehicle("B-500", "34 BB 500")
        assert vehicle_cache.get_hash("B-500") is None
        veh = vehicle_cache.get_vehicle("B-500")
        assert veh is not None
        assert veh["plate"] == "34 BB 500"

        # Update existing vehicle with new plate and hash
        vehicle_cache.upsert_vehicle("B-500", "34 BB 501", "new_hash")
        veh_updated = vehicle_cache.get_vehicle("B-500")
        assert veh_updated is not None
        assert veh_updated["plate"] == "34 BB 501"
        assert vehicle_cache.get_hash("B-500") is None

        # Test close_connection and reconnect
        vehicle_cache.close_connection()
        conn = vehicle_cache.get_db_connection()
        assert conn is not None
