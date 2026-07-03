"""MQTT ingest subscriber: the sensor system of record.

Subscribes to ``sensors/#`` and is the only writer of sensor data into SQLite.
For each message it:

- auto-upserts the ``sensors`` registry row (Decision #4: zero-config discovery),
- canonicalizes the reading's timestamp (node-stamped, with a receipt-time
  fallback — Decision #5),
- inserts a typed row into the per-type readings table, and
- applies retained ``.../status`` (LWT) updates to ``sensors.status``.

Mirrors the GPS logger's ethos: auto-reconnect (paho backoff + persistent session
so QoS-1 messages queue across a restart), a periodic heartbeat with a
dropped-reading reason breakdown, and graceful shutdown.

Run::

    uv run mqttbus/ingest.py
"""

import json
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from api.db import canonical_timestamp, get_connection, init_db
from api.sensor_schema import READING_TABLES
from common.humidity import with_derived_humidity
from mqttbus import topics
from mqttbus.client import KEEPALIVE_SECONDS, broker_host, broker_port, make_client

HEARTBEAT_SECONDS = 60

# A node NTP-synced off the Pi should never be ahead of receipt by more than a
# small skew; a larger lead means an unsynced/garbage clock, so fall back to
# receipt time. Readings that are *older* than receipt are kept as-is, so a node
# buffering across a Pi reboot replays with correct per-reading times (Decision #5).
FUTURE_SKEW_SECONDS = 60

CLIENT_ID = 'gps-ingest'


@dataclass
class IngestStats:
    """Rolling counters for the current heartbeat window.

    Tracks inserts versus drops by reason so a stalled or misbehaving stream names
    its own cause in the journal, logger-style. Per-window counters reset each
    heartbeat.
    """

    written: int = 0
    status_updates: int = 0
    ts_missing: int = 0
    ts_bad: int = 0
    ts_future: int = 0
    unknown_type: int = 0
    json_err: int = 0
    last_heartbeat: float = field(default_factory=time.monotonic)

    def reset_window(self) -> None:
        """Zero the per-window counters, preserving the heartbeat clock."""
        self.written = self.status_updates = 0
        self.ts_missing = self.ts_bad = self.ts_future = 0
        self.unknown_type = self.json_err = 0

    def heartbeat_line(self) -> str:
        """Build a one-line summary of the current window for the journal."""
        return (
            f'heartbeat: wrote={self.written} status={self.status_updates} | '
            f'dropped unknown_type={self.unknown_type} json_err={self.json_err} | '
            f'ts_fallback missing={self.ts_missing} bad={self.ts_bad} '
            f'future={self.ts_future}'
        )


def ensure_sensor(conn: sqlite3.Connection, node: str, type: str, first_seen: str) -> int:
    """Return the sensor row id, inserting a registry row if it's new.

    Does not touch ``last_seen``/``status`` (those are updated by the reading and
    status paths respectively), so calling it from either path is side-effect-free
    beyond first registration.

    Args:
        conn: Open SQLite connection.
        node: Topic ``<node>`` segment.
        type: Topic ``<type>`` segment.
        first_seen: Canonical timestamp to record if this is a new sensor.

    Returns:
        The ``sensors.id`` for ``(node, type)``.
    """
    conn.execute(
        'INSERT OR IGNORE INTO sensors (node, type, first_seen, status) '
        "VALUES (?, ?, ?, 'unknown')",
        (node, type, first_seen),
    )
    return conn.execute(
        'SELECT id FROM sensors WHERE node = ? AND type = ?', (node, type)
    ).fetchone()['id']


def resolve_timestamp(payload: dict, receipt_dt: datetime, stats: IngestStats) -> str:
    """Resolve a reading's storage timestamp from its payload (Decision #5).

    Uses the node-supplied ``ts`` when present, parseable, and not implausibly far
    in the future; otherwise falls back to receipt time and counts the reason.
    Timestamps *older* than receipt are accepted unchanged, so buffered/replayed
    readings keep their true times.

    Args:
        payload: The decoded JSON reading.
        receipt_dt: UTC time the message was received.
        stats: Counters, mutated in place when a fallback is taken.

    Returns:
        A canonical fixed-width millisecond UTC timestamp.
    """
    raw = payload.get('ts')
    if not raw:
        stats.ts_missing += 1
        return canonical_timestamp(receipt_dt.isoformat())
    try:
        dt = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        stats.ts_bad += 1
        return canonical_timestamp(receipt_dt.isoformat())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    if (dt - receipt_dt).total_seconds() > FUTURE_SKEW_SECONDS:
        stats.ts_future += 1
        return canonical_timestamp(receipt_dt.isoformat())
    return canonical_timestamp(dt.isoformat())


