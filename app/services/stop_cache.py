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
_all_conns: set[sqlite3.Connection] = set()
_conn_gen = 0


def _tr_upper(s: str | None) -> str:
    if not s:
        return ""
    return str(s).replace("i", "İ").replace("ı", "I").upper()


def _open_stop_conn(target: str, *, uri: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(
        target,
        uri=uri,
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.create_function("tr_upper", 1, _tr_upper)
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def _init_stop_schema(conn: sqlite3.Connection, *, is_memory: bool) -> None:
    with conn:
        if not is_memory:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stop_cache (
                stop_code TEXT PRIMARY KEY,
                stop_name TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                district TEXT,
                direction TEXT,
                fiziki_durum TEXT,
                akilli_durak INTEGER,
                durak_tipi TEXT,
                engelli_kullanim TEXT,
                updated_at REAL NOT NULL
            );
            """
        )
        existing_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(stop_cache);").fetchall()
        }
        for col_name, col_type in (
            ("fiziki_durum", "TEXT"),
            ("akilli_durak", "INTEGER"),
            ("durak_tipi", "TEXT"),
            ("engelli_kullanim", "TEXT"),
        ):
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE stop_cache ADD COLUMN {col_name} {col_type};")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sc_stop_name ON stop_cache(stop_name);")


def get_db_connection() -> sqlite3.Connection:
    global _primary_conn, _initialized, _is_memory
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "conn_gen", -1) == _conn_gen:
        try:
            conn.execute("SELECT 1;")
            return conn
        except Exception:
            conn = None

    with _init_lock:
        conn = getattr(_local, "conn", None)
        if conn is not None and getattr(_local, "conn_gen", -1) == _conn_gen:
            try:
                conn.execute("SELECT 1;")
                return conn
            except Exception:
                conn = None

        db_target = settings.stop_cache_path
        if not _initialized:
            _is_memory = db_target == ":memory:"

        new_conn: sqlite3.Connection | None = None
        if _is_memory:
            uri_target = (
                "file:stop_cache_mem?mode=memory&cache=shared"
                if db_target == ":memory:"
                else "file:stop_cache_fallback?mode=memory&cache=shared"
            )
            new_conn = _open_stop_conn(uri_target, uri=True)
            if not _initialized:
                _init_stop_schema(new_conn, is_memory=True)
                _initialized = True
            if _primary_conn is None:
                _primary_conn = new_conn
        else:
            p = Path(db_target)
            if not p.is_absolute():
                p = Path(__file__).resolve().parent.parent.parent / p
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                new_conn = _open_stop_conn(str(p), uri=False)
                if not _initialized:
                    _init_stop_schema(new_conn, is_memory=False)
                    _initialized = True
                else:
                    new_conn.execute("PRAGMA synchronous = NORMAL;")
            except Exception as exc:
                if new_conn is not None:
                    try:
                        new_conn.close()
                    except Exception:
                        pass
                    new_conn = None
                logger.warning("Could not open %s (%s), trying /tmp/stops.db", db_target, exc)
                try:
                    p_tmp = Path("/tmp/stops.db")
                    new_conn = _open_stop_conn(str(p_tmp), uri=False)
                    if not _initialized:
                        _init_stop_schema(new_conn, is_memory=False)
                        _initialized = True
                    else:
                        new_conn.execute("PRAGMA synchronous = NORMAL;")
                except Exception as exc2:
                    if new_conn is not None:
                        try:
                            new_conn.close()
                        except Exception:
                            pass
                        new_conn = None
                    logger.warning(
                        "Could not open /tmp fallback either (%s), using in-memory shared fallback",
                        exc2,
                    )
                    _is_memory = True
                    new_conn = _open_stop_conn(
                        "file:stop_cache_fallback?mode=memory&cache=shared", uri=True
                    )
                    if not _initialized:
                        _init_stop_schema(new_conn, is_memory=True)
                        _initialized = True
                    if _primary_conn is None:
                        _primary_conn = new_conn

        _all_conns.add(new_conn)
        _local.conn = new_conn
        _local.conn_gen = _conn_gen
        return new_conn


def close_connection() -> None:
    """Close and release all active SQLite connections across worker threads."""
    global _primary_conn, _initialized, _is_memory, _conn_gen
    with _init_lock:
        _conn_gen += 1
        conns_to_close = list(_all_conns)
        _all_conns.clear()

        local_conn = getattr(_local, "conn", None)
        if local_conn is not None:
            if local_conn not in conns_to_close:
                conns_to_close.append(local_conn)
            _local.conn = None
        _local.conn_gen = _conn_gen

        if _primary_conn is not None:
            if _primary_conn not in conns_to_close:
                conns_to_close.append(_primary_conn)
            _primary_conn = None

        for conn in conns_to_close:
            try:
                conn.close()
            except Exception:
                pass

        _initialized = False
        _is_memory = False


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


_SELECT_COLS = (
    "stop_code, stop_name, latitude, longitude, district, direction, "
    "fiziki_durum, akilli_durak, durak_tipi, engelli_kullanim, updated_at"
)

_UPSERT_SQL = """
    INSERT INTO stop_cache (
        stop_code, stop_name, latitude, longitude, district, direction,
        fiziki_durum, akilli_durak, durak_tipi, engelli_kullanim, updated_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(stop_code) DO UPDATE SET
        stop_name = CASE WHEN excluded.stop_name != '' THEN excluded.stop_name ELSE stop_cache.stop_name END,
        latitude = COALESCE(excluded.latitude, stop_cache.latitude),
        longitude = COALESCE(excluded.longitude, stop_cache.longitude),
        district = COALESCE(excluded.district, stop_cache.district),
        direction = COALESCE(excluded.direction, stop_cache.direction),
        fiziki_durum = COALESCE(excluded.fiziki_durum, stop_cache.fiziki_durum),
        akilli_durak = COALESCE(excluded.akilli_durak, stop_cache.akilli_durak),
        durak_tipi = COALESCE(excluded.durak_tipi, stop_cache.durak_tipi),
        engelli_kullanim = COALESCE(excluded.engelli_kullanim, stop_cache.engelli_kullanim),
        updated_at = excluded.updated_at;
"""


def _coerce_akilli_int(val: Any) -> int | None:
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, int) and val in (0, 1):
        return val
    return None


def _row_to_stop_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    raw_akilli = d.get("akilli_durak")
    if raw_akilli is not None:
        d["akilli_durak"] = bool(raw_akilli)
    return d


def upsert_stop(
    stop_code: str,
    stop_name: str,
    latitude: float | None = None,
    longitude: float | None = None,
    district: str | None = None,
    direction: str | None = None,
    fiziki_durum: str | None = None,
    akilli_durak: bool | None = None,
    durak_tipi: str | None = None,
    engelli_kullanim: str | None = None,
) -> None:
    code = stop_code.strip()
    if not code:
        return

    name = stop_name.strip()
    dir_val = _clean_direction(direction)
    dist_val = district.strip() if district else None
    fiziki_val = fiziki_durum.strip() if fiziki_durum else None
    akilli_val = _coerce_akilli_int(akilli_durak)
    tipi_val = durak_tipi.strip() if durak_tipi else None
    engelli_val = engelli_kullanim.strip() if engelli_kullanim else None
    now = time.time()

    conn = get_db_connection()
    with _write_lock, conn:
        conn.execute(
            _UPSERT_SQL,
            (
                code,
                name,
                latitude,
                longitude,
                dist_val,
                dir_val,
                fiziki_val,
                akilli_val,
                tipi_val,
                engelli_val,
                now,
            ),
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

        fiziki = item.get("fiziki_durum")
        fiziki_val = str(fiziki).strip() if fiziki else None
        akilli_val = _coerce_akilli_int(item.get("akilli_durak"))
        durak_tipi = item.get("durak_tipi")
        tipi_val = str(durak_tipi).strip() if durak_tipi else None
        engelli = item.get("engelli_kullanim")
        engelli_val = str(engelli).strip() if engelli else None

        rows.append(
            (
                code,
                name,
                lat_val,
                lon_val,
                dist_val,
                dir_val,
                fiziki_val,
                akilli_val,
                tipi_val,
                engelli_val,
                now,
            )
        )

    if not rows:
        return

    try:
        conn = get_db_connection()
        with _write_lock, conn:
            conn.executemany(_UPSERT_SQL, rows)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to upsert stop cache batch: %s", exc)


def get_stop(stop_code: str) -> dict[str, Any] | None:
    code = stop_code.strip()
    if not code:
        return None
    conn = get_db_connection()
    sql = f"SELECT {_SELECT_COLS} FROM stop_cache WHERE stop_code = ? LIMIT 1;"
    if _is_memory:
        with _write_lock:
            row = conn.execute(sql, (code,)).fetchone()
    else:
        row = conn.execute(sql, (code,)).fetchone()
    if not row:
        return None
    return _row_to_stop_dict(row)


def get_all_stops() -> list[dict[str, Any]]:
    conn = get_db_connection()
    sql = f"SELECT {_SELECT_COLS} FROM stop_cache;"
    if _is_memory:
        with _write_lock:
            rows = conn.execute(sql).fetchall()
    else:
        rows = conn.execute(sql).fetchall()
    return [_row_to_stop_dict(row) for row in rows]


def search_stops(query: str, limit: int = 20) -> list[dict[str, Any]]:
    q = query.strip()
    if not q:
        return []
    conn = get_db_connection()
    pattern = f"%{q}%"
    sql = f"""
        SELECT {_SELECT_COLS}
        FROM stop_cache
        WHERE tr_upper(stop_name) LIKE tr_upper(?) OR stop_code LIKE ?
        ORDER BY CASE WHEN stop_code = ? THEN 0 WHEN tr_upper(stop_name) LIKE tr_upper(?) THEN 1 ELSE 2 END, stop_name ASC
        LIMIT ?;
    """
    params = (pattern, pattern, q, f"{q}%", limit)
    if _is_memory:
        with _write_lock:
            rows = conn.execute(sql, params).fetchall()
    else:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_stop_dict(row) for row in rows]
