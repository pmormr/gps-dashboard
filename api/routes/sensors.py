"""Sensor read API: registry, latest readings, history, and bucketed series.

Backs the SPA's Systems (current values) and Trends (charts) views. Everything
here reads from SQLite — the ingest subscriber (``mqttbus/ingest.py``) is the
only writer — so these endpoints need no MQTT connection and work regardless of
the broker's websockets support; a future live upgrade can swap the poll for an
MQTT-over-WS push without changing the schema.
"""

from flask import Blueprint, jsonify, request

from api.db import get_connection
from api.params import parse_limit, parse_time_window
from api.sensor_schema import METRIC_META, READING_TABLES
from api.sensors_read import latest_reading
from common.timefmt import epoch_seconds

sensors_bp = Blueprint('sensors', __name__)

DEFAULT_HISTORY_HOURS = 24
MAX_READINGS = 20000

# /api/sensors/series — the bucketed multi-metric trend engine (feeds the SPA Trends view).
DEFAULT_BUCKETS = 1000  # target point count across the window when ?buckets is absent
MAX_BUCKETS = 2000  # clamp: bounds both the SQL grouping and the wire payload
MAX_SERIES = 12  # cap on overlaid metrics per request


@sensors_bp.get('/api/sensors')
def list_sensors():
    """Registry rows, each with its latest reading embedded.

    One round trip for the current-values panel: liveness (status/last_seen)
    plus the most recent value of every metric.
    """
    conn = get_connection()
    rows = conn.execute(
        'SELECT id, node, type, location, description, first_seen, last_seen, '
        'status FROM sensors ORDER BY node, type'
    ).fetchall()
    sensors = []
    for row in rows:
        sensor = dict(row)
        sensor['latest'] = latest_reading(conn, row['id'], row['type'])
        sensors.append(sensor)
    return jsonify({'sensors': sensors, 'metrics': READING_TABLES, 'meta': METRIC_META})


@sensors_bp.get('/api/sensors/<int:sensor_id>/readings')
def sensor_readings(sensor_id):
    """History for one sensor over a time range, for the trend chart.

    Defaults to the trailing ``DEFAULT_HISTORY_HOURS`` when no range is given.
    Rows are ascending by time so the chart plots left-to-right.
    """
    conn = get_connection()
    sensor = conn.execute('SELECT id, type FROM sensors WHERE id = ?', (sensor_id,)).fetchone()
    if sensor is None:
        return jsonify({'error': f'No sensor with id {sensor_id}'}), 404
    spec = READING_TABLES.get(sensor['type'])
    if spec is None:
        return jsonify({'error': f"Unknown sensor type '{sensor['type']}'"}), 400

    start_ts, end_ts, err = parse_time_window(request.args, DEFAULT_HISTORY_HOURS)
    if err:
        return err

    limit, err = parse_limit(request.args, default=MAX_READINGS, maximum=MAX_READINGS)
    if err:
        return err

    cols = ', '.join(['timestamp', *spec['metrics']])
    rows = conn.execute(
        f'SELECT {cols} FROM {spec["table"]} '
        'WHERE sensor_id = ? AND timestamp >= ? AND timestamp <= ? '
        'ORDER BY timestamp ASC LIMIT ?',
        (sensor_id, start_ts, end_ts, limit),
    ).fetchall()
    readings = [dict(r) for r in rows]
    return jsonify(
        {
            'type': sensor['type'],
            'metrics': spec['metrics'],
            'start': start_ts,
            'end': end_ts,
            'readings': readings,
            'count': len(readings),
            'truncated': len(readings) == limit,
        }
    )


def _canonical_to_epoch_s(ts: str) -> int:
    """Whole-second epoch for a canonical millisecond-UTC timestamp.

    Used to lay out the bucket grid in the same ``epoch // bucket_s`` space the
    SQL ``GROUP BY`` uses, so grouped rows index straight into the dense grid.

    Args:
        ts: A canonical ``...Z`` timestamp (see :func:`api.db.canonical_timestamp`).

    Returns:
        Seconds since the Unix epoch (UTC), truncated to whole seconds.
    """
    return int(epoch_seconds(ts))


def _parse_metric_specs(conn, raw):
    """Resolve ``sensorId.column`` metric addresses against the sensor registry.

    Args:
        conn: Open SQLite connection.
        raw: The ``metrics`` query value (comma-separated ``<sensor_id>.<column>``).

    Returns:
        ``(specs, error)`` where ``specs`` is an ordered list of
        ``(sensor_id, type, column)`` tuples (request order, de-duplicated) and
        ``error`` is None; or ``(None, (json, status))`` on a malformed address,
        an unknown sensor, or a column not in that sensor type's metric set.
    """
    addrs = [a.strip() for a in raw.split(',') if a.strip()]
    if not addrs:
        return None, (jsonify({'error': "'metrics' must list at least one sensorId.column"}), 400)
    if len(addrs) > MAX_SERIES:
        return None, (jsonify({'error': f'At most {MAX_SERIES} metrics per request'}), 400)

    types: dict[int, str] = {}
    for row in conn.execute('SELECT id, type FROM sensors').fetchall():
        types[row['id']] = row['type']

    specs: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str]] = set()
    for addr in addrs:
        sensor_part, sep, column = addr.partition('.')
        if not sep or not sensor_part.isdigit() or not column:
            return None, (jsonify({'error': f"Malformed metric address '{addr}'"}), 400)
        sensor_id = int(sensor_part)
        type = types.get(sensor_id)
        if type is None:
            return None, (jsonify({'error': f'No sensor with id {sensor_id}'}), 400)
        spec = READING_TABLES.get(type)
        if spec is None or column not in spec['metrics']:
            return None, (
                jsonify({'error': f"Unknown metric '{column}' for sensor {sensor_id}"}),
                400,
            )
        if (sensor_id, column) not in seen:
            seen.add((sensor_id, column))
            specs.append((sensor_id, type, column))
    return specs, None


