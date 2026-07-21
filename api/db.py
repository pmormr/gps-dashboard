import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Canonical timestamp formatting lives in common.timefmt (a project-wide
# convention, not a database concern); re-exported here for the many call sites
# that import these from api.db.
from common.timefmt import (
    canonical_timestamp as canonical_timestamp,
)
from common.timefmt import (
    format_canonical as format_canonical,
)
from common.timefmt import (
    now_canonical as now_canonical,
)

DB_PATH = Path(os.environ.get('GPS_DB_PATH', Path.home() / 'gps_history.db'))

#: Explicit places-sidecar override (env / tools / tests). When None, the sidecar
#: rides beside the main DB (places_db_path) — a derived default on purpose: every
#: process that opens a connection (app, logger, processor, ingest…) resolves the
#: same pair of files by construction, so no unit file can drift and, e.g.,
#: initialise a divergent sidecar file.
_env_places = os.environ.get('GPS_PLACES_DB_PATH')
PLACES_DB_PATH: Path | None = Path(_env_places) if _env_places else None


def places_db_path() -> Path:
    """Resolve the places sidecar path: ``PLACES_DB_PATH`` override, else beside ``DB_PATH``."""
    return PLACES_DB_PATH if PLACES_DB_PATH is not None else DB_PATH.parent / 'places.db'


def apply_path_overrides(db: str | None = None, places_db: str | None = None) -> None:
    """Point the module at explicit DB paths from a CLI ``--db``/``--places-db``.

    Tools mutate the module-level ``DB_PATH``/``PLACES_DB_PATH`` globals so every
    ``get_connection()`` in the process resolves the overridden files. This
    centralizes that global mutation (and the footgun of doing it by hand) so a
    ``--db``-taking tool is one call, not a copied ``if args.db: api.db.DB_PATH = ...``
    block. A ``None`` (the argparse default when the flag is absent) leaves the
    corresponding global at its env-derived default.

    Args:
        db: Main SQLite DB path override, or None to leave ``DB_PATH`` unchanged.
        places_db: Places sidecar path override, or None to leave ``PLACES_DB_PATH``
            unchanged.
    """
    global DB_PATH, PLACES_DB_PATH
    if db:
        DB_PATH = Path(db)
    if places_db:
        PLACES_DB_PATH = Path(places_db)


def get_connection() -> sqlite3.Connection:
    """Open the main DB with the places sidecar ATTACHed as ``places_db``.

    The places tier (rebuildable POI data, kept out of the backup path) lives in
    its own file; ATTACH auto-creates it when missing. Invariant: no write
    transaction may span both files — cross-file commits are not crash-atomic in
    WAL mode. Tier writes touch only ``places_db``; everything else only ``main``.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=30000')
    conn.execute('ATTACH DATABASE ? AS places_db', (str(places_db_path()),))
    # journal_mode is a persistent per-file property, but setting it is cheap and
    # keeps a sidecar created by any code path in WAL (merges must not block reads).
    conn.execute('PRAGMA places_db.journal_mode=WAL')
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
            track     REAL,
            epx       REAL,
            epy       REAL,
            epv       REAL,
            eps       REAL,
            climb     REAL,
            mode      INTEGER
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
            dew_point_c           REAL,
            abs_humidity_gm3      REAL,
            heat_index_c          REAL,
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
        -- gps_points/track_points directly. fuel_rate_lph is a reserved placeholder,
        -- stored NULL: the Pentastar has no 015E PID, so fuel rate is derived at
        -- read time (common/obd.py, serving /api/obd/economy). Column set mirrors
        -- api/sensor_schema.py's 'obd' metrics.
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

        -- Raspberry Pi host metrics (the Pi as a sensor), ingested over MQTT like the
        -- other streams. Published by sensors/system_reader.py (one row per 30s snapshot)
        -- from stdlib /proc + /sys + vcgencmd reads, so rows join gps_points on the
        -- canonical ms grid. `throttled` is the raw vcgencmd get_throttled bitmask
        -- (0 = healthy; INTEGER like the Victron enum columns); the *_now columns are
        -- its live (currently-active) bits split into 0/1 channels at poll time, so
        -- "when did it throttle" is a chartable series rather than a sticky since-boot
        -- flag. Column set mirrors api/sensor_schema.py's 'system' metrics.
        CREATE TABLE IF NOT EXISTS system_readings (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id         INTEGER NOT NULL,
            timestamp         TEXT NOT NULL,
            cpu_temp_c        REAL,
            load_1m           REAL,
            mem_used_pct      REAL,
            disk_root_pct     REAL,
            disk_nvme_pct     REAL,
            disk_nvme_free_gb REAL,
            uptime_s          REAL,
            throttled         INTEGER,
            undervolt_now     INTEGER,
            freq_capped_now   INTEGER,
            throttled_now     INTEGER,
            temp_limit_now    INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_system_sensor_time
            ON system_readings(sensor_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_system_time
            ON system_readings(timestamp);

        -- OpenWrt router telemetry (network infrastructure as a sensor), ingested
        -- over MQTT like the other streams. Published by sensors/openwrt_reader.py
        -- (one SSH round-trip per snapshot; node = the router's vault hostname, e.g.
        -- van-edge). wan_up is a 0/1 enum; *_kbps are reader-side counter deltas
        -- (NULL on the first poll and across a router reboot); wan_ping_ms is NULL
        -- when the internet is unreachable — distinct from wan_up (interface state).
        -- Column set mirrors api/sensor_schema.py's 'openwrt' metrics.
        CREATE TABLE IF NOT EXISTS openwrt_readings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id       INTEGER NOT NULL,
            timestamp       TEXT NOT NULL,
            load_1m         REAL,
            mem_used_pct    REAL,
            uptime_s        REAL,
            wan_up          INTEGER,
            wan_rx_kbps     REAL,
            wan_tx_kbps     REAL,
            wan_ping_ms     REAL,
            halow_rx_kbps   REAL,
            halow_tx_kbps   REAL,
            halow_stations  INTEGER,
            halow_rssi_dbm  REAL,
            halow_noise_dbm REAL,
            halow_tx_mbps   REAL,
            halow_rx_mbps   REAL,
            halow_temp_c    REAL,
            dhcp_leases     INTEGER,
            conntrack_count INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_openwrt_sensor_time
            ON openwrt_readings(sensor_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_openwrt_time
            ON openwrt_readings(timestamp);

        -- Dahua NVR health (recording infrastructure as a sensor), ingested over
        -- MQTT like the other streams. Published by sensors/dahua_reader.py (one
        -- fleet process, node van-nvr). hdd_ok is a 0/1 enum from the storage
        -- State field; channels_video_loss counts channels the NVR flags as not
        -- delivering video (0 = all recording); clock_offset_s is the device's
        -- NTP drift — folded to the nearest whole hour, since getCurrentTime
        -- returns TZ-local time. An unreachable NVR is a dropped reading,
        -- not a row. Column set mirrors api/sensor_schema.py's 'nvr' metrics.
        CREATE TABLE IF NOT EXISTS nvr_readings (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id           INTEGER NOT NULL,
            timestamp           TEXT NOT NULL,
            hdd_ok              INTEGER,
            hdd_err_partitions  INTEGER,
            hdd_temp_c          REAL,
            hdd_realloc_sectors INTEGER,
            hdd_power_on_h      INTEGER,
            channels_video_loss INTEGER,
            cpu_pct             REAL,
            mem_used_pct        REAL,
            clock_offset_s      REAL
        );
        CREATE INDEX IF NOT EXISTS idx_nvr_sensor_time
            ON nvr_readings(sensor_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_nvr_time
            ON nvr_readings(timestamp);

        -- Dahua camera health, one node per camera (van-cam-*), published by the
        -- same fleet process. online is a 0/1 enum — poll answered; a down camera
        -- still writes a row (online=0, other columns NULL) so outages chart.
        -- record_mode is the camera's RecordMode config enum (0 auto / 1 manual /
        -- 2 off). Column set mirrors api/sensor_schema.py's 'camera' metrics.
        CREATE TABLE IF NOT EXISTS camera_readings (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id      INTEGER NOT NULL,
            timestamp      TEXT NOT NULL,
            online         INTEGER,
            cpu_pct        REAL,
            mem_used_pct   REAL,
            uptime_s       REAL,
            clock_offset_s REAL,
            record_mode    INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_camera_sensor_time
            ON camera_readings(sensor_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_camera_time
            ON camera_readings(timestamp);

        -- Dometic CFX3 fridge telemetry, ingested over MQTT like the other streams.
        -- Polled from the fridge's DDMP TCP server by sensors/fridge_reader.py (one
        -- row per ~60s snapshot) onto the canonical ms grid. comp0/comp1 are the
        -- dual-zone compartments (either can be fridge or freezer — the setpoint
        -- decides, hence generic zone naming); power_source and battery_protection
        -- are enums (INTEGER); the *_alert columns are the fridge's own alarm flags.
        -- An unreachable fridge is a dropped cycle + stream offline, not a row.
        -- Column set mirrors api/sensor_schema.py's 'fridge' metrics.
        CREATE TABLE IF NOT EXISTS fridge_readings (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id          INTEGER NOT NULL,
            timestamp          TEXT NOT NULL,
            comp0_temp_c       REAL,
            comp1_temp_c       REAL,
            comp0_set_c        REAL,
            comp1_set_c        REAL,
            comp0_door_open    INTEGER,
            comp1_door_open    INTEGER,
            comp0_power        INTEGER,
            comp1_power        INTEGER,
            cooler_power       INTEGER,
            power_source       INTEGER,
            input_voltage_v    REAL,
            battery_protection INTEGER,
            temp_alert_cc      INTEGER,
            temp_alert_dcm     INTEGER,
            door_alert         INTEGER,
            voltage_alert      INTEGER,
            dc_current_a       REAL
        );
        CREATE INDEX IF NOT EXISTS idx_fridge_sensor_time
            ON fridge_readings(sensor_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_fridge_time
            ON fridge_readings(timestamp);

        -- The fridge's own DC power-usage history (7 buckets per span, decoded from
        -- the DDMP history topics), flattened by the reader to one row per bucket
        -- and UPSERTed by ingest via the HISTORY_TABLES spec (api/sensor_schema.py)
        -- — re-polling a window updates rows in place instead of appending
        -- near-duplicates. bucket_ts is the grid-snapped bucket start (canonical
        -- ms-UTC); the PK is exactly the read path (one fridge, span + time scans).
        CREATE TABLE IF NOT EXISTS fridge_history (
            sensor_id    INTEGER NOT NULL,
            span         TEXT NOT NULL,      -- 'hour' | 'day' | 'week'
            bucket_ts    TEXT NOT NULL,
            dc_current_a REAL,
            updated_at   TEXT NOT NULL,
            PRIMARY KEY (sensor_id, span, bucket_ts)
        ) WITHOUT ROWID;

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
        -- from raw gps_points by gps-processor; fully rebuildable (see
        -- .claude/modules/processor.md). kind='stop' rows carry dwell_start/dwell_end/radius.
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

        -- Per-satellite SKY observations (GNSS Observatory tier), written by the
        -- logger on its own ~60s throttle. The receiver's own computed az/el per
        -- SV — the long-term record behind the live /skyplot. Only positioned
        -- sats (az+el present) are stored. Foundation for the sky/obstruction map
        -- and observed-orbit prediction. See .claude/modules/observatory.md.
        CREATE TABLE IF NOT EXISTS sat_observations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            gnssid      INTEGER,
            svid        INTEGER,
            az          REAL,
            el          REAL,
            snr         REAL,
            used        INTEGER,
            health      INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_sat_observations_sv
            ON sat_observations(gnssid, svid, timestamp);
        CREATE INDEX IF NOT EXISTS idx_sat_observations_timestamp
            ON sat_observations(timestamp);

        -- gps-processor cursor (e.g. last_committed_raw_id) + any future scalar state.
        CREATE TABLE IF NOT EXISTS processing_state (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        -- Radio RX transmission log — written by the radio-recorder daemon
        -- (VOX-gated capture off the Digirig; plans/radio-platform-plan.md R8).
        -- freq_hz/mode/dcd_main are a rigctld snapshot at gate-open and read the
        -- ACTIVE MAIN BAND only, while the audio is SP1's A+B mix — dcd_main
        -- marks that tag's confidence (a sub-band signal may carry a wrong freq
        -- tag). No band column: get-VFO is unsupported, which band is Main is
        -- unreadable. lat/lon snap from the latest raw fix, NULL when stale.
        -- audio_path is relative to the audio dir and NULLed by the retention
        -- pruner — the row outlives its audio. waveform is a fixed-N JSON int
        -- array (0..255 bar heights, absolute dBFS window) derived at record
        -- time from the per-block peaks, so it survives the audio prune.
        CREATE TABLE IF NOT EXISTS radio_transmissions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_utc TEXT NOT NULL,
            ended_utc   TEXT NOT NULL,
            duration_s  REAL NOT NULL,
            freq_hz     INTEGER,
            mode        TEXT,
            dcd_main    INTEGER,
            peak_dbfs   REAL,
            rms_dbfs    REAL,
            audio_path  TEXT,
            lat         REAL,
            lon         REAL,
            waveform    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_radio_transmissions_started
            ON radio_transmissions(started_utc);

        -- Drone telemetry tier — aerial GPS tracks extracted from DJI footage by
        -- tools/import_drone.py (offline batch import, NOT the live MQTT path).
        -- One row per clip; canonical ms-UTC puts drone points on the same time
        -- axis as gps_points. Rebuildable from the source media (see
        -- .claude/modules/drone.md). media_path is the canonical rex-nas path, NULL when a
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
        -- Natural key: no model exposes a serial, so dedup on the
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

        -- Phone location-history tier — the user's Google Timeline export,
        -- imported by tools/import_phone_timeline.py (see .claude/modules/phone.md).
        -- Derived and fully rebuildable from the export (full-replace each run);
        -- canonical ms-UTC puts phone points on the same axis as gps_points.
        -- phone_paths is one contiguous breadcrumb segment (a Timeline
        -- timelinePath); the semantic layer (visits/activities) is kept out of the
        -- user-curated annotations table on purpose.
        CREATE TABLE IF NOT EXISTS phone_paths (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time  TEXT NOT NULL,
            end_time    TEXT NOT NULL,
            n_points    INTEGER NOT NULL,
            min_lat     REAL NOT NULL,
            min_lon     REAL NOT NULL,
            max_lat     REAL NOT NULL,
            max_lon     REAL NOT NULL,
            imported_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_phone_paths_start_time
            ON phone_paths(start_time);

        CREATE TABLE IF NOT EXISTS phone_track_points (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            path_id       INTEGER NOT NULL,
            timestamp     TEXT NOT NULL,
            lat           REAL NOT NULL,
            lon           REAL NOT NULL,
            importance    REAL NOT NULL,
            -- Mode of the activity segment covering this point's time (driving/
            -- walking/…), for color-by-mode rendering; NULL between labeled trips.
            activity_type TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_phone_track_points_path
            ON phone_track_points(path_id);
        CREATE INDEX IF NOT EXISTS idx_phone_track_points_timestamp
            ON phone_track_points(timestamp);

        CREATE TABLE IF NOT EXISTS phone_visits (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time    TEXT NOT NULL,
            end_time      TEXT NOT NULL,
            lat           REAL NOT NULL,
            lon           REAL NOT NULL,
            place_id      TEXT,
            semantic_type TEXT,
            probability   REAL
        );
        CREATE INDEX IF NOT EXISTS idx_phone_visits_start_time
            ON phone_visits(start_time);

        CREATE TABLE IF NOT EXISTS phone_activities (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time    TEXT NOT NULL,
            end_time      TEXT NOT NULL,
            start_lat     REAL NOT NULL,
            start_lon     REAL NOT NULL,
            end_lat       REAL NOT NULL,
            end_lon       REAL NOT NULL,
            distance_m    REAL,
            activity_type TEXT,
            probability   REAL
        );
        CREATE INDEX IF NOT EXISTS idx_phone_activities_start_time
            ON phone_activities(start_time);
    """)
    conn.commit()
    # The places tier lives in the ATTACHed sidecar, so its schema only applies
    # to connections that have one — every get_connection() caller. Bare
    # connections (tests, ad-hoc scripts) get the main schema only.
    schemas = {row[1] for row in conn.execute('PRAGMA database_list')}
    if 'places_db' in schemas:
        _init_places_schema(conn)


