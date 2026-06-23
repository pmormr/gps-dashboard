import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

DB_PATH = Path(os.environ.get('GPS_DB_PATH', Path.home() / 'gps_history.db'))

# Whole-second strftime template; canonical timestamps append a 3-digit
# millisecond fraction and a ``Z`` suffix to this (see ``_canonical``).
_SECONDS_FORMAT = '%Y-%m-%dT%H:%M:%S'


def _canonical(dt: datetime) -> str:
    """Format a datetime as fixed-width millisecond UTC text.

    Produces ``2026-06-09T14:55:55.200Z`` — whole seconds plus a zero-padded
    3-digit millisecond fraction and a ``Z`` suffix. The width is fixed so
    lexical ordering matches chronological ordering across every timestamp
    column; mixing widths breaks it, since ``'.'`` sorts before ``'Z'`` (a
    whole-second ``...55Z`` would sort *after* a millisecond ``...55.200Z``).

    Args:
        dt: A datetime; naive values are treated as UTC.

    Returns:
        The timestamp as fixed-width millisecond UTC text.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt = dt.astimezone(UTC)
    return f'{dt.strftime(_SECONDS_FORMAT)}.{dt.microsecond // 1000:03d}Z'


def canonical_timestamp(value: str) -> str:
    """Normalize an ISO-8601 timestamp to the canonical storage format.

    Every timestamp column stores fixed-width millisecond UTC strings
    (``2026-06-09T14:55:55.200Z``). The logger, marks, and sensor receipts write
    this form via ``now_canonical``; annotation bounds arrive from the browser as
    ``...000Z``. Collapsing every source to one width-aligned UTC format keeps the
    lexical range comparisons in the points, annotations, and sensor queries
    correct — and at 5 Hz the millisecond fraction makes raw fixes
    sub-second-distinct (a usable dedup key), which whole-second strings were not.

    Args:
        value: An ISO-8601 timestamp, with or without a ``Z`` suffix, an explicit
            offset, or fractional seconds.

    Returns:
        The timestamp as a fixed-width millisecond UTC string.

    Raises:
        ValueError: If ``value`` is not a parseable ISO-8601 timestamp.
    """
    return _canonical(datetime.fromisoformat(value.replace('Z', '+00:00')))


def now_canonical() -> str:
    """Return the current time as a fixed-width millisecond canonical string.

    The single source for "now" timestamps written across tiers (the logger's
    raw inserts, the marks upsert). Wall-clock ``now()`` is PPS-disciplined
    stratum-1 GPS time on the Pi (C23), so it is GPS-quality without the backward
    jitter of the TPV ``time`` field.

    Returns:
        The current UTC time as fixed-width millisecond canonical text.
    """
    return _canonical(datetime.now(UTC))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=30000')
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    _maybe_rename_trips_to_annotations(conn)
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

        CREATE TABLE IF NOT EXISTS annotations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time   TEXT,
            notes      TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_annotations_start_time
            ON annotations(start_time);

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

        -- OBD-II vehicle telemetry (the van as a sensor), ingested over MQTT like
        -- bme680. Wide per-type readings table on the canonical ms grid so rows join
        -- gps_points/track_points directly. fuel_rate_lph is a reserved placeholder:
        -- the Pentastar has no 015E PID, so fuel rate is derived in Phase 4 (see the
        -- OBD plan). Column set mirrors api/sensor_schema.py's 'obd' metrics.
        CREATE TABLE IF NOT EXISTS obd_readings (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id             INTEGER NOT NULL,
            timestamp             TEXT NOT NULL,
            rpm                   REAL,
            speed_kph             REAL,
            engine_load_pct       REAL,
            throttle_pct          REAL,
            coolant_c             REAL,
            intake_c              REAL,
            ambient_air_c         REAL,
            map_kpa               REAL,
            barometric_kpa        REAL,
            fuel_level_pct        REAL,
            fuel_rate_lph         REAL,
            voltage_v             REAL,
            run_time_s            REAL,
            short_fuel_trim_1_pct REAL,
            long_fuel_trim_1_pct  REAL,
            short_fuel_trim_2_pct REAL,
            long_fuel_trim_2_pct  REAL,
            absolute_load_pct     REAL,
            commanded_equiv_ratio REAL
        );
        CREATE INDEX IF NOT EXISTS idx_obd_sensor_time
            ON obd_readings(sensor_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_obd_time
            ON obd_readings(timestamp);

        -- Victron house-power telemetry (the van's electrical system as a sensor),
        -- ingested over MQTT like obd/bme680. Bridged from the Venus OS GX device's
        -- own MQTT broker by sensors/victron_reader.py (one row per 30s snapshot) onto
        -- the canonical ms grid, so rows join gps_points/track_points for per-trip
        -- energy analysis. State/source columns are enums (INTEGER); the rest are
        -- measurements. Column set mirrors api/sensor_schema.py's 'victron' metrics.
        CREATE TABLE IF NOT EXISTS victron_readings (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id            INTEGER NOT NULL,
            timestamp            TEXT NOT NULL,
            battery_soc          REAL,
            battery_voltage      REAL,
            battery_current      REAL,
            battery_power        REAL,
            battery_temp_c       REAL,
            consumed_ah          REAL,
            time_to_go_s         REAL,
            battery_state        INTEGER,
            pv_power             REAL,
            pv_voltage           REAL,
            pv_yield_today_kwh   REAL,
            solar_state          INTEGER,
            dc_system_power      REAL,
            ac_in_power          REAL,
            ac_in_current        REAL,
            ac_in_source         INTEGER,
            ac_consumption_power REAL,
            vebus_state          INTEGER,
            vebus_mode           INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_victron_sensor_time
            ON victron_readings(sensor_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_victron_time
            ON victron_readings(timestamp);

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

        -- Processed tier: denoised/simplified points the frontend reads. Derived
        -- from raw gps_points by gps-processor; fully rebuildable (see the denoise
        -- plan). kind='stop' rows carry dwell_start/dwell_end/radius.
        CREATE TABLE IF NOT EXISTS track_points (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            lat         REAL NOT NULL,
            lon         REAL NOT NULL,
            speed       REAL,
            altitude    REAL,
            track       REAL,
            kind        TEXT NOT NULL,
            n_raw       INTEGER NOT NULL,
            importance  REAL NOT NULL,
            accuracy    REAL,
            dwell_start TEXT,
            dwell_end   TEXT,
            radius      REAL,
            src_raw_id  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_track_points_timestamp
            ON track_points(timestamp);
        CREATE INDEX IF NOT EXISTS idx_track_points_src_raw_id
            ON track_points(src_raw_id);

        -- Processor-emitted "interesting" events (stop_start/end, mode transitions,
        -- rate spikes, drift). Distinct from the user-curated annotations table;
        -- rebuildable like track_points.
        CREATE TABLE IF NOT EXISTS track_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            end_time    TEXT,
            type        TEXT NOT NULL,
            magnitude   REAL,
            payload     TEXT,
            src_raw_id  INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_track_events_timestamp
            ON track_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_track_events_src_raw_id
            ON track_events(src_raw_id);

        -- SKY-sourced receiver telemetry (DOP + sat counts), written by the logger
        -- on its own ~5s throttle. Standalone — not joined into the position path.
        CREATE TABLE IF NOT EXISTS receiver_metadata (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            hdop        REAL,
            vdop        REAL,
            pdop        REAL,
            nsat_used   INTEGER,
            nsat_seen   INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_receiver_metadata_timestamp
            ON receiver_metadata(timestamp);

        -- gps-processor cursor (e.g. last_committed_raw_id) + any future scalar state.
        CREATE TABLE IF NOT EXISTS processing_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        -- Drone telemetry tier — aerial GPS tracks extracted from DJI footage by
        -- tools/import_drone.py (offline batch import, NOT the live MQTT path).
        -- One row per clip; canonical ms-UTC puts drone points on the same time
        -- axis as gps_points. Rebuildable from the source media. See the drone
        -- platform plan. media_path is the canonical rex-nas path, NULL when a
        -- flight is first imported from an SD card and backfilled by the NAS scan.
        CREATE TABLE IF NOT EXISTS drone_flights (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            model         TEXT NOT NULL,
            model_code    TEXT NOT NULL,
            first_fix_utc TEXT NOT NULL,
            last_fix_utc  TEXT NOT NULL,
            media_path    TEXT,
            source_name   TEXT,
            n_points      INTEGER NOT NULL,
            min_lat       REAL NOT NULL,
            min_lon       REAL NOT NULL,
            max_lat       REAL NOT NULL,
            max_lon       REAL NOT NULL,
            imported_at   TEXT NOT NULL
        );
        -- Natural key (decision 3): no model exposes a serial, so dedup on the
        -- model code + the first valid fix's ms-UTC. Idempotent re-import and
        -- SD-now/NAS-later convergence both rely on this.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_drone_flights_natural_key
            ON drone_flights(model_code, first_fix_utc);

        CREATE TABLE IF NOT EXISTS drone_track_points (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            flight_id   INTEGER NOT NULL,
            timestamp   TEXT NOT NULL,
            lat         REAL NOT NULL,
            lon         REAL NOT NULL,
            abs_alt     REAL,
            importance  REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_drone_track_points_flight
            ON drone_track_points(flight_id);
        CREATE INDEX IF NOT EXISTS idx_drone_track_points_timestamp
            ON drone_track_points(timestamp);
    """)
    conn.commit()


