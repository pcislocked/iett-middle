"""Persistent SQLite cache for IETT stops (Squid-style interception cache)."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

_local = threading.local()
_init_lock = threading.Lock()
_write_lock = threading.Lock()
_initialized = False
_primary_conn: sqlite3.Connection | None = None
_is_memory = False


def _tr_upper(s: str | None) -> str:
    if not s:
        return ""
    return str(s).replace("i", "İ").replace("ı", "I").upper()


def get_db_connection() -> sqlite3.Connection:
    global _primary_conn, _initialized, _is_memory
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.execute("SELECT 1;")
            return conn
        except Exception:
            conn = None

    db_target = settings.stop_cache_path
    _is_memory = db_target == ":memory:"

    if _is_memory:
        conn_str = "file:stop_cache_mem?mode=memory&cache=shared"
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
        logger.warning("Could not open %s (%s), trying /tmp/stops.db", db_target, exc)
        try:
            p_tmp = Path("/tmp/stops.db")
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
                "file:stop_cache_fallback?mode=memory&cache=shared",
                uri=True,
                timeout=30.0,
                check_same_thread=False,
            )

    new_conn.row_factory = sqlite3.Row
    new_conn.create_function("tr_upper", 1, _tr_upper)

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
                    CREATE TABLE IF NOT EXISTS stop_cache (
                        stop_code TEXT PRIMARY KEY,
                        stop_name TEXT NOT NULL,
                        latitude REAL,
                        longitude REAL,
                        district TEXT,
                        direction TEXT,
                        updated_at REAL NOT NULL
                    );
                    """
                )
                new_conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_sc_stop_name ON stop_cache(stop_name);"
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
        conn.execute("DELETE FROM stop_cache;")


def _clean_direction(direction: str | None) -> str | None:
    if not direction:
        return None
    d = direction.strip()
    # RouteStop directions ('G' = Gidiş, 'D' = Dönüş) are route variants, NOT physical stop directions
    if d.upper() in ("G", "D"):
        return None
    return d


def upsert_stop(
    stop_code: str,
    stop_name: str,
    latitude: float | None = None,
    longitude: float | None = None,
    district: str | None = None,
    direction: str | None = None,
) -> None:
    code = stop_code.strip()
    if not code:
        return

    name = stop_name.strip()
    dir_val = _clean_direction(direction)
    dist_val = district.strip() if district else None
    now = time.time()

    conn = get_db_connection()
    with _write_lock, conn:
        conn.execute(
            """
            INSERT INTO stop_cache (
                stop_code, stop_name, latitude, longitude, district, direction, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stop_code) DO UPDATE SET
                stop_name = CASE WHEN excluded.stop_name != '' THEN excluded.stop_name ELSE stop_cache.stop_name END,
                latitude = COALESCE(excluded.latitude, stop_cache.latitude),
                longitude = COALESCE(excluded.longitude, stop_cache.longitude),
                district = COALESCE(excluded.district, stop_cache.district),
                direction = COALESCE(excluded.direction, stop_cache.direction),
                updated_at = excluded.updated_at;
            """,
            (code, name, latitude, longitude, dist_val, dir_val, now),
        )


def upsert_stops_batch(items: list[dict[str, Any]]) -> None:
    if not items:
        return

    now = time.time()
    rows = []
    for item in items:
        code = str(item.get("stop_code") or item.get("dcode") or "").strip()
        if not code:
            continue
        name = str(item.get("stop_name") or item.get("name") or "").strip()

        lat = item.get("latitude")
        if lat is None:
            lat = item.get("lat")
        lon = item.get("longitude")
        if lon is None:
            lon = item.get("lon")

        lat_val = float(lat) if lat is not None else None
        lon_val = float(lon) if lon is not None else None

        district = item.get("district") or item.get("ilce")
        dist_val = str(district).strip() if district else None

        direction = item.get("direction")
        dir_val = _clean_direction(str(direction) if direction else None)

        rows.append((code, name, lat_val, lon_val, dist_val, dir_val, now))

    if not rows:
        return

    try:
        conn = get_db_connection()
        with _write_lock, conn:
            conn.executemany(
                """
                INSERT INTO stop_cache (
                    stop_code, stop_name, latitude, longitude, district, direction, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(stop_code) DO UPDATE SET
                    stop_name = CASE WHEN excluded.stop_name != '' THEN excluded.stop_name ELSE stop_cache.stop_name END,
                    latitude = COALESCE(excluded.latitude, stop_cache.latitude),
                    longitude = COALESCE(excluded.longitude, stop_cache.longitude),
                    district = COALESCE(excluded.district, stop_cache.district),
                    direction = COALESCE(excluded.direction, stop_cache.direction),
                    updated_at = excluded.updated_at;
                """,
                rows,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to upsert stop cache batch: %s", exc)


def get_stop(stop_code: str) -> dict[str, Any] | None:
    code = stop_code.strip()
    if not code:
        return None
    conn = get_db_connection()
    cursor = conn.execute(
        "SELECT stop_code, stop_name, latitude, longitude, district, direction, updated_at FROM stop_cache WHERE stop_code = ? LIMIT 1;",
        (code,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return dict(row)


def get_all_stops() -> list[dict[str, Any]]:
    conn = get_db_connection()
    cursor = conn.execute(
        "SELECT stop_code, stop_name, latitude, longitude, district, direction, updated_at FROM stop_cache;"
    )
    return [dict(row) for row in cursor.fetchall()]


def search_stops(query: str, limit: int = 20) -> list[dict[str, Any]]:
    q = query.strip()
    if not q:
        return []
    conn = get_db_connection()
    pattern = f"%{q}%"
    cursor = conn.execute(
        """
        SELECT stop_code, stop_name, latitude, longitude, district, direction, updated_at
        FROM stop_cache
        WHERE tr_upper(stop_name) LIKE tr_upper(?) OR stop_code LIKE ?
        ORDER BY CASE WHEN stop_code = ? THEN 0 WHEN tr_upper(stop_name) LIKE tr_upper(?) THEN 1 ELSE 2 END, stop_name ASC
        LIMIT ?;
        """,
        (pattern, pattern, q, f"{q}%", limit),
    )
    return [dict(row) for row in cursor.fetchall()]
