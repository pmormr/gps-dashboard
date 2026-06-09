import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get('GPS_DB_PATH', Path.home() / 'gps_history.db'))

TIMESTAMP_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


def canonical_timestamp(value: str) -> str:
    """Normalize an ISO-8601 timestamp to the canonical storage format.

    Every timestamp column stores whole-second UTC strings
    (``2026-06-09T14:55:55Z``). The logger and marks already write this form;
    trip bounds arrive from the browser as ``...000Z``. Collapsing both to one
    width-aligned UTC format keeps the lexical range comparisons in the points
    and trips queries correct.

    Args:
        value: An ISO-8601 timestamp, with or without a ``Z`` suffix, an explicit
            offset, or fractional seconds.

    Returns:
        The timestamp as a whole-second UTC string.

    Raises:
        ValueError: If ``value`` is not a parseable ISO-8601 timestamp.
    """
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(TIMESTAMP_FORMAT)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gps_points (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            lat       REAL NOT NULL,
            lon       REAL NOT NULL,
            speed     REAL,
            altitude  REAL,
            track     REAL
        );
        CREATE INDEX IF NOT EXISTS idx_gps_points_timestamp
            ON gps_points(timestamp);

        CREATE TABLE IF NOT EXISTS trips (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time   TEXT NOT NULL,
            notes      TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_trips_start_time
            ON trips(start_time);

        CREATE TABLE IF NOT EXISTS marks (
            key       TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sensors (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            node        TEXT NOT NULL,
            type        TEXT NOT NULL,
            location    TEXT,
            description TEXT DEFAULT '',
            first_seen  TEXT NOT NULL,
            last_seen   TEXT,
            status      TEXT DEFAULT 'unknown',
            UNIQUE(node, type)
        );

        CREATE TABLE IF NOT EXISTS bme680_readings (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id             INTEGER NOT NULL,
            timestamp             TEXT NOT NULL,
            temp_c                REAL,
            humidity_pct          REAL,
            pressure_hpa          REAL,
            gas_ohms              REAL,
            iaq                   REAL,
            iaq_accuracy          INTEGER,
            co2_equivalent        REAL,
            breath_voc_equivalent REAL
        );
        CREATE INDEX IF NOT EXISTS idx_bme680_sensor_time
            ON bme680_readings(sensor_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_bme680_time
            ON bme680_readings(timestamp);

        CREATE TABLE IF NOT EXISTS alarm_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id   INTEGER,
            metric      TEXT NOT NULL,
            min_value   REAL,
            max_value   REAL,
            hysteresis  REAL DEFAULT 0,
            enabled     INTEGER DEFAULT 1,
            name        TEXT
        );

        CREATE TABLE IF NOT EXISTS alarm_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id     INTEGER NOT NULL,
            state       TEXT NOT NULL,
            value       REAL,
            timestamp   TEXT NOT NULL
        );
    """)
    conn.commit()


def _add_missing_columns(
    conn: sqlite3.Connection, table: str, columns: dict[str, str]
) -> None:
    """Add any columns absent from ``table`` (idempotent schema migration).

    ``init_db`` uses ``CREATE TABLE IF NOT EXISTS``, which leaves an already-created
    table untouched, so new columns on an existing DB (e.g. the Pi's) need an
    explicit ``ALTER TABLE``. Existing columns are skipped, so this is safe to run
    on every startup.

    Args:
        conn: Open SQLite connection.
        table: Table name (a trusted literal, not user input).
        columns: Mapping of column name to its SQL type/declaration.
    """
    existing = {row['name'] for row in conn.execute(f"PRAGMA table_info({table})")}
    added = [name for name in columns if name not in existing]
    for name in added:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {columns[name]}")
    if added:
        conn.commit()
        print(f"Migration: added {table} column(s): {', '.join(added)}")


def migrate(conn: sqlite3.Connection) -> None:
    _add_missing_columns(
        conn,
        'bme680_readings',
        {
            'iaq': 'REAL',
            'iaq_accuracy': 'INTEGER',
            'co2_equivalent': 'REAL',
            'breath_voc_equivalent': 'REAL',
        },
    )

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='location_history'"
    ).fetchone()
    if row is not None:
        count = conn.execute("SELECT COUNT(*) FROM location_history").fetchone()[0]
        conn.execute("DROP TABLE location_history")
        conn.commit()
        print(f"Migration: dropped legacy location_history table ({count} rows)")

    deleted = conn.execute(
        "DELETE FROM gps_points WHERE lat = 0 AND lon = 0"
    ).rowcount
    conn.commit()
    if deleted:
        print(f"Migration: deleted {deleted} null-island gps_points rows")

    normalized = 0
    for row in conn.execute("SELECT id, start_time, end_time FROM trips").fetchall():
        new_start = canonical_timestamp(row['start_time'])
        new_end = canonical_timestamp(row['end_time'])
        if new_start != row['start_time'] or new_end != row['end_time']:
            conn.execute(
                "UPDATE trips SET start_time = ?, end_time = ? WHERE id = ?",
                (new_start, new_end, row['id']),
            )
            normalized += 1
    if normalized:
        conn.commit()
        print(f"Migration: normalized timestamps on {normalized} trip(s)")