def _maybe_rename_trips_to_annotations(conn: sqlite3.Connection) -> None:
    """Rename the legacy ``trips`` table to ``annotations`` and drop NOT NULL on
    ``end_time`` so a NULL value marks a point-in-time annotation.

    SQLite has no ALTER COLUMN, so the swap is a CREATE-INSERT-DROP-rename
    dance. Idempotent: only fires when ``trips`` still exists. Runs at the top
    of ``init_db`` so the subsequent ``CREATE TABLE IF NOT EXISTS annotations``
    becomes a no-op.

    Args:
        conn: Open SQLite connection.
    """
    has_trips = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trips'"
    ).fetchone()
    if not has_trips:
        return
    conn.executescript("""
        CREATE TABLE annotations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time   TEXT,
            notes      TEXT DEFAULT ''
        );
        INSERT INTO annotations (id, name, start_time, end_time, notes)
            SELECT id, name, start_time, end_time, notes FROM trips;
        DROP TABLE trips;
        CREATE INDEX IF NOT EXISTS idx_annotations_start_time
            ON annotations(start_time);
    """)
    conn.commit()
    print('Migration: renamed trips → annotations (end_time now nullable)')


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
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
    existing = {row['name'] for row in conn.execute(f'PRAGMA table_info({table})')}
    added = [name for name in columns if name not in existing]
    for name in added:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {columns[name]}')
    if added:
        conn.commit()
        print(f'Migration: added {table} column(s): {", ".join(added)}')


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

    # Per-fix accuracy/quality fields from gpsd TPV, for accuracy-weighted denoise
    # and richer analysis (C4). Existing rows get NULL.
    _add_missing_columns(
        conn,
        'gps_points',
        {
            'epx': 'REAL',
            'epy': 'REAL',
            'epv': 'REAL',
            'eps': 'REAL',
            'climb': 'REAL',
            'mode': 'INTEGER',
        },
    )

    # One-time, idempotent: widen whole-second timestamps to fixed-width ms
    # (``...SSZ`` → ``...SS.000Z``). canonical_timestamp now emits ms everywhere,
    # so any range-compared table left at whole-second width would reintroduce the
    # ``'.'`` < ``'Z'`` ordering hazard (a whole-second row sorts *after* its ms
    # sibling). Annotations re-normalize below via canonical_timestamp; these tables
    # need a direct rewrite. The gps_points pass is a full-table UPDATE that briefly
    # holds the WAL write lock against the live logger (busy_timeout=30000 covers it).
    for table in ('gps_points', 'bme680_readings', 'marks'):
        widened = conn.execute(
            f"UPDATE {table} SET timestamp = substr(timestamp, 1, 19) || '.000Z' "
            "WHERE length(timestamp) = 20 AND timestamp NOT LIKE '%.%'"
        ).rowcount
        if widened:
            conn.commit()
            print(f'Migration: widened {widened} {table} timestamp(s) to ms')

    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='location_history'"
    ).fetchone()
    if row is not None:
        count = conn.execute('SELECT COUNT(*) FROM location_history').fetchone()[0]
        conn.execute('DROP TABLE location_history')
        conn.commit()
        print(f'Migration: dropped legacy location_history table ({count} rows)')

    deleted = conn.execute('DELETE FROM gps_points WHERE lat = 0 AND lon = 0').rowcount
    conn.commit()
    if deleted:
        print(f'Migration: deleted {deleted} null-island gps_points rows')

    normalized = 0
    rows = conn.execute('SELECT id, start_time, end_time FROM annotations').fetchall()
    for row in rows:
        new_start = canonical_timestamp(row['start_time'])
        new_end = canonical_timestamp(row['end_time']) if row['end_time'] else None
        if new_start != row['start_time'] or new_end != row['end_time']:
            conn.execute(
                'UPDATE annotations SET start_time = ?, end_time = ? WHERE id = ?',
                (new_start, new_end, row['id']),
            )
            normalized += 1
    if normalized:
        conn.commit()
        print(f'Migration: normalized timestamps on {normalized} annotation(s)')
