"""
SQLite database layer for F1 Dashboard.
Caches all API responses to avoid re-fetching and enables offline browsing.
"""

import sqlite3
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "f1_cache.db")

# Whitelist of valid table names to prevent SQL injection
VALID_TABLES = frozenset({
    "races", "results", "driver_standings", "constructor_standings",
    "qualifying", "pit_stops", "lap_times",
    "openf1_sessions", "openf1_drivers", "car_locations",
    "openf1_laps", "stints", "track_outlines",
})


def _validate_table(table):
    """Validate table name against whitelist to prevent SQL injection."""
    if table not in VALID_TABLES:
        raise ValueError(f"Invalid table name: {table!r}")


_VALID_COLUMN_RE = __import__('re').compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _validate_column(col):
    """Validate column name to prevent SQL injection via filter keys."""
    if not _VALID_COLUMN_RE.match(col):
        raise ValueError(f"Invalid column name: {col!r}")


def get_connection():
    """Get a connection to the SQLite database."""
    # timeout= makes sqlite3 retry internally for up to 30s instead of
    # raising "database is locked" immediately on a concurrent writer.
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # Belt-and-braces: busy_timeout backs the same retry behaviour at the
    # SQLite level, which matters once WAL readers/writers overlap under
    # Flask's multi-threaded request handling.
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _get_db():
    """Context manager for safe database connections. Auto-closes on exit."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        -- Race calendar cache
        CREATE TABLE IF NOT EXISTS races (
            season INTEGER,
            round INTEGER,
            race_name TEXT,
            circuit_name TEXT,
            circuit_id TEXT,
            locality TEXT,
            country TEXT,
            date TEXT,
            time TEXT,
            url TEXT,
            lat REAL,
            lng REAL,
            has_sprint INTEGER DEFAULT 0,
            fetched_at TIMESTAMP,
            PRIMARY KEY (season, round)
        );

        -- Race results cache
        CREATE TABLE IF NOT EXISTS results (
            season INTEGER,
            round INTEGER,
            position INTEGER,
            driver_id TEXT,
            driver_code TEXT,
            driver_name TEXT,
            constructor TEXT,
            grid INTEGER,
            laps INTEGER,
            status TEXT,
            time_text TEXT,
            points REAL,
            fastest_lap_time TEXT,
            fastest_lap_rank INTEGER,
            fetched_at TIMESTAMP,
            PRIMARY KEY (season, round, position)
        );

        -- Driver standings cache
        CREATE TABLE IF NOT EXISTS driver_standings (
            season INTEGER,
            round INTEGER,
            position INTEGER,
            driver_id TEXT,
            driver_code TEXT,
            driver_name TEXT,
            constructor TEXT,
            points REAL,
            wins INTEGER,
            nationality TEXT,
            fetched_at TIMESTAMP,
            PRIMARY KEY (season, position)
        );

        -- Constructor standings cache
        CREATE TABLE IF NOT EXISTS constructor_standings (
            season INTEGER,
            round INTEGER,
            position INTEGER,
            constructor TEXT,
            constructor_id TEXT,
            nationality TEXT,
            points REAL,
            wins INTEGER,
            fetched_at TIMESTAMP,
            PRIMARY KEY (season, position)
        );

        -- Qualifying results
        CREATE TABLE IF NOT EXISTS qualifying (
            season INTEGER,
            round INTEGER,
            position INTEGER,
            driver_code TEXT,
            driver_name TEXT,
            constructor TEXT,
            q1 TEXT,
            q2 TEXT,
            q3 TEXT,
            fetched_at TIMESTAMP,
            PRIMARY KEY (season, round, position)
        );

        -- Pit stops
        CREATE TABLE IF NOT EXISTS pit_stops (
            season INTEGER,
            round INTEGER,
            driver_code TEXT,
            stop INTEGER,
            lap INTEGER,
            time TEXT,
            duration TEXT,
            fetched_at TIMESTAMP,
            PRIMARY KEY (season, round, driver_code, stop)
        );

        -- Lap times
        CREATE TABLE IF NOT EXISTS lap_times (
            season INTEGER,
            round INTEGER,
            driver_code TEXT,
            lap INTEGER,
            position INTEGER,
            time TEXT,
            fetched_at TIMESTAMP,
            PRIMARY KEY (season, round, driver_code, lap)
        );

        -- ── OpenF1 Tables ──────────────────────────────────────

        -- OpenF1 session metadata
        CREATE TABLE IF NOT EXISTS openf1_sessions (
            session_key INTEGER PRIMARY KEY,
            session_name TEXT,
            session_type TEXT,
            meeting_key INTEGER,
            circuit_key INTEGER,
            circuit_short_name TEXT,
            country_name TEXT,
            year INTEGER,
            date_start TEXT,
            date_end TEXT,
            fetched_at TIMESTAMP
        );

        -- OpenF1 driver metadata per session
        CREATE TABLE IF NOT EXISTS openf1_drivers (
            session_key INTEGER,
            driver_number INTEGER,
            full_name TEXT,
            name_acronym TEXT,
            team_name TEXT,
            team_colour TEXT,
            headshot_url TEXT,
            country_code TEXT,
            fetched_at TIMESTAMP,
            PRIMARY KEY (session_key, driver_number)
        );

        -- Car location data (latest position per driver)
        CREATE TABLE IF NOT EXISTS car_locations (
            session_key INTEGER,
            driver_number INTEGER,
            x REAL,
            y REAL,
            z REAL,
            date TEXT,
            fetched_at TIMESTAMP,
            PRIMARY KEY (session_key, driver_number)
        );

        -- OpenF1 lap data with sector times
        CREATE TABLE IF NOT EXISTS openf1_laps (
            session_key INTEGER,
            driver_number INTEGER,
            lap_number INTEGER,
            duration_sector_1 REAL,
            duration_sector_2 REAL,
            duration_sector_3 REAL,
            lap_duration REAL,
            is_pit_out_lap INTEGER,
            st_speed REAL,
            date_start TEXT,
            fetched_at TIMESTAMP,
            PRIMARY KEY (session_key, driver_number, lap_number)
        );

        -- Stint / tyre strategy data
        CREATE TABLE IF NOT EXISTS stints (
            session_key INTEGER,
            driver_number INTEGER,
            stint_number INTEGER,
            compound TEXT,
            tyre_age_at_start INTEGER,
            lap_start INTEGER,
            lap_end INTEGER,
            fetched_at TIMESTAMP,
            PRIMARY KEY (session_key, driver_number, stint_number)
        );
        -- Track outline cache (circuit shape per session)
        CREATE TABLE IF NOT EXISTS track_outlines (
            session_key INTEGER,
            x REAL,
            y REAL,
            fetched_at TIMESTAMP
        );

        -- Performance Indexes
        CREATE INDEX IF NOT EXISTS idx_results_season_round ON results(season, round);
        CREATE INDEX IF NOT EXISTS idx_lap_times_lookup ON lap_times(season, round, driver_code);
        CREATE INDEX IF NOT EXISTS idx_openf1_laps_session ON openf1_laps(session_key, driver_number);
    """)

    conn.commit()
    conn.close()

