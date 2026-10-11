"""Persistent SQLite cache for vehicle kapino <-> plate mappings and ARAC hashes."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "vehicles.db"

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _clean_str(val: str | None) -> str:
    if not val:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(val).upper())


def get_db_connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        import os
        import sys

        is_test = "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules
        try:
            if is_test:
                conn = sqlite3.connect(":memory:", check_same_thread=False)
            else:
                DB_PATH.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        except Exception as exc:
            logger.warning("Could not open %s, using :memory: fallback: %s", DB_PATH, exc)
            conn = sqlite3.connect(":memory:", check_same_thread=False)

        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vehicle_cache (
                    clean_kapino TEXT PRIMARY KEY,
                    door_number TEXT NOT NULL,
                    plate TEXT,
                    clean_plate TEXT,
                    hash_value TEXT,
                    hash_updated_at REAL,
                    updated_at REAL NOT NULL
                );
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vc_clean_plate ON vehicle_cache(clean_plate);"
            )
        _conn = conn
    return _conn


def clear_cache() -> None:
    conn = get_db_connection()
    with _lock, conn:
        conn.execute("DELETE FROM vehicle_cache;")


def upsert_vehicle(
    door_number: str,
    plate: str | None = None,
    hash_value: str | None = None,
) -> None:
    clean_k = _clean_str(door_number)
    if not clean_k:
        return

    clean_p = _clean_str(plate) if plate else None
    now = time.time()
    hash_ts = now if hash_value else None

    conn = get_db_connection()
    with _lock, conn:
        conn.execute(
            """
            INSERT INTO vehicle_cache (
                clean_kapino, door_number, plate, clean_plate, hash_value, hash_updated_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(clean_kapino) DO UPDATE SET
                door_number = COALESCE(excluded.door_number, vehicle_cache.door_number),
                plate = COALESCE(excluded.plate, vehicle_cache.plate),
                clean_plate = COALESCE(excluded.clean_plate, vehicle_cache.clean_plate),
                hash_value = COALESCE(excluded.hash_value, vehicle_cache.hash_value),
                hash_updated_at = COALESCE(excluded.hash_updated_at, vehicle_cache.hash_updated_at),
                updated_at = excluded.updated_at;
            """,
            (
                clean_k,
                door_number.strip(),
                plate.strip() if plate else None,
                clean_p,
                hash_value,
                hash_ts,
                now,
            ),
        )


def upsert_vehicles_batch(items: list[dict[str, Any]]) -> None:
    if not items:
        return

    now = time.time()
    rows = []
    for item in items:
        door = item.get("doorNumber") or item.get("door_number") or item.get("kapino")
        if not door:
            continue
        clean_k = _clean_str(door)
        if not clean_k:
            continue
        plate = item.get("plate") or item.get("numberPlate")
        clean_p = _clean_str(plate) if plate else None
        h_val = item.get("value") or item.get("hash_value")
        h_ts = now if h_val else None

        rows.append(
            (
                clean_k,
                str(door).strip(),
                str(plate).strip() if plate else None,
                clean_p,
                str(h_val) if h_val else None,
                h_ts,
                now,
            )
        )

    if not rows:
        return

    conn = get_db_connection()
    with _lock, conn:
        conn.executemany(
            """
            INSERT INTO vehicle_cache (
                clean_kapino, door_number, plate, clean_plate, hash_value, hash_updated_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(clean_kapino) DO UPDATE SET
                door_number = COALESCE(excluded.door_number, vehicle_cache.door_number),
                plate = COALESCE(excluded.plate, vehicle_cache.plate),
                clean_plate = COALESCE(excluded.clean_plate, vehicle_cache.clean_plate),
                hash_value = COALESCE(excluded.hash_value, vehicle_cache.hash_value),
                hash_updated_at = COALESCE(excluded.hash_updated_at, vehicle_cache.hash_updated_at),
                updated_at = excluded.updated_at;
            """,
            rows,
        )


def get_vehicle(query: str) -> dict[str, Any] | None:
    clean_q = _clean_str(query)
    if not clean_q:
        return None

    conn = get_db_connection()
    with _lock:
        row = conn.execute(
            """
            SELECT clean_kapino, door_number, plate, clean_plate, hash_value, hash_updated_at, updated_at
            FROM vehicle_cache
            WHERE clean_kapino = ? OR clean_plate = ?
            LIMIT 1;
            """,
            (clean_q, clean_q),
        ).fetchone()

    if row:
        return dict(row)
    return None


def get_hash(query: str, max_age: float = 600.0) -> str | None:
    veh = get_vehicle(query)
    if not veh:
        return None

    hash_val = veh.get("hash_value")
    hash_ts = veh.get("hash_updated_at")
    if hash_val and hash_ts and (time.time() - float(hash_ts) < max_age):
        return str(hash_val)
    return None


def search_vehicles(query: str, limit: int = 10) -> list[dict[str, Any]]:
    clean_q = _clean_str(query)
    if not clean_q:
        return []

    conn = get_db_connection()
    like_pattern = f"%{clean_q}%"
    with _lock:
        rows = conn.execute(
            """
            SELECT clean_kapino, door_number, plate, clean_plate, hash_value, updated_at
            FROM vehicle_cache
            WHERE clean_kapino LIKE ? OR clean_plate LIKE ?
            ORDER BY
                CASE WHEN clean_kapino = ? OR clean_plate = ? THEN 0 ELSE 1 END,
                updated_at DESC
            LIMIT ?;
            """,
            (like_pattern, like_pattern, clean_q, clean_q, limit),
        ).fetchall()

    return [dict(r) for r in rows]
