"""Tests for the size-aware decimation in GET /api/points (C17).

Seeds the processed ``track_points`` tier directly, then drives the real route
through the Flask test client to pin the contract: every overlapping stop
survives regardless of ``limit``, the remaining budget is filled by
highest-``importance`` moving vertices, ``truncated`` reflects only dropped
moving vertices, and the bbox / time-window / validation filters behave.
"""

import api.db as db

WINDOW = {'start': '2026-06-20T12:00:00Z', 'end': '2026-06-20T13:00:00Z'}


def _ts(hh, mm):
    return f'2026-06-20T{hh:02d}:{mm:02d}:00.000Z'


def _insert(
    client,
    *,
    kind,
    timestamp=None,
    importance=0.0,
    lat=40.0,
    lon=-105.0,
    dwell_start=None,
    dwell_end=None,
    n_raw=1,
    src_raw_id=0,
):
    """Insert one track_points row into the test database."""
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO track_points '
        '(timestamp, lat, lon, kind, n_raw, importance, dwell_start, dwell_end, src_raw_id) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (timestamp, lat, lon, kind, n_raw, importance, dwell_start, dwell_end, src_raw_id),
    )
    conn.commit()
    conn.close()


def _get(client, **overrides):
    params = {**WINDOW, **overrides}
    return client.get('/api/points', query_string=params)


def test_stops_always_kept_even_below_limit(client):
    for i in range(3):
        _insert(
            client,
            kind='stop',
            timestamp=_ts(12, 10 + i),
            dwell_start=_ts(12, 10 + i),
            dwell_end=_ts(12, 20 + i),
        )
    for i in range(5):
        _insert(client, kind='track', timestamp=_ts(12, 30 + i), importance=float(i + 1))

    resp = _get(client, limit=1)
    body = resp.get_json()
    assert resp.status_code == 200
    assert body['count'] == 3
    assert all(p['kind'] == 'stop' for p in body['points'])
    assert body['truncated'] is True


def test_importance_fill_keeps_highest_then_reports_truncated(client):
    for i in range(5):
        _insert(client, kind='track', timestamp=_ts(12, 10 + i), importance=float(i + 1))

    body = _get(client, limit=3).get_json()
    assert body['count'] == 3
    assert {p['importance'] for p in body['points']} == {5.0, 4.0, 3.0}
    assert body['truncated'] is True


def test_not_truncated_when_budget_unfilled(client):
    _insert(client, kind='track', timestamp=_ts(12, 10), importance=2.0)
    _insert(client, kind='track', timestamp=_ts(12, 20), importance=1.0)

    body = _get(client, limit=5000).get_json()
    assert body['count'] == 2
    assert body['truncated'] is False


def test_points_returned_sorted_by_timestamp(client):
    _insert(client, kind='track', timestamp=_ts(12, 50), importance=1.0)
    _insert(client, kind='track', timestamp=_ts(12, 10), importance=2.0)
    _insert(client, kind='track', timestamp=_ts(12, 30), importance=3.0)

    points = _get(client).get_json()['points']
    assert [p['timestamp'] for p in points] == sorted(p['timestamp'] for p in points)


def test_bbox_filters_moving_vertices(client):
    _insert(client, kind='track', timestamp=_ts(12, 10), importance=1.0, lat=40.0, lon=-105.0)
    _insert(client, kind='track', timestamp=_ts(12, 20), importance=1.0, lat=50.0, lon=-105.0)

    body = _get(client, bbox='-106,39,-104,41').get_json()
    assert body['count'] == 1
    assert body['points'][0]['lat'] == 40.0


def test_stop_matched_on_interval_overlap_not_just_start(client):
    # A stop whose dwell straddles the window start must be kept; one entirely
    # before the window must not. (The response omits dwell_* columns, so the
    # distinct lat identifies which stop came back.)
    _insert(
        client,
        kind='stop',
        timestamp=_ts(11, 30),
        lat=40.0,
        dwell_start=_ts(11, 30),
        dwell_end=_ts(12, 30),
    )
    _insert(
        client,
        kind='stop',
        timestamp=_ts(8, 0),
        lat=41.0,
        dwell_start=_ts(8, 0),
        dwell_end=_ts(9, 0),
    )

    body = _get(client).get_json()
    assert body['count'] == 1
    assert body['points'][0]['lat'] == 40.0


def test_moving_vertex_outside_window_excluded(client):
    _insert(client, kind='track', timestamp=_ts(12, 30), importance=1.0)
    _insert(client, kind='track', timestamp=_ts(14, 0), importance=1.0)

    assert _get(client).get_json()['count'] == 1


def test_missing_start_end_is_400(client):
    assert client.get('/api/points').status_code == 400
    assert client.get('/api/points', query_string={'start': WINDOW['start']}).status_code == 400


def test_invalid_timestamp_is_400(client):
    assert _get(client, start='not-a-time').status_code == 400


def test_invalid_bbox_is_400(client):
    assert _get(client, bbox='1,2,3').status_code == 400


def test_invalid_limit_is_400(client):
    assert _get(client, limit='abc').status_code == 400


def test_latest_point_404_when_empty(client):
    assert client.get('/api/points/latest').status_code == 404


def test_latest_point_returns_most_recent_raw_fix(client):
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO gps_points (timestamp, lat, lon) VALUES (?, ?, ?)',
        (_ts(12, 10), 40.0, -105.0),
    )
    conn.execute(
        'INSERT INTO gps_points (timestamp, lat, lon) VALUES (?, ?, ?)',
        (_ts(12, 40), 41.0, -106.0),
    )
    conn.commit()
    conn.close()

    body = client.get('/api/points/latest').get_json()
    assert body['timestamp'] == _ts(12, 40)
    assert body['lat'] == 41.0
