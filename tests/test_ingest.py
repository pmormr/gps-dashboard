"""Tests for the column-driven MQTT ingest writer (``mqttbus/ingest.py``).

Guards the shared-spec refactor (O-ingest): a reading for each known sensor type
must land in its table with payload values mapped to the right columns, unknown
payload keys ignored, absent columns left NULL, and the registry auto-created. An
unregistered type is counted and dropped without writing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from api.db import init_db, migrate
from mqttbus import topics
from mqttbus.ingest import IngestStats, record_reading

RECEIPT = datetime(2026, 6, 22, 20, 0, 0, tzinfo=UTC)
TS = '2026-06-22T20:00:00.000Z'


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """An in-memory SQLite DB with the full schema initialised."""
    connection = sqlite3.connect(':memory:')
    connection.row_factory = sqlite3.Row
    init_db(connection)
    migrate(connection)
    yield connection
    connection.close()


def test_records_bme680_reading(conn: sqlite3.Connection) -> None:
    """A bme680 payload lands in bme680_readings, values mapped by column name."""
    topic = topics.SensorTopic(node='cabin', type='bme680', kind='reading')
    payload = {'ts': TS, 'temp_c': 22.5, 'humidity_pct': 48.0, 'not_a_column': 1}
    record_reading(conn, topic, payload, RECEIPT, IngestStats())

    row = conn.execute('SELECT * FROM bme680_readings').fetchone()
    assert row['temp_c'] == 22.5
    assert row['humidity_pct'] == 48.0
    assert row['gas_ohms'] is None  # absent from payload → NULL
    assert conn.execute("SELECT 1 FROM sensors WHERE node = 'cabin' AND type = 'bme680'").fetchone()


def test_records_obd_reading(conn: sqlite3.Connection) -> None:
    """An obd payload lands in obd_readings; the wide column set maps correctly."""
    topic = topics.SensorTopic(node='van', type='obd', kind='reading')
    payload = {'ts': TS, 'rpm': 1173.0, 'map_kpa': 52.0, 'fuel_level_pct': 64.7}
    record_reading(conn, topic, payload, RECEIPT, IngestStats())

    row = conn.execute('SELECT * FROM obd_readings').fetchone()
    assert row['rpm'] == 1173.0
    assert row['map_kpa'] == 52.0
    assert row['fuel_rate_lph'] is None  # reserved placeholder, never sent in Phase 1
    assert conn.execute("SELECT 1 FROM sensors WHERE node = 'van' AND type = 'obd'").fetchone()


def test_records_victron_reading(conn: sqlite3.Connection) -> None:
    """A victron payload lands in victron_readings; the wide column set maps correctly."""
    topic = topics.SensorTopic(node='house', type='victron', kind='reading')
    payload = {'ts': TS, 'battery_soc': 57.1, 'pv_power': 13.7, 'not_a_column': 1}
    record_reading(conn, topic, payload, RECEIPT, IngestStats())

    row = conn.execute('SELECT * FROM victron_readings').fetchone()
    assert row['battery_soc'] == 57.1
    assert row['pv_power'] == 13.7
    assert row['vebus_state'] is None  # absent from payload → NULL
    registered = conn.execute(
        "SELECT 1 FROM sensors WHERE node = 'house' AND type = 'victron'"
    ).fetchone()
    assert registered


def test_unknown_type_counted_not_written(conn: sqlite3.Connection) -> None:
    """A reading for an unregistered type increments unknown_type and writes nothing."""
    topic = topics.SensorTopic(node='x', type='mystery', kind='reading')
    stats = IngestStats()
    record_reading(conn, topic, {'ts': TS}, RECEIPT, stats)

    assert stats.unknown_type == 1
    assert stats.written == 0
    assert conn.execute("SELECT 1 FROM sensors WHERE type = 'mystery'").fetchone() is None
