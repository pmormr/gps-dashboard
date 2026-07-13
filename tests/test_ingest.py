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

from api.db import init_db
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


def test_bme680_derives_moisture_channels(conn: sqlite3.Connection) -> None:
    """Ingest fills dew_point_c/abs_humidity_gm3 from temp+RH; NULL when inputs absent."""
    topic = topics.SensorTopic(node='cabin', type='bme680', kind='reading')
    record_reading(
        conn, topic, {'ts': TS, 'temp_c': 20.0, 'humidity_pct': 50.0}, RECEIPT, IngestStats()
    )
    record_reading(conn, topic, {'ts': TS, 'temp_c': 20.0}, RECEIPT, IngestStats())

    full, partial = conn.execute(
        'SELECT dew_point_c, abs_humidity_gm3 FROM bme680_readings ORDER BY id'
    ).fetchall()
    assert full['dew_point_c'] == pytest.approx(9.26, abs=0.05)
    assert full['abs_humidity_gm3'] == pytest.approx(8.6, abs=0.1)
    assert partial['dew_point_c'] is None
    assert partial['abs_humidity_gm3'] is None


def test_records_obd_reading(conn: sqlite3.Connection) -> None:
    """An obd payload lands in obd_readings; the wide column set maps correctly."""
    topic = topics.SensorTopic(node='van', type='obd', kind='reading')
    payload = {'ts': TS, 'rpm': 1173.0, 'map_kpa': 52.0, 'fuel_level_pct': 64.7}
    record_reading(conn, topic, payload, RECEIPT, IngestStats())

    row = conn.execute('SELECT * FROM obd_readings').fetchone()
    assert row['rpm'] == 1173.0
    assert row['map_kpa'] == 52.0
    assert row['fuel_rate_lph'] is None  # reserved placeholder — the reader never sends it
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


def test_records_openwrt_reading(conn: sqlite3.Connection) -> None:
    """An openwrt payload lands in openwrt_readings; enum + delta columns map."""
    topic = topics.SensorTopic(node='van-edge', type='openwrt', kind='reading')
    payload = {'ts': TS, 'wan_up': 1, 'halow_rssi_dbm': -54.0, 'halow_temp_c': 59.0}
    record_reading(conn, topic, payload, RECEIPT, IngestStats())

    row = conn.execute('SELECT * FROM openwrt_readings').fetchone()
    assert row['wan_up'] == 1
    assert row['halow_rssi_dbm'] == -54.0
    assert row['halow_temp_c'] == 59.0
    assert row['wan_rx_kbps'] is None  # first poll has no delta → NULL
    registered = conn.execute(
        "SELECT 1 FROM sensors WHERE node = 'van-edge' AND type = 'openwrt'"
    ).fetchone()
    assert registered


def test_records_nvr_and_camera_readings(conn: sqlite3.Connection) -> None:
    """The two Dahua fleet types land in their tables (one process, many nodes)."""
    nvr_topic = topics.SensorTopic(node='van-nvr', type='nvr', kind='reading')
    record_reading(
        conn, nvr_topic, {'ts': TS, 'hdd_ok': 1, 'hdd_temp_c': 48.0}, RECEIPT, IngestStats()
    )
    cam_topic = topics.SensorTopic(node='van-cam-front', type='camera', kind='reading')
    record_reading(
        conn, cam_topic, {'ts': TS, 'online': 0, 'record_mode': None}, RECEIPT, IngestStats()
    )

    nvr = conn.execute('SELECT * FROM nvr_readings').fetchone()
    assert nvr['hdd_ok'] == 1 and nvr['hdd_temp_c'] == 48.0
    assert nvr['channels_video_loss'] is None
    cam = conn.execute('SELECT * FROM camera_readings').fetchone()
    assert cam['online'] == 0 and cam['record_mode'] is None
    nodes = {
        r['node'] for r in conn.execute("SELECT node FROM sensors WHERE type IN ('nvr', 'camera')")
    }
    assert nodes == {'van-nvr', 'van-cam-front'}


