import os
import sqlite3
from pathlib import Path

# Canonical timestamp formatting lives in common.timefmt (a project-wide
# convention, not a database concern); re-exported here for the many call sites
# that import these from api.db.
from common.timefmt import (
    _canonical as _canonical,
)
from common.timefmt import (
    canonical_timestamp as canonical_timestamp,
)
from common.timefmt import (
    now_canonical as now_canonical,
)

DB_PATH = Path(os.environ.get('GPS_DB_PATH', Path.home() / 'gps_history.db'))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA busy_timeout=30000')
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

        -- Attractions tier — parks/public-lands POIs (self-guided tours, facility
        -- hours, things to do) synced from online sources by
        -- tools/import_attractions.py while the Pi has WAN; browsed offline.
        -- Fully rebuildable (full-replace per source on import). One unified
        -- table across sources/kinds: columns carry only what queries filter or
        -- sort on; display-only structure (tour stops, hours, amenities, fees)
        -- rides in the details JSON. lat/lon nullable — rows without a resolvable
        -- coordinate simply never match a bbox. See .claude/modules/attractions.md.
        CREATE TABLE IF NOT EXISTS attractions (
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
            synced_at   TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_attractions_source_key
            ON attractions(source, source_id);
        CREATE INDEX IF NOT EXISTS idx_attractions_latlon
            ON attractions(lat, lon);
        CREATE INDEX IF NOT EXISTS idx_attractions_kind
            ON attractions(source_kind);

        -- Scheduled park events (ranger programs, guided walks). Kept out of
        -- attractions: an event is a schedule, not a place. attraction_event_dates
        -- is the source's pre-expanded occurrence list — one row per date × time
        -- window, park-local calendar dates as published (NOT the ms-UTC axis),
        -- so "what's on this week" is one indexed range query.
        CREATE TABLE IF NOT EXISTS attraction_events (
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
        CREATE UNIQUE INDEX IF NOT EXISTS idx_attraction_events_source_key
            ON attraction_events(source, source_id);

        CREATE TABLE IF NOT EXISTS attraction_event_dates (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id   INTEGER NOT NULL,
            date       TEXT NOT NULL,
            time_start TEXT,
            time_end   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_attraction_event_dates_date
            ON attraction_event_dates(date, event_id);
        CREATE INDEX IF NOT EXISTS idx_attraction_event_dates_event
            ON attraction_event_dates(event_id);

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
    _migrate_live_throttle_channels(conn)


#: Mirrors sensors/system_reader.py LIVE_THROTTLE_CHANNELS (not imported — api does
#: not depend on the sensors package, and this migration is temporary anyway).
_LIVE_THROTTLE_COLUMNS: dict[str, int] = {
    'undervolt_now': 0x1,
    'freq_capped_now': 0x2,
    'throttled_now': 0x4,
    'temp_limit_now': 0x8,
}


def _migrate_live_throttle_channels(conn: sqlite3.Connection) -> None:
    """One-shot: add the live-throttle 0/1 columns and backfill from the bitmask.

    Gated on the column add, so steady-state startups pay one PRAGMA. The backfill
    is pure SQL — each channel is a bit-mask of the already-stored raw ``throttled``
    (NULL bitmask rows stay NULL). Drop once landed on the Pi's DB, like the earlier
    one-shot migrations.
    """
    # Positional index — callers pass connections with and without a row factory.
    existing = {row[1] for row in conn.execute('PRAGMA table_info(system_readings)')}
    missing = [col for col in _LIVE_THROTTLE_COLUMNS if col not in existing]
    if not missing:
        return
    for col in missing:
        conn.execute(f'ALTER TABLE system_readings ADD COLUMN {col} INTEGER')
    assignments = ', '.join(
        f'{col} = (throttled & {bit}) != 0' for col, bit in _LIVE_THROTTLE_COLUMNS.items()
    )
    backfilled = conn.execute(f'UPDATE system_readings SET {assignments}').rowcount
    conn.commit()
    print(
        f'Migration: added system_readings column(s) {", ".join(missing)}; '
        f'backfilled {backfilled} row(s)'
    )
