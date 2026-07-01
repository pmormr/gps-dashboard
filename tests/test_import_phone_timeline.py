"""Tests for the Google Timeline history importer (``tools/import_phone_timeline``).

Covers the pure parse layer (coordinate/timestamp normalization, per-kind
dispatch, malformed-segment skipping, per-segment Reumann–Witkam thinning) and the
full-replace load against an in-memory DB.
"""

from __future__ import annotations

import sqlite3

import pytest

from api.db import init_db
from tools.import_phone_timeline import (
    load_timeline,
    parse_latlng,
    parse_timeline,
)


def _path_seg(points: list[tuple[str, str]]) -> dict:
    """Build a ``timelinePath`` segment from ``(point, time)`` string pairs."""
    return {
        'startTime': points[0][1],
        'endTime': points[-1][1],
        'timelinePath': [{'point': p, 'time': t} for p, t in points],
    }


def _visit_seg() -> dict:
    return {
        'startTime': '2011-04-16T10:08:00.000-04:00',
        'endTime': '2011-04-16T11:09:47.000-04:00',
        'visit': {
            'probability': 0.74,
            'topCandidate': {
                'placeId': 'ChIJabc',
                'semanticType': 'HOME',
                'probability': 0.2,
                'placeLocation': {'latLng': '40.798°, -77.859°'},
            },
        },
    }


def _activity_seg() -> dict:
    return {
        'startTime': '2015-07-29T09:08:01.000-04:00',
        'endTime': '2015-07-29T09:40:01.000-04:00',
        'activity': {
            'start': {'latLng': '40.790°, -77.850°'},
            'end': {'latLng': '40.800°, -77.840°'},
            'distanceMeters': 4403.0,
            'topCandidate': {'type': 'WALKING', 'probability': 0.9},
        },
    }


# --- parse_latlng --------------------------------------------------------------


def test_parse_latlng_basic() -> None:
    assert parse_latlng('40.793251°, -77.859949°') == (40.793251, -77.859949)


def test_parse_latlng_no_degree_symbol_and_spacing() -> None:
    assert parse_latlng('  -12.5 , 34.0 ') == (-12.5, 34.0)


def test_parse_latlng_bad_raises() -> None:
    with pytest.raises(ValueError):
        parse_latlng('not a coordinate')


# --- parse_timeline dispatch + normalization -----------------------------------


def test_dispatch_by_kind_ignores_other_segments() -> None:
    data = {
        'semanticSegments': [
            _path_seg([('40.0°, -77.0°', '2020-01-01T00:00:00.000Z')]),
            _visit_seg(),
            _activity_seg(),
            {'startTime': 'x', 'endTime': 'y', 'timelineMemory': {}},  # ignored kind
        ],
        'rawSignals': [{'position': {'LatLng': '1°, 2°'}}],  # ignored entirely
    }
    timeline = parse_timeline(data, epsilon=20.0)
    assert len(timeline.paths) == 1
    assert len(timeline.visits) == 1
    assert len(timeline.activities) == 1


def test_timestamps_normalized_to_utc() -> None:
    timeline = parse_timeline({'semanticSegments': [_visit_seg()]}, epsilon=20.0)
    visit = timeline.visits[0]
    # 10:08 at -04:00 → 14:08 UTC, fixed-width millisecond Z form.
    assert visit.start_time == '2011-04-16T14:08:00.000Z'
    assert visit.place_id == 'ChIJabc'
    assert visit.semantic_type == 'HOME'
    assert visit.probability == pytest.approx(0.74)  # visit-level, not topCandidate


def test_activity_fields() -> None:
    timeline = parse_timeline({'semanticSegments': [_activity_seg()]}, epsilon=20.0)
    act = timeline.activities[0]
    assert act.distance_m == pytest.approx(4403.0)
    assert act.activity_type == 'WALKING'
    assert (act.start_lat, act.start_lon) == (40.790, -77.850)
    assert (act.end_lat, act.end_lon) == (40.800, -77.840)


# --- thinning ------------------------------------------------------------------