def record_reading(
    conn: sqlite3.Connection,
    topic: topics.SensorTopic,
    payload: dict,
    receipt_dt: datetime,
    stats: IngestStats,
) -> None:
    """Upsert the sensor and insert one typed reading row.

    Args:
        conn: Open SQLite connection.
        topic: Parsed reading topic (node + type).
        payload: Decoded JSON reading; unknown keys are ignored (forward-compat).
        receipt_dt: UTC time the message was received.
        stats: Counters, mutated in place.
    """
    spec = READING_TABLES.get(topic.type)
    if spec is None:
        # Unknown type: counted and dropped. A new stream needs its table in
        # api.db plus an entry in READING_TABLES before ingest will store it.
        stats.unknown_type += 1
        return
    receipt_iso = canonical_timestamp(receipt_dt.isoformat())
    sensor_id = ensure_sensor(conn, topic.node, topic.type, receipt_iso)
    ts = resolve_timestamp(payload, receipt_dt, stats)
    # Derived moisture channels: the node sends temp/RH; dew point and absolute
    # humidity are computed here so the columns are queryable like any metric.
    if topic.type == 'bme680':
        payload = with_derived_humidity(payload)
    metrics = spec['metrics']
    columns = ', '.join(['sensor_id', 'timestamp', *metrics])
    placeholders = ', '.join(['?'] * (len(metrics) + 2))
    values = [sensor_id, ts, *(payload.get(metric) for metric in metrics)]
    conn.execute(
        f'INSERT INTO {spec["table"]} ({columns}) VALUES ({placeholders})',
        values,
    )
    conn.execute('UPDATE sensors SET last_seen = ? WHERE id = ?', (receipt_iso, sensor_id))
    conn.commit()
    stats.written += 1


def apply_status(
    conn: sqlite3.Connection,
    topic: topics.SensorTopic,
    value: str,
    receipt_dt: datetime,
    stats: IngestStats,
) -> None:
    """Apply a retained LWT status to the registry.

    Args:
        conn: Open SQLite connection.
        topic: Parsed status topic (node + type).
        value: Status payload, e.g. ``"online"`` / ``"offline"``.
        receipt_dt: UTC time the message was received.
        stats: Counters, mutated in place.
    """
    receipt_iso = canonical_timestamp(receipt_dt.isoformat())
    ensure_sensor(conn, topic.node, topic.type, receipt_iso)
    conn.execute(
        'UPDATE sensors SET status = ? WHERE node = ? AND type = ?',
        (value, topic.node, topic.type),
    )
    conn.commit()
    stats.status_updates += 1


def on_connect(client, userdata, flags, reason_code, properties) -> None:
    """Subscribe to the full sensor tree on (re)connect."""
    client.subscribe(topics.SENSORS_WILDCARD, qos=1)
    print(f'ingest connected ({reason_code}); subscribed {topics.SENSORS_WILDCARD}', flush=True)


def on_message(client, userdata, msg) -> None:
    """Route one broker message to the reading or status path.

    Retained *reading* messages (redelivered on subscribe) are skipped to avoid
    re-inserting a reading already recorded from its live delivery; status messages
    are always applied (idempotent).
    """
    conn = userdata['conn']
    stats = userdata['stats']
    topic = topics.parse_sensor_topic(msg.topic)
    if topic is None:
        return
    receipt_dt = datetime.now(UTC)

    if topic.kind == 'status':
        value = msg.payload.decode('utf-8', 'replace').strip()
        # An empty status payload is a retained-message clear, not a liveness
        # signal (online/offline are always non-empty); ignore it so clearing a
        # retained status doesn't resurrect the sensor row.
        if not value:
            return
        apply_status(conn, topic, value, receipt_dt, stats)
        return

    if msg.retain:
        return
    try:
        payload = json.loads(msg.payload)
    except (json.JSONDecodeError, ValueError):
        stats.json_err += 1
        return
    if not isinstance(payload, dict):
        stats.json_err += 1
        return
    record_reading(conn, topic, payload, receipt_dt, stats)


def main() -> int:
    """Run the ingest loop until interrupted.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    conn = get_connection()
    init_db(conn)
    stats = IngestStats()

    client = make_client(CLIENT_ID, clean_session=False)
    client.user_data_set({'conn': conn, 'stats': stats})
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(broker_host(), broker_port(), keepalive=KEEPALIVE_SECONDS)
    client.loop_start()

    print('MQTT ingest started', flush=True)
    try:
        while True:
            time.sleep(1)
            now = time.monotonic()
            if now - stats.last_heartbeat >= HEARTBEAT_SECONDS:
                print(stats.heartbeat_line(), flush=True)
                stats.reset_window()
                stats.last_heartbeat = now
    except KeyboardInterrupt:
        print('MQTT ingest stopped', flush=True)
    finally:
        client.loop_stop()
        client.disconnect()
    return 0


if __name__ == '__main__':
    sys.exit(main())
