import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get('GPS_DB_PATH', Path(__file__).parent.parent / 'gps_history.db'))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
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
    """)
    conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='location_history'"
    ).fetchone()
    if row is None:
        return
    count = conn.execute("SELECT COUNT(*) FROM location_history").fetchone()[0]
    conn.execute("DROP TABLE location_history")
    conn.commit()
    print(f"Migration: dropped legacy location_history table ({count} rows)")