def test_collinear_path_thinned_to_endpoints() -> None:
    seg = _path_seg(
        [
            ('40.0°, -77.000°', '2020-01-01T00:00:00.000Z'),
            ('40.0°, -77.001°', '2020-01-01T00:01:00.000Z'),  # collinear → dropped
            ('40.0°, -77.002°', '2020-01-01T00:02:00.000Z'),
        ]
    )
    path = parse_timeline({'semanticSegments': [seg]}, epsilon=20.0).paths[0]
    assert [(p.lon, p.importance) for p in path.points] == [(-77.000, 0.0), (-77.002, 0.0)]
    assert path.start_time == '2020-01-01T00:00:00.000Z'
    assert path.end_time == '2020-01-01T00:02:00.000Z'
    assert path.bbox == (40.0, -77.002, 40.0, -77.000)


def test_breadcrumb_points_tagged_with_activity_mode() -> None:
    data = {
        'semanticSegments': [
            _path_seg(
                [
                    ('40.000°, -77.000°', '2020-01-01T00:00:00.000Z'),  # in interval
                    ('40.010°, -77.002°', '2020-01-01T00:05:00.000Z'),  # in interval, off-line
                    ('40.000°, -77.004°', '2020-01-01T02:00:00.000Z'),  # after interval → None
                ]
            ),
            {
                'startTime': '2020-01-01T00:00:00.000Z',
                'endTime': '2020-01-01T00:10:00.000Z',
                'activity': {
                    'start': {'latLng': '40.0°, -77.0°'},
                    'end': {'latLng': '40.0°, -77.002°'},
                    'topCandidate': {'type': 'IN_PASSENGER_VEHICLE', 'probability': 0.9},
                },
            },
        ]
    }
    path = parse_timeline(data, epsilon=20.0).paths[0]
    modes = {p.timestamp: p.activity_type for p in path.points}
    assert modes['2020-01-01T00:00:00.000Z'] == 'IN_PASSENGER_VEHICLE'
    assert modes['2020-01-01T00:05:00.000Z'] == 'IN_PASSENGER_VEHICLE'
    assert modes['2020-01-01T02:00:00.000Z'] is None


def test_points_sorted_before_thinning() -> None:
    seg = _path_seg(
        [
            ('40.0°, -77.002°', '2020-01-01T00:02:00.000Z'),
            ('40.0°, -77.000°', '2020-01-01T00:00:00.000Z'),
        ]
    )
    path = parse_timeline({'semanticSegments': [seg]}, epsilon=20.0).paths[0]
    assert path.points[0].timestamp < path.points[1].timestamp


# --- malformed segments are skipped, not fatal ---------------------------------


def test_malformed_segments_skipped() -> None:
    data = {
        'semanticSegments': [
            _path_seg([('garbage', '2020-01-01T00:00:00.000Z')]),  # bad coord → empty → dropped
            {'visit': {'topCandidate': {}}},  # no placeLocation → dropped
            {'activity': {'start': {'latLng': '1°, 2°'}}},  # no end → dropped
        ]
    }
    timeline = parse_timeline(data, epsilon=20.0)
    assert timeline.paths == []
    assert timeline.visits == []
    assert timeline.activities == []


# --- load + full replace -------------------------------------------------------


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        for table in ('phone_paths', 'phone_track_points', 'phone_visits', 'phone_activities')
    }


def test_load_writes_rows_and_replaces() -> None:
    conn = sqlite3.connect(':memory:')
    init_db(conn)

    first = parse_timeline(
        {
            'semanticSegments': [
                _path_seg(
                    [
                        ('40.0°, -77.000°', '2020-01-01T00:00:00.000Z'),
                        ('41.0°, -76.000°', '2020-01-01T01:00:00.000Z'),
                    ]
                ),
                _visit_seg(),
                _activity_seg(),
            ]
        },
        epsilon=20.0,
    )
    load_timeline(conn, first)
    assert _counts(conn) == {
        'phone_paths': 1,
        'phone_track_points': 2,
        'phone_visits': 1,
        'phone_activities': 1,
    }

    # Re-loading a smaller timeline fully replaces — no accumulation.
    second = parse_timeline({'semanticSegments': [_visit_seg()]}, epsilon=20.0)
    load_timeline(conn, second)
    assert _counts(conn) == {
        'phone_paths': 0,
        'phone_track_points': 0,
        'phone_visits': 1,
        'phone_activities': 0,
    }