def test_records_fridge_history_upsert(conn: sqlite3.Connection) -> None:
    """History rows UPSERT on (sensor_id, span, bucket_ts): re-polls converge in place."""
    topic = topics.SensorTopic(node='van', type='fridge', kind='reading')
    row = {'span': 'hour', 'bucket_ts': '2026-07-13T17:00:00.000Z', 'dc_current_a': 0.3}
    stats = IngestStats()
    record_reading(conn, topic, {'ts': TS, 'history': [row]}, RECEIPT, stats)
    record_reading(
        conn, topic, {'ts': TS, 'history': [{**row, 'dc_current_a': 1.5}]}, RECEIPT, stats
    )

    stored = conn.execute('SELECT * FROM fridge_history').fetchall()
    assert len(stored) == 1
    assert stored[0]['dc_current_a'] == 1.5
    assert stored[0]['updated_at'] == TS
    assert stats.hist_rows == 2


def test_fridge_history_spans_key_separately(conn: sqlite3.Connection) -> None:
    """The same bucket_ts under different spans is two rows, not one."""
    topic = topics.SensorTopic(node='van', type='fridge', kind='reading')
    ts = '2026-07-13T00:00:00.000Z'
    history = [
        {'span': 'day', 'bucket_ts': ts, 'dc_current_a': 0.5},
        {'span': 'week', 'bucket_ts': ts, 'dc_current_a': 0.4},
    ]
    record_reading(conn, topic, {'ts': TS, 'history': history}, RECEIPT, IngestStats())

    spans = {r['span']: r['dc_current_a'] for r in conn.execute('SELECT * FROM fridge_history')}
    assert spans == {'day': 0.5, 'week': 0.4}


def test_fridge_history_malformed_rows_counted_not_written(conn: sqlite3.Connection) -> None:
    """Non-dict rows and missing/non-string key parts are skipped and counted."""
    topic = topics.SensorTopic(node='van', type='fridge', kind='reading')
    history = [
        'not a dict',
        {'span': 'hour', 'dc_current_a': 1.0},  # no bucket_ts
        {'span': 'hour', 'bucket_ts': 12345, 'dc_current_a': 1.0},  # non-string key
        {'span': 'hour', 'bucket_ts': '2026-07-13T17:00:00.000Z', 'dc_current_a': None},
    ]
    stats = IngestStats()
    record_reading(conn, topic, {'ts': TS, 'history': history}, RECEIPT, stats)

    stored = conn.execute('SELECT * FROM fridge_history').fetchall()
    assert len(stored) == 1
    assert stored[0]['dc_current_a'] is None  # a NULL metric is valid; bad keys are not
    assert stats.hist_bad == 3
    assert stats.written == 1  # the flat reading row still lands


def test_fridge_reading_without_history_is_plain_insert(conn: sqlite3.Connection) -> None:
    """A fridge payload with no history key writes the flat row and nothing else."""
    topic = topics.SensorTopic(node='van', type='fridge', kind='reading')
    record_reading(
        conn, topic, {'ts': TS, 'comp0_temp_c': 2.0, 'dc_current_a': 0.9}, RECEIPT, IngestStats()
    )

    row = conn.execute('SELECT * FROM fridge_readings').fetchone()
    assert row['comp0_temp_c'] == 2.0
    assert row['dc_current_a'] == 0.9
    assert conn.execute('SELECT COUNT(*) FROM fridge_history').fetchone()[0] == 0


def test_unknown_type_counted_not_written(conn: sqlite3.Connection) -> None:
    """A reading for an unregistered type increments unknown_type and writes nothing."""
    topic = topics.SensorTopic(node='x', type='mystery', kind='reading')
    stats = IngestStats()
    record_reading(conn, topic, {'ts': TS}, RECEIPT, stats)

    assert stats.unknown_type == 1
    assert stats.written == 0
    assert conn.execute("SELECT 1 FROM sensors WHERE type = 'mystery'").fetchone() is None