@sensors_bp.get('/api/sensors/series')
def sensor_series():
    """Time-bucketed, aligned multi-metric series — the trend-graph engine.

    Takes ``metrics`` (comma-separated ``<sensor_id>.<column>`` addresses), a
    ``start``/``end`` window (defaults trailing ``DEFAULT_HISTORY_HOURS``), and a
    ``buckets`` target resolution. Returns one shared bucket grid (``x``, epoch
    ms) plus, per metric, an aligned ``values`` array (averaged per bucket, NULL
    for empty buckets) carrying its :data:`METRIC_META` presentation fields. The
    dense grid puts every metric — across sensors and cadences — on one axis so
    the client overlays them directly; bucketing bounds long windows (a 2-week
    range that would truncate ``/readings`` collapses to ``buckets`` points).
    """
    conn = get_connection()
    raw = request.args.get('metrics', '')
    specs, err = _parse_metric_specs(conn, raw)
    if err:
        return err

    start_ts, end_ts, terr = parse_time_window(request.args, DEFAULT_HISTORY_HOURS)
    if terr:
        return terr
    if start_ts >= end_ts:
        return jsonify({'error': "'start' must be before 'end'"}), 400

    buckets, berr = parse_limit(
        request.args, default=DEFAULT_BUCKETS, maximum=MAX_BUCKETS, key='buckets'
    )
    if berr:
        return berr

    start_s = _canonical_to_epoch_s(start_ts)
    end_s = _canonical_to_epoch_s(end_ts)
    bucket_s = max(1, round((end_s - start_s) / buckets))
    start_bucket = start_s // bucket_s
    n = end_s // bucket_s - start_bucket + 1
    x = [(start_bucket + i) * bucket_s * 1000 for i in range(n)]

    # One query per sensor covers all its requested columns; scatter the grouped
    # rows into each column's aligned grid by bucket index. Per metric: the avg
    # line plus the min/max envelope (per-bucket spread), so downsampling a long
    # window keeps spikes (voltage sag, RPM) visible instead of averaging them out.
    by_sensor: dict[tuple[int, str], list[str]] = {}
    for sensor_id, type, column in specs:
        by_sensor.setdefault((sensor_id, type), []).append(column)
    keys = [(sensor_id, column) for sensor_id, _, column in specs]
    avg: dict[tuple[int, str], list[float | None]] = {k: [None] * n for k in keys}
    lo: dict[tuple[int, str], list[float | None]] = {k: [None] * n for k in keys}
    hi: dict[tuple[int, str], list[float | None]] = {k: [None] * n for k in keys}
    for (sensor_id, type), cols in by_sensor.items():
        table = READING_TABLES[type]['table']
        select = ', '.join(
            f'AVG("{c}") AS "a_{c}", MIN("{c}") AS "lo_{c}", MAX("{c}") AS "hi_{c}"' for c in cols
        )
        rows = conn.execute(
            f"SELECT CAST(strftime('%s', substr(timestamp, 1, 19)) AS INTEGER) / ? AS b, "
            f'{select} FROM {table} '
            'WHERE sensor_id = ? AND timestamp >= ? AND timestamp <= ? GROUP BY b',
            (bucket_s, sensor_id, start_ts, end_ts),
        ).fetchall()
        for row in rows:
            idx = row['b'] - start_bucket
            if 0 <= idx < n:
                for c in cols:
                    avg[(sensor_id, c)][idx] = row[f'a_{c}']
                    lo[(sensor_id, c)][idx] = row[f'lo_{c}']
                    hi[(sensor_id, c)][idx] = row[f'hi_{c}']

    series = []
    for sensor_id, _, column in specs:
        meta = METRIC_META.get(column)
        key = (sensor_id, column)
        series.append(
            {
                'metric': f'{sensor_id}.{column}',
                'sensor_id': sensor_id,
                'column': column,
                'label': meta['label'] if meta else column,
                'unit': meta['unit'] if meta else '',
                'color': meta['color'] if meta else '#94a3b8',
                'dec': meta['dec'] if meta else 1,
                'convert': meta['convert'] if meta else None,
                'y_range': meta['y_range'] if meta else None,
                'group': meta['group'] if meta else '',
                'smooth': meta['smooth'] if meta else 0,
                'values': avg[key],
                'min': lo[key],
                'max': hi[key],
            }
        )
    return jsonify(
        {'start': start_ts, 'end': end_ts, 'bucket_ms': bucket_s * 1000, 'x': x, 'series': series}
    )
