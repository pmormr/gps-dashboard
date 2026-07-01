"""Flask-client tests for the phone-history read API (``/api/phone/*``).

Seeds an isolated temp DB (the ``client`` fixture) through the importer's own
:func:`tools.import_phone_timeline.load_timeline`, then exercises the time/bbox
overlap filters, the endpoints-always decimation, and param validation.
"""

from __future__ import annotations

import pytest

from api.db import get_connection
from tools.import_phone_timeline import (
    Activity,
    PhonePath,
    Timeline,
    TrackPoint,
    Visit,
    load_timeline,
)


def _seed(timeline: Timeline) -> None:
    """Full-replace the phone tier of the client's temp DB with ``timeline``."""
    conn = get_connection()
    load_timeline(conn, timeline)
    conn.close()


def _path(start: str, end: str, points: list[TrackPoint]) -> PhonePath:
    lats = [p.lat for p in points]
    lons = [p.lon for p in points]
    return PhonePath(start, end, points, (min(lats), min(lons), max(lats), max(lons)))


# Two paths in different places and years: A in 2020 near (40, -77), B in 2021
# near (30, -90).
_PATH_A = _path(
    '2020-01-01T00:00:00.000Z',
    '2020-01-01T01:00:00.000Z',
    [
        TrackPoint('2020-01-01T00:00:00.000Z', 40.0, -77.0, 0.0),
        TrackPoint('2020-01-01T00:30:00.000Z', 40.5, -77.5, 12.0),
        TrackPoint('2020-01-01T01:00:00.000Z', 41.0, -78.0, 0.0),
    ],
)
_PATH_B = _path(
    '2021-06-01T00:00:00.000Z',
    '2021-06-01T00:30:00.000Z',
    [
        TrackPoint('2021-06-01T00:00:00.000Z', 30.0, -90.0, 0.0),
        TrackPoint('2021-06-01T00:30:00.000Z', 30.5, -90.5, 0.0),
    ],
)
_VISIT = Visit(
    '2020-01-01T00:05:00.000Z', '2020-01-01T00:20:00.000Z', 40.2, -77.2, 'p1', 'HOME', 0.9
)
_ACTIVITY = Activity(
    '2020-01-01T00:20:00.000Z',
    '2020-01-01T00:30:00.000Z',
    40.2,
    -77.2,
    40.5,
    -77.5,
    4400.0,
    'WALKING',
    0.8,
)


# --- /api/phone/tracks ---------------------------------------------------------


def test_tracks_no_filter_returns_all_with_points(client) -> None:
    _seed(Timeline([_PATH_A, _PATH_B], [], []))
    body = client.get('/api/phone/tracks').get_json()
    assert body['count'] == 2
    assert body['truncated'] is False
    by_id = {p['start_time']: p for p in body['paths']}
    assert len(by_id['2020-01-01T00:00:00.000Z']['points']) == 3
    # Embedded points come back in time order.
    times = [pt['timestamp'] for pt in by_id['2020-01-01T00:00:00.000Z']['points']]
    assert times == sorted(times)


def test_tracks_time_window_overlap(client) -> None:
    _seed(Timeline([_PATH_A, _PATH_B], [], []))
    body = client.get(
        '/api/phone/tracks?start=2020-01-01T00:00:00Z&end=2020-02-01T00:00:00Z'
    ).get_json()
    assert body['count'] == 1
    assert body['paths'][0]['start_time'] == '2020-01-01T00:00:00.000Z'


def test_tracks_bbox_filter(client) -> None:
    _seed(Timeline([_PATH_A, _PATH_B], [], []))
    # A box around path B only.
    body = client.get('/api/phone/tracks?bbox=-91,29,-89,31').get_json()
    assert body['count'] == 1
    assert body['paths'][0]['start_time'] == '2021-06-01T00:00:00.000Z'


def test_tracks_decimation_keeps_endpoints(client) -> None:
    # One path: 2 endpoints (importance 0) + 3 interior vertices of rising import.
    interior = [
        TrackPoint(f'2022-01-01T00:{m:02d}:00.000Z', 50.0 + m, -100.0, float(m)) for m in (1, 2, 3)
    ]
    path = _path(
        '2022-01-01T00:00:00.000Z',
        '2022-01-01T00:04:00.000Z',
        [
            TrackPoint('2022-01-01T00:00:00.000Z', 50.0, -100.0, 0.0),
            *interior,
            TrackPoint('2022-01-01T00:04:00.000Z', 54.0, -100.0, 0.0),
        ],
    )
    _seed(Timeline([path], [], []))

    # limit=3 → 2 endpoints kept, budget 1 interior (the highest importance = 3.0).
    body = client.get('/api/phone/tracks?limit=3').get_json()
    assert body['truncated'] is True
    points = body['paths'][0]['points']
    kept_importances = sorted(pt['importance'] for pt in points)
    assert kept_importances == [0.0, 0.0, 3.0]


# --- /api/phone/places ---------------------------------------------------------


def test_places_returns_visits_and_activities(client) -> None:
    _seed(Timeline([], [_VISIT], [_ACTIVITY]))
    body = client.get('/api/phone/places').get_json()
    assert body['count'] == 2
    assert body['visits'][0]['place_id'] == 'p1'
    assert body['visits'][0]['semantic_type'] == 'HOME'
    assert body['activities'][0]['activity_type'] == 'WALKING'
    assert body['activities'][0]['distance_m'] == pytest.approx(4400.0)


def test_places_time_window_excludes_outside(client) -> None:
    _seed(Timeline([], [_VISIT], [_ACTIVITY]))
    body = client.get(
        '/api/phone/places?start=2019-01-01T00:00:00Z&end=2019-12-31T00:00:00Z'
    ).get_json()
    assert body['count'] == 0


def test_places_bbox_filter(client) -> None:
    _seed(Timeline([], [_VISIT], [_ACTIVITY]))
    # Box far from the seeded (40.2, -77.2) place → nothing.
    body = client.get('/api/phone/places?bbox=0,0,1,1').get_json()
    assert body['count'] == 0


# --- validation ----------------------------------------------------------------


def test_tracks_bad_bbox_400(client) -> None:
    assert client.get('/api/phone/tracks?bbox=1,2,3').status_code == 400


def test_tracks_bad_time_400(client) -> None:
    assert client.get('/api/phone/tracks?start=not-a-time').status_code == 400


def test_places_bad_limit_400(client) -> None:
    assert client.get('/api/phone/places?limit=0').status_code == 400