def _init_places_schema(conn: sqlite3.Connection) -> None:
    """Create the places tier's tables in the ATTACHed sidecar.

    The tier lives in its own file (see :func:`places_db_path`): millions of
    rebuildable-from-public-download POI rows must not inflate gps_history.db
    or its 6-hourly backup snapshot. Everything here is full-replace per
    source on import — nothing in the sidecar needs backup.
    """
    conn.executescript("""
        -- Places tier — the app's general POI substrate (NPS + RIDB + the broad
        -- OSM extract + GNIS names), synced by tools/import_places.py while
        -- online, browsed offline. One
        -- unified table across sources/kinds: columns carry only what queries
        -- filter or sort on; display-only structure (tour stops, hours, amenities,
        -- fees) rides in the details JSON. lat/lon nullable — rows without a
        -- resolvable coordinate simply never match a bbox. See
        -- .claude/modules/places.md.
        CREATE TABLE IF NOT EXISTS places_db.places (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_id   TEXT NOT NULL,
            park_code   TEXT,
            name        TEXT NOT NULL,
            lat         REAL,
            lon         REAL,
            summary     TEXT,
            details     TEXT NOT NULL,
            synced_at   TEXT NOT NULL,
            category    TEXT,
            rank        INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS places_db.idx_places_source_key
            ON places(source, source_id);
        CREATE INDEX IF NOT EXISTS places_db.idx_places_latlon
            ON places(lat, lon);
        CREATE INDEX IF NOT EXISTS places_db.idx_places_kind
            ON places(source_kind);

        -- Search index over the browse/search text (name + the ~40-char summary
        -- teaser + category/kind terms). External content: rows live only in
        -- places; the importer rebuilds the index after every import/merge
        -- (writes to the tier are bulk-only, so there are no sync triggers).
        -- FTS5 matching is token-PREFIX ('creek' matches 'Clear Creek Trail';
        -- 'lear' does not match 'Clear') — the intended search contract.
        CREATE VIRTUAL TABLE IF NOT EXISTS places_db.places_fts USING fts5(
            name, summary, category, source_kind,
            content='places', content_rowid='id'
        );

        -- Scheduled park events (ranger programs, guided walks). Kept out of
        -- places: an event is a schedule, not a place. place_event_dates is the
        -- source's pre-expanded occurrence list — one row per date × time window,
        -- park-local calendar dates as published (NOT the ms-UTC axis), so
        -- "what's on this week" is one indexed range query.
        CREATE TABLE IF NOT EXISTS places_db.place_events (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            source            TEXT NOT NULL,
            source_id         TEXT NOT NULL,
            park_code         TEXT,
            name              TEXT NOT NULL,
            lat               REAL,
            lon               REAL,
            location_text     TEXT,
            is_free           INTEGER,
            needs_reservation INTEGER,
            details           TEXT NOT NULL,
            synced_at         TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS places_db.idx_place_events_source_key
            ON place_events(source, source_id);

        CREATE TABLE IF NOT EXISTS places_db.place_event_dates (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id   INTEGER NOT NULL,
            date       TEXT NOT NULL,
            time_start TEXT,
            time_end   TEXT
        );
        CREATE INDEX IF NOT EXISTS places_db.idx_place_event_dates_date
            ON place_event_dates(date, event_id);
        CREATE INDEX IF NOT EXISTS places_db.idx_place_event_dates_event
            ON place_event_dates(event_id);

        -- Wikipedia summary cache: offline blurb + thumbnail for wiki-tagged
        -- places. Keyed by the wiki id (place_wiki_key), NOT places.id — every
        -- source merge is a full-replace, so a places-keyed cache would orphan
        -- on each one; the detail read resolves place → key from its tags at
        -- read time instead. Built off-Pi by tools/fetch_wikipedia.py, merged
        -- by tools/import_places.py --wiki-db.
        CREATE TABLE IF NOT EXISTS places_db.place_wiki (
            wiki_key   TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            lang       TEXT NOT NULL,
            extract    TEXT NOT NULL,
            page_url   TEXT,
            thumb      BLOB,
            thumb_mime TEXT,
            fetched_at TEXT NOT NULL
        );

        -- The freshness probe (updater/probes.py) reads max(fetched_at); without
        -- an index that is a full-table scan that walks every thumbnail's
        -- overflow chain (~GBs), with it an O(1) seek.
        CREATE INDEX IF NOT EXISTS places_db.idx_place_wiki_fetched
            ON place_wiki(fetched_at);
    """)
    conn.commit()
    # Rank-gated viewport reads (the map pin gate, the browse default) would
    # otherwise scan idx_places_latlon's whole NA latitude band and discard
    # ~everything by rank (seconds at 10.7M rows); a partial index per gate
    # tier makes it milliseconds. No r4: rank<=4 reads only happen at z14+,
    # where the bbox lat band is tiny. A bound `rank <= ?` still plans onto
    # these — SQLite considers bound parameter values when testing
    # partial-index usability.
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS places_db.idx_places_latlon_r1
            ON places(lat, lon) WHERE rank <= 1;
        CREATE INDEX IF NOT EXISTS places_db.idx_places_latlon_r2
            ON places(lat, lon) WHERE rank <= 2;
        CREATE INDEX IF NOT EXISTS places_db.idx_places_latlon_r3
            ON places(lat, lon) WHERE rank <= 3;

        -- Non-bbox rank-gated browse (the Places view with no fix / Everywhere
        -- mode) orders by plain (rank, name); this covering index serves the
        -- top-N directly — sorting the whole ≤3 tier (3.8M rows) took ~45 s on
        -- the Pi. The route only emits index-friendly ordering when there is
        -- no bbox, so bbox'd reads can never plan onto this instead of the
        -- latlon partials (see list_places).
        CREATE INDEX IF NOT EXISTS places_db.idx_places_rank_name
            ON places(rank, name) WHERE rank <= 3;
    """)
    conn.commit()


def place_wiki_key(details: Mapping[str, Any]) -> str | None:
    """Resolve a place's Wikipedia join key from its source tags.

    OSM rows carry ``wikidata`` (a QID) and/or ``wikipedia`` (``lang:Title``)
    tags in their raw-tag details; the QID wins (stable across article
    renames), a bare title without a language prefix assumes English, and
    multi-values take their first entry. The same function builds keys in
    ``tools/fetch_wikipedia.py`` and resolves them in the detail read, so the
    two sides cannot drift.

    Args:
        details: The place's parsed ``details`` JSON.

    Returns:
        The ``place_wiki.wiki_key`` (``'Q42'`` or ``'en:Title'``), or None.
    """
    qid = details.get('wikidata')
    if isinstance(qid, str) and qid.strip():
        return qid.strip().split(';', 1)[0].strip().upper()
    tag = details.get('wikipedia')
    if not isinstance(tag, str) or not tag.strip():
        return None
    text = tag.strip().split(';', 1)[0].strip()
    if text.lower().startswith(('http://', 'https://')):
        return None  # malformed tag (URLs belong in website=); rare, skip
    lang, sep, title = text.partition(':')
    if not sep:
        lang, title = 'en', text
    lang, title = lang.strip().lower(), title.strip().replace('_', ' ')
    if not lang or not title:
        return None
    return f'{lang}:{title}'


#: Federal-source kind → (category, rank), the same axes the OSM taxonomy
#: (tools/build_osm_pois.py TAXONOMY) assigns, so one category filter and one
#: rank×zoom pin gate governs every source. Stamped by tools/import_places.py
#: on every NPS/RIDB sync; unknown future kinds import with NULLs (never pinned
#: by a rank gate) until mapped here.
PLACES_KIND_RANKS: dict[str, tuple[str, int]] = {
    'park': ('park', 1),
    'recarea': ('park', 2),
    'campground': ('camping', 2),
    'visitorcenter': ('attraction', 2),
    'thingstodo': ('attraction', 3),
    'tour': ('attraction', 3),
    'facility': ('outdoors', 3),
    'permit': ('outdoors', 4),
    'site': ('historic', 4),
}
