"""Tests for GET /api/sensors/series — the bucketed multi-metric trend engine.

Pins the dense-grid bucketing + alignment, within-bucket averaging, cross-sensor
overlay on a shared axis, and the address/window validation (see
plans/sensor-graphing-plan.md).
"""

import api.db as db

# A 5-minute window; with buckets=5 the server picks 60 s buckets, giving a
# deterministic 6-slot grid (minutes 0..5 inclusive of both bounds).
WINDOW = {'start': '2026-06-20T12:00:00.000Z', 'end': '2026-06-20T12:05:00.000Z', 'buckets': 5}


def _sensor(conn, node, type):
    """Register a sensor and return its id."""
    cur = conn.execute(
        "INSERT INTO sensors (node, type, first_seen, status) VALUES (?, ?, ?, 'online')",
        (node, type, '2026-06-20T11:00:00.000Z'),
    )
    return cur.lastrowid


def _vic(conn, sensor_id, ts, voltage):
    conn.execute(
        'INSERT INTO victron_readings (sensor_id, timestamp, battery_voltage) VALUES (?, ?, ?)',
        (sensor_id, ts, voltage),
    )


def _bme(conn, sensor_id, ts, temp_c):
    conn.execute(
        'INSERT INTO bme680_readings (sensor_id, timestamp, temp_c) VALUES (?, ?, ?)',
        (sensor_id, ts, temp_c),
    )


def test_buckets_average_and_align_with_nulls(client):
    conn = db.get_connection()
    sid = _sensor(conn, 'house', 'victron')
    # Two readings in bucket 0 (minute 0) → averaged; one in bucket 2; rest empty.
    _vic(conn, sid, '2026-06-20T12:00:10.000Z', 13.0)
    _vic(conn, sid, '2026-06-20T12:00:50.000Z', 13.4)
    _vic(conn, sid, '2026-06-20T12:02:30.000Z', 12.8)
    conn.commit()
    conn.close()

    body = client.get(
        '/api/sensors/series', query_string={**WINDOW, 'metrics': f'{sid}.battery_voltage'}
    ).get_json()

    assert body['bucket_ms'] == 60000
    assert len(body['x']) == 6
    assert body['x'][1] - body['x'][0] == 60000
    (s,) = body['series']
    assert s['metric'] == f'{sid}.battery_voltage'
    assert s['values'] == [13.2, None, 12.8, None, None, None]


def test_min_max_envelope_brackets_the_average(client):
    conn = db.get_connection()
    sid = _sensor(conn, 'house', 'victron')
    # Two readings in bucket 0: avg 13.2, spread 13.0–13.4.
    _vic(conn, sid, '2026-06-20T12:00:10.000Z', 13.0)
    _vic(conn, sid, '2026-06-20T12:00:50.000Z', 13.4)
    conn.commit()
    conn.close()

    body = client.get(
        '/api/sensors/series', query_string={**WINDOW, 'metrics': f'{sid}.battery_voltage'}
    ).get_json()
    (s,) = body['series']
    assert s['values'][0] == 13.2
    assert s['min'][0] == 13.0
    assert s['max'][0] == 13.4
    # Empty buckets are null across all three arrays.
    assert s['min'][1] is None and s['max'][1] is None


def test_meta_fields_travel_with_each_series(client):
    conn = db.get_connection()
    sid = _sensor(conn, 'house', 'victron')
    _vic(conn, sid, '2026-06-20T12:00:10.000Z', 13.0)
    conn.commit()
    conn.close()

    body = client.get(
        '/api/sensors/series', query_string={**WINDOW, 'metrics': f'{sid}.battery_voltage'}
    ).get_json()
    (s,) = body['series']
    assert s['label'] == 'Battery V'
    assert s['unit'] == 'V'
    assert s['color'] == '#facc15'
    assert s['dec'] == 2


def test_cross_sensor_overlay_shares_one_grid(client):
    conn = db.get_connection()
    vic = _sensor(conn, 'house', 'victron')
    cabin = _sensor(conn, 'cabin', 'bme680')
    _vic(conn, vic, '2026-06-20T12:01:00.000Z', 13.1)
    _bme(conn, cabin, '2026-06-20T12:03:00.000Z', 21.5)
    conn.commit()
    conn.close()

    body = client.get(
        '/api/sensors/series',
        query_string={**WINDOW, 'metrics': f'{vic}.battery_voltage,{cabin}.temp_c'},
    ).get_json()

    assert len(body['series']) == 2
    # Both series index into the same x grid; each lands in its own bucket.
    v, t = body['series']
    assert v['values'][1] == 13.1 and v['values'][3] is None
    assert t['values'][3] == 21.5 and t['values'][1] is None
    assert v['values'].__len__() == t['values'].__len__() == len(body['x'])


def test_default_window_when_unset(client):
    conn = db.get_connection()
    sid = _sensor(conn, 'house', 'victron')
    conn.commit()
    conn.close()
    # No start/end → trailing-24h default; still a well-formed empty grid.
    body = client.get(
        '/api/sensors/series', query_string={'metrics': f'{sid}.battery_voltage'}
    ).get_json()
    assert 'x' in body and len(body['series']) == 1


def test_validation_rejects_bad_requests(client):
    conn = db.get_connection()
    sid = _sensor(conn, 'house', 'victron')
    conn.commit()
    conn.close()

    # Missing metrics.
    assert client.get('/api/sensors/series', query_string=WINDOW).status_code == 400
    # Malformed address.
    assert (
        client.get('/api/sensors/series', query_string={**WINDOW, 'metrics': 'nope'}).status_code
        == 400
    )
    # Unknown sensor id.
    assert (
        client.get(
            '/api/sensors/series', query_string={**WINDOW, 'metrics': '999.battery_voltage'}
        ).status_code
        == 400
    )
    # Unknown column for this sensor type.
    assert (
        client.get(
            '/api/sensors/series', query_string={**WINDOW, 'metrics': f'{sid}.rpm'}
        ).status_code
        == 400
    )
    # Inverted window.
    bad = {'start': WINDOW['end'], 'end': WINDOW['start'], 'metrics': f'{sid}.battery_voltage'}
    assert client.get('/api/sensors/series', query_string=bad).status_code == 400
    # Non-positive buckets.
    zero = {**WINDOW, 'buckets': 0, 'metrics': f'{sid}.battery_voltage'}
    assert client.get('/api/sensors/series', query_string=zero).status_code == 400
