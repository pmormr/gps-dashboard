"""Unit tests for the NPS attractions importer's pure transforms.

Sample records mirror the real API shapes captured in the Phase-0 spike
(string coordinates, event-level ``times``, tour stops referencing ``places``
assets); no network.
"""

from __future__ import annotations

from tools.import_attractions import (
    Attraction,
    apply_park_fallback,
    dedupe_by_source_id,
    parse_ampm,
    parse_event,
    parse_facility,
    parse_park,
    parse_thingstodo,
    parse_tour,
    summarize,
)

_PARK = {
    'id': 'P1',
    'parkCode': 'romo',
    'fullName': 'Rocky Mountain National Park',
    'latitude': '40.3556924',
    'longitude': '-105.6972879',
    'description': 'Mountains.',
    'relevanceScore': 1.0,
    'images': [{'url': 'https://x/img.jpg', 'title': 'T', 'caption': 'C', 'credit': 'drop-me'}],
}


def test_parse_park_coerces_string_coords_and_trims_details() -> None:
    park = parse_park(_PARK)
    assert park.source_kind == 'park'
    assert park.park_code == 'romo'
    assert park.lat == 40.3556924
    assert park.lon == -105.6972879
    assert 'relevanceScore' not in park.details
    assert park.details['images'] == [{'url': 'https://x/img.jpg', 'title': 'T', 'caption': 'C'}]


def test_parse_thingstodo_missing_coords_falls_back_to_park() -> None:
    ttd = parse_thingstodo(
        {
            'id': 'T1',
            'title': 'Wildlife Viewing',
            'latitude': '',
            'longitude': '',
            'shortDescription': 'Look at elk.',
            'relatedParks': [{'parkCode': 'romo'}],
        }
    )
    assert ttd.park_code == 'romo'
    assert ttd.lat is None

    filled = apply_park_fallback([parse_park(_PARK), ttd])
    by_id = {a.source_id: a for a in filled}
    assert by_id['T1'].lat == 40.3556924
    assert by_id['T1'].lon == -105.6972879


def test_parse_tour_resolves_stop_coords_and_pins_first_located_stop() -> None:
    tour = parse_tour(
        {
            'id': 'TR1',
            'title': 'Holzwarth Historic Site Tour',
            'description': 'A walk.',
            'park': {'parkCode': 'romo'},
            'stops': [
                {'assetId': 'A2', 'ordinal': '2', 'significance': 'Second'},
                {'assetId': 'A1', 'ordinal': '1', 'significance': 'First'},
                {'assetId': 'MISSING', 'ordinal': '3', 'significance': 'Third'},
            ],
        },
        places={'A1': (40.1, -105.1), 'A2': (40.2, -105.2)},
    )
    stops = tour.details['stops']
    assert [s['significance'] for s in stops] == ['First', 'Second', 'Third']
    assert (stops[0]['lat'], stops[0]['lon']) == (40.1, -105.1)
    assert stops[2]['lat'] is None
    assert (tour.lat, tour.lon) == (40.1, -105.1)
    assert tour.park_code == 'romo'


def test_parse_facility_keeps_operating_hours_in_details() -> None:
    hours = [{'standardHours': {'monday': 'All Day'}, 'description': 'Summer season.'}]
    facility = parse_facility(
        'campground',
        {
            'id': 'C1',
            'parkCode': 'romo',
            'name': 'Aspenglen Campground',
            'latitude': '40.399',
            'longitude': '-105.593',
            'description': 'A campground.',
            'operatingHours': hours,
        },
    )
    assert facility.source_kind == 'campground'
    assert facility.details['operatingHours'] == hours


def test_parse_event_expands_dates_times_and_coerces_string_bools() -> None:
    event = parse_event(
        {
            'id': 'E1',
            'title': 'Bird Walk',
            'sitecode': 'romo',
            'latitude': '40.4',
            'longitude': '-105.5',
            'location': 'West Alluvial Fan Parking',
            'isfree': 'true',
            'isregresrequired': 'false',
            'dates': ['2026-07-04', '2026-07-08'],
            'times': [{'timestart': '07:30 AM', 'timeend': '09:00 AM'}],
            'description': '<p>Bring <b>binoculars</b>.</p>',
        }
    )
    assert event.is_free is True
    assert event.needs_reservation is False
    assert [(d.date, d.time_start, d.time_end) for d in event.dates] == [
        ('2026-07-04', '07:30', '09:00'),
        ('2026-07-08', '07:30', '09:00'),
    ]


def test_parse_event_without_times_yields_null_time_occurrences() -> None:
    event = parse_event(
        {'id': 'E2', 'title': 'All Day Thing', 'dates': ['2026-07-04'], 'times': []}
    )
    assert [(d.date, d.time_start, d.time_end) for d in event.dates] == [('2026-07-04', None, None)]


def test_parse_event_falls_back_to_datestart_when_dates_empty() -> None:
    event = parse_event({'id': 'E3', 'title': 'One Off', 'datestart': '2026-08-01', 'dates': []})
    assert [d.date for d in event.dates] == ['2026-08-01']


def test_parse_ampm() -> None:
    assert parse_ampm('07:30 AM') == '07:30'
    assert parse_ampm('09:00 PM') == '21:00'
    assert parse_ampm('') is None
    assert parse_ampm('sunset') is None


def test_summarize_strips_tags_and_truncates_on_word_boundary() -> None:
    assert summarize('<p>Bring <b>binoculars</b>.</p>') == 'Bring binoculars.'
    long = summarize('word ' * 100, limit=20)
    assert long is not None
    assert long.endswith('…')
    assert len(long) <= 21
    assert summarize('') is None
    assert summarize(None) is None


def test_dedupe_by_source_id_keeps_first() -> None:
    a = Attraction('park', 'X', None, 'A', None, None, None, {})
    b = Attraction('park', 'X', None, 'B', None, None, None, {})
    c = Attraction('park', 'Y', None, 'C', None, None, None, {})
    assert [r.name for r in dedupe_by_source_id([a, b, c])] == ['A', 'C']
