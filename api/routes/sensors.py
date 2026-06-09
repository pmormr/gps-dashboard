"""Sensor viewer: read-only registry, latest readings, and history.

Backs the ``/sensors`` page. Everything here reads from SQLite — the ingest
subscriber (``mqttbus/ingest.py``) is the only writer — so this view needs no
MQTT connection and works regardless of the broker's websockets support
(Phase 3 blocker F). The page polls these JSON endpoints; a future live upgrade
can swap the poll for an MQTT-over-WS push without changing the schema.
"""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template, request

from api.db import canonical_timestamp, get_connection

sensors_bp = Blueprint('sensors', __name__)

# Per-type readings table + the metric columns to surface, in display order.
# Adding a sensor type means adding a table and an entry here; the registry,
# latest-reading, and history paths are all driven off this map.
READING_TABLES = {
    'bme680': {
        'table': 'bme680_readings',
        'metrics': [
            'temp_c', 'humidity_pct', 'pressure_hpa', 'iaq', 'iaq_accuracy',
            'co2_equivalent', 'breath_voc_equivalent', 'gas_ohms',
        ],
    },
}

DEFAULT_HISTORY_HOURS = 24
MAX_READINGS = 20000


def _latest_reading(conn, sensor_id, type):
    """Return the most recent reading row for a sensor, or None.

    Args:
        conn: Open SQLite connection.
        sensor_id: ``sensors.id``.
        type: Sensor type, used to pick the readings table.

    Returns:
        The latest reading as a dict (timestamp + metric columns), or None if
        the type is unknown or the sensor has no readings yet.
    """
    spec = READING_TABLES.get(type)
    if spec is None:
        return None
    cols = ', '.join(['timestamp', *spec['metrics']])
    row = conn.execute(
        f"SELECT {cols} FROM {spec['table']} "
        "WHERE sensor_id = ? ORDER BY timestamp DESC LIMIT 1",
        (sensor_id,),
    ).fetchone()
    return dict(row) if row else None


@sensors_bp.get('/api/sensors')
def list_sensors():
    """Registry rows, each with its latest reading embedded.

    One round trip for the current-values panel: liveness (status/last_seen)
    plus the most recent value of every metric.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, node, type, location, description, first_seen, last_seen, "
        "status FROM sensors ORDER BY node, type"
    ).fetchall()
    sensors = []
    for row in rows:
        sensor = dict(row)
        sensor['latest'] = _latest_reading(conn, row['id'], row['type'])
        sensors.append(sensor)
    return jsonify({'sensors': sensors, 'metrics': READING_TABLES})


@sensors_bp.get('/api/sensors/<int:sensor_id>/readings')
def sensor_readings(sensor_id):
    """History for one sensor over a time range, for the trend chart.

    Defaults to the trailing ``DEFAULT_HISTORY_HOURS`` when no range is given.
    Rows are ascending by time so the chart plots left-to-right.
    """
    conn = get_connection()
    sensor = conn.execute(
        "SELECT id, type FROM sensors WHERE id = ?", (sensor_id,)
    ).fetchone()
    if sensor is None:
        return jsonify({'error': f'No sensor with id {sensor_id}'}), 404
    spec = READING_TABLES.get(sensor['type'])
    if spec is None:
        return jsonify({'error': f"Unknown sensor type '{sensor['type']}'"}), 400

    end = request.args.get('end')
    start = request.args.get('start')
    try:
        end_ts = canonical_timestamp(end) if end else canonical_timestamp(
            datetime.now(timezone.utc).isoformat())
        start_ts = canonical_timestamp(start) if start else canonical_timestamp(
            (datetime.now(timezone.utc)
             - timedelta(hours=DEFAULT_HISTORY_HOURS)).isoformat())
    except ValueError as exc:
        return jsonify({'error': f'Invalid timestamp: {exc}'}), 400

    try:
        limit = min(int(request.args.get('limit', MAX_READINGS)), MAX_READINGS)
    except ValueError:
        return jsonify({'error': "'limit' must be an integer"}), 400

    cols = ', '.join(['timestamp', *spec['metrics']])
    rows = conn.execute(
        f"SELECT {cols} FROM {spec['table']} "
        "WHERE sensor_id = ? AND timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp ASC LIMIT ?",
        (sensor_id, start_ts, end_ts, limit),
    ).fetchall()
    readings = [dict(r) for r in rows]
    return jsonify({
        'type': sensor['type'],
        'metrics': spec['metrics'],
        'start': start_ts,
        'end': end_ts,
        'readings': readings,
        'count': len(readings),
        'truncated': len(readings) == limit,
    })


@sensors_bp.get('/sensors')
def sensors_page():
    """Render the sensor viewer shell; data is loaded client-side."""
    return render_template('sensors.html')
