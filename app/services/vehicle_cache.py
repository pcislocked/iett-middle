"""Persistent SQLite cache for vehicle kapino <-> plate mappings and ARAC hashes."""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "vehicles.db"

_local = threading.local()
_init_lock = threading.Lock()
_write_lock = threading.Lock()
_initialized = False
_primary_conn: sqlite3.Connection | None = None


_is_memory = False


def _clean_str(val: str | None) -> str:
    if not val:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(val).upper())


def get_db_connection() -> sqlite3.Connection:
    global _primary_conn, _initialized, _is_memory
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.execute("SELECT 1;")
            return conn
        except Exception:
            conn = None

    db_target = settings.vehicle_cache_path
    _is_memory = db_target == ":memory:"

    if _is_memory:
        conn_str = "file:vehicle_cache_mem?mode=memory&cache=shared"
        uri = True
    else:
        p = Path(db_target)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent.parent / p
        p.parent.mkdir(parents=True, exist_ok=True)
        conn_str = str(p)
        uri = False

    try:
        new_conn = sqlite3.connect(
            conn_str,
            uri=uri,
            timeout=30.0,
            check_same_thread=False,
        )
        if not _is_memory:
            new_conn.execute("CREATE TABLE IF NOT EXISTS _test_write (id INT);")
    except Exception as exc:
        logger.warning("Could not open %s (%s), trying /tmp/vehicles.db", db_target, exc)
        try:
            p_tmp = Path("/tmp/vehicles.db")
            new_conn = sqlite3.connect(
                str(p_tmp),
                uri=False,
                timeout=30.0,
                check_same_thread=False,
            )
            new_conn.execute("CREATE TABLE IF NOT EXISTS _test_write (id INT);")
        except Exception as exc2:
            logger.warning(
                "Could not open /tmp fallback either (%s), using in-memory shared fallback", exc2
            )
            _is_memory = True
            new_conn = sqlite3.connect(
                "file:vehicle_cache_fallback?mode=memory&cache=shared",
                uri=True,
                timeout=30.0,
                check_same_thread=False,
            )

    new_conn.row_factory = sqlite3.Row

    with _init_lock:
        if not _initialized:
            if _is_memory and _primary_conn is None:
                _primary_conn = new_conn
            with new_conn:
                if not _is_memory:
                    new_conn.execute("PRAGMA journal_mode = WAL;")
                    new_conn.execute("PRAGMA synchronous = NORMAL;")
                new_conn.execute(
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
                new_conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_vc_clean_plate ON vehicle_cache(clean_plate);"
                )
            _initialized = True

    _local.conn = new_conn
    return new_conn


def close_connection() -> None:
    """Close and release the active SQLite connection(s)."""
    global _primary_conn, _initialized, _is_memory
    with _init_lock:
        conn = getattr(_local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            _local.conn = None

        if _primary_conn is not None:
            try:
                _primary_conn.close()
            except Exception:
                pass
            _primary_conn = None
        _initialized = False


def clear_cache() -> None:
    conn = get_db_connection()
    with _write_lock, conn:
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
    with _write_lock, conn:
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
    with _write_lock, conn:
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
    if _is_memory:
        with _write_lock:
            row = conn.execute(
                """
                SELECT clean_kapino, door_number, plate, clean_plate, hash_value, hash_updated_at, updated_at
                FROM vehicle_cache
                WHERE clean_kapino = ? OR clean_plate = ?
                LIMIT 1;
                """,
                (clean_q, clean_q),
            ).fetchone()
    else:
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


def get_hash(query: str, max_age: float = 600.0) -> str | None:  # noqa: ARG001
    """ARAC dynamic hashes are single-use tokens that expire immediately.

    This function is retained for API backward compatibility and always returns None.
    """
    return None


def search_vehicles(query: str, limit: int = 10) -> list[dict[str, Any]]:
    clean_q = _clean_str(query)
    if not clean_q:
        return []

    conn = get_db_connection()
    like_pattern = f"%{clean_q}%"
    if _is_memory:
        with _write_lock:
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
    else:
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
