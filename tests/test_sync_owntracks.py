"""Pure-helper tests for ``tools/sync_owntracks.py`` (no network, no DB).

Covers the two testable seams of the sync: cursor → Recorder ``from`` param
formatting, and Recorder record → ``owntracks_points`` row mapping.
"""

from __future__ import annotations

from datetime import timedelta

from tools.sync_owntracks import location_row, recorder_from_param


def test_from_param_empty_cursor_pulls_everything():
    assert recorder_from_param(None) == '1970-01-01T00:00:00'


def test_from_param_backs_off_default_slack():
    assert recorder_from_param('2026-08-28T15:03:12.000Z') == '2026-08-28T14:03:12'


def test_from_param_custom_slack():
    frm = recorder_from_param('2026-08-28T15:03:12.500Z', slack=timedelta(0))
    assert frm == '2026-08-28T15:03:12'


def test_location_row_maps_fields():
    rec = {
        '_type': 'location',
        'tst': 1787929392,
        'lat': 39.31,
        'lon': -77.84,
        'acc': 3,
        'alt': 123,
        'vel': 0,
        'batt': 100,
        'tid': 'PM',
    }
    row = location_row(rec, 'paul', 'phone', '2026-08-28T15:10:00.000Z')
    assert row == (
        'paul',
        'phone',
        '2026-08-28T15:03:12.000Z',
        39.31,
        -77.84,
        3,
        123,
        0,
        100,
        '2026-08-28T15:10:00.000Z',
    )


def test_location_row_optional_fields_default_none():
    rec = {'_type': 'location', 'tst': 0, 'lat': 1.0, 'lon': 2.0}
    row = location_row(rec, 'u', 'd', 's')
    assert row == ('u', 'd', '1970-01-01T00:00:00.000Z', 1.0, 2.0, None, None, None, None, 's')


def test_location_row_rejects_non_location():
    assert location_row({'_type': 'lwt', 'tst': 1, 'lat': 1.0, 'lon': 2.0}, 'u', 'd', 's') is None


def test_location_row_rejects_missing_required_fields():
    assert location_row({'_type': 'location', 'lat': 1.0, 'lon': 2.0}, 'u', 'd', 's') is None
    assert (
        location_row({'_type': 'location', 'tst': 1, 'lat': None, 'lon': 2.0}, 'u', 'd', 's')
        is None
    )