def is_stale(fetched_at_str, max_age_hours=1):
    """Check if cached data is stale based on age."""
    if fetched_at_str is None:
        return True
    try:
        fetched_at = datetime.fromisoformat(fetched_at_str)
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - fetched_at) > timedelta(hours=max_age_hours)
    except (ValueError, TypeError):
        return True


def get_cached(table, filters, max_age_hours=1):
    """
    Check cache for data. Returns list of dicts if fresh, None if stale/missing.
    
    Args:
        table: Table name to query
        filters: Dict of column=value pairs for WHERE clause
        max_age_hours: Maximum age in hours before data is considered stale
    """
    _validate_table(table)

    if not filters:
        raise ValueError("get_cached requires at least one filter to avoid a full-table scan")

    where_parts = []
    values = []
    for col, val in filters.items():
        _validate_column(col)
        where_parts.append(f"{col} = ?")
        values.append(val)

    where_clause = " AND ".join(where_parts)
    query = f"SELECT * FROM {table} WHERE {where_clause}"

    with _get_db() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(query, values)
            rows = cursor.fetchall()
        except sqlite3.OperationalError:
            return None

    if not rows:
        return None

    # Check freshness using fetched_at from first row
    first_row = dict(rows[0])
    fetched_at = first_row.get("fetched_at")
    if is_stale(fetched_at, max_age_hours):
        return None

    return [dict(row) for row in rows]


def upsert(table, data_list):
    """
    Insert or replace rows into a table.
    Uses executemany for bulk performance.
    
    Args:
        table: Table name
        data_list: List of dicts, each representing a row
    """
    if not data_list:
        return

    _validate_table(table)

    columns = list(data_list[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_names = ", ".join(columns)

    query = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"
    all_values = [[data.get(col) for col in columns] for data in data_list]

    with _get_db() as conn:
        cursor = conn.cursor()
        cursor.executemany(query, all_values)
        conn.commit()


def clear_table(table, filters=None):
    """Clear rows from a table, optionally filtered."""
    _validate_table(table)

    with _get_db() as conn:
        cursor = conn.cursor()

        if filters:
            where_parts = []
            values = []
            for col, val in filters.items():
                _validate_column(col)
                where_parts.append(f"{col} = ?")
                values.append(val)
            where_clause = " AND ".join(where_parts)
            cursor.execute(f"DELETE FROM {table} WHERE {where_clause}", values)
        else:
            cursor.execute(f"DELETE FROM {table}")

        conn.commit()