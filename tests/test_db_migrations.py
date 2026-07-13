"""Tests for the one-shot migrations in ``api.db``.

Kept in their own file so the tests drop with the migrations once they've run on
the Pi's DB (the only long-lived database).
"""

from __future__ import annotations

import sqlite3

from api.db import _migrate_fridge_dc_current, _migrate_live_throttle_channels

LIVE_COLUMNS = ('undervolt_now', 'freq_capped_now', 'throttled_now', 'temp_limit_now')


def _legacy_conn() -> sqlite3.Connection:
    """Return an in-memory DB with the pre-migration system_readings shape."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute(
        'CREATE TABLE system_readings ('
        'id INTEGER PRIMARY KEY, sensor_id INTEGER, timestamp TEXT, throttled INTEGER)'
    )
    return conn


def test_live_throttle_backfill() -> None:
    """The migration adds the channels and backfills them from the stored bitmask."""
    conn = _legacy_conn()
    conn.executemany(
        "INSERT INTO system_readings (sensor_id, timestamp, throttled) VALUES (1, 't', ?)",
        [(0x50005,), (0x50000,), (0,), (None,)],
    )
    _migrate_live_throttle_channels(conn)

    rows = [
        tuple(row[col] for col in LIVE_COLUMNS)
        for row in conn.execute('SELECT * FROM system_readings ORDER BY id')
    ]
    assert rows == [
        (1, 0, 1, 0),  # under-volt + throttled live, sticky bits ignored
        (0, 0, 0, 0),  # sticky-only mask: condition over
        (0, 0, 0, 0),  # healthy
        (None, None, None, None),  # off-Pi reading stays unknown
    ]


def test_live_throttle_migration_is_idempotent() -> None:
    """A second run is a no-op once the columns exist."""
    conn = _legacy_conn()
    _migrate_live_throttle_channels(conn)
    _migrate_live_throttle_channels(conn)
    columns = [row['name'] for row in conn.execute('PRAGMA table_info(system_readings)')]
    assert columns.count('undervolt_now') == 1


def test_fridge_dc_current_migration_adds_column_once() -> None:
    """The pre-history fridge_readings shape gains dc_current_a; reruns are no-ops."""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute(
        'CREATE TABLE fridge_readings ('
        'id INTEGER PRIMARY KEY, sensor_id INTEGER, timestamp TEXT, comp0_temp_c REAL)'
    )
    _migrate_fridge_dc_current(conn)
    _migrate_fridge_dc_current(conn)

    columns = [row['name'] for row in conn.execute('PRAGMA table_info(fridge_readings)')]
    assert columns.count('dc_current_a') == 1
