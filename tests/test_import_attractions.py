"""Unit tests for the attractions importer's pure transforms (NPS + RIDB).

Sample records mirror the real source shapes: NPS API records as captured in
the Phase-0 spike (string coordinates, event-level ``times``, tour stops
referencing ``places`` assets), RIDB CSV rows as in the full export (all-string
fields, ``0.0`` null-island coordinates, ALL-CAPS labels); no network, no zip.
"""

from __future__ import annotations

from tools.import_attractions import (
    Attraction,
    RidbExport,
    _ridb_label,
    apply_park_fallback,
    build_ridb_attractions,
    dedupe_by_source_id,
    parse_ampm,
    parse_event,
    parse_facility,
    parse_park,
    parse_ridb_facility,
    parse_ridb_recarea,
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
    'activities': [
        {'id': 'A2', 'name': 'Hiking'},
        {'id': 'A1', 'name': 'Camping'},
        {'id': 'A3'},
    ],
}


def test_parse_park_coerces_string_coords_and_trims_details() -> None:
    park = parse_park(_PARK)
    assert park.source_kind == 'park'
    assert park.park_code == 'romo'
    assert park.lat == 40.3556924
    assert park.lon == -105.6972879
    assert 'relevanceScore' not in park.details
    assert park.details['images'] == [{'url': 'https://x/img.jpg', 'title': 'T', 'caption': 'C'}]
    assert park.details['activities'] == ['Camping', 'Hiking']


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


# --- RIDB ---------------------------------------------------------------------------


def _ridb_export() -> RidbExport:
    """A minimal joined export: one FS rec area with three facilities, one NPS pair."""
    return RidbExport(
        recareas=[
            {
                'RecAreaID': '1000',
                'RecAreaName': 'Stanislaus National Forest',
                'RecAreaDescription': '<p>Big trees.</p>',
                'RecAreaLatitude': '38.2',
                'RecAreaLongitude': '-120.0',
            },
            {
                'RecAreaID': '2000',
                'RecAreaName': 'Rocky Mountain National Park',
                'RecAreaLatitude': '40.3',
                'RecAreaLongitude': '-105.7',
            },
        ],
        facilities=[
            {
                'FacilityID': '10',
                'FacilityName': 'West Shore Campground',
                'FacilityDescription': 'Lakeside sites.',
                'FacilityTypeDescription': 'Campground',
                'FacilityLatitude': '38.25',
                'FacilityLongitude': '-120.05',
                'Reservable': 'true',
            },
            {
                'FacilityID': '11',
                'FacilityName': 'CHALK CREEK TRAILHEAD',
                'FacilityDescription': '',
                'FacilityTypeDescription': 'Facility',
                'FacilityLatitude': '0.000000',
                'FacilityLongitude': '0.000000',
            },
            {
                'FacilityID': '12',
                'FacilityName': 'Forest Day Pass',
                'FacilityTypeDescription': 'Activity Pass',
                'FacilityLatitude': '',
                'FacilityLongitude': '',
            },
            {
                'FacilityID': '13',
                'FacilityName': 'Wilderness Permit',
                'FacilityTypeDescription': 'Permit',
                'FacilityLatitude': '',
                'FacilityLongitude': '',
            },
            {
                'FacilityID': '20',
                'FacilityName': 'Moraine Park Campground',
                'FacilityTypeDescription': 'Campground',
                'FacilityLatitude': '40.36',
                'FacilityLongitude': '-105.6',
            },
            {
                'FacilityID': '14',
                'FacilityName': '   ',
                'FacilityTypeDescription': 'Campground',
                'FacilityLatitude': '38.3',
                'FacilityLongitude': '-120.1',
            },
        ],
        recarea_orgs={'1000': '131', '2000': '128'},
        facility_orgs={'10': '131', '11': '131', '12': '131', '13': '131', '20': '128'},
        org_names={'131': 'FS', '128': 'NPS'},
        facility_recarea={'10': '1000', '11': '1000', '13': '1000', '20': '2000'},
        facility_address={'10': ('Bear Valley', 'CA')},
        recarea_activities={'1000': ['Camping', 'Hiking']},
        facility_activities={'10': ['Camping']},
        campsites={'10': {'count': 25, 'equipment': {'RV': 35.0, 'Tent': 0.0}}},
    )


def test_ridb_label_title_cases_but_keeps_rv() -> None:
    assert _ridb_label('PICKUP CAMPER') == 'Pickup Camper'
    assert _ridb_label('RV/MOTORHOME') == 'RV/Motorhome'
    assert _ridb_label('Tent') == 'Tent'


def test_parse_ridb_recarea_builds_container_row() -> None:
    export = _ridb_export()
    area = parse_ridb_recarea(export.recareas[0], export)
    assert area.source_kind == 'recarea'
    assert area.source_id == 'ra:1000'
    assert area.park_code == '1000'
    assert (area.lat, area.lon) == (38.2, -120.0)
    assert area.summary == 'Big trees.'
    assert area.details['org'] == 'FS'
    assert area.details['activities'] == ['Camping', 'Hiking']
    assert 'phone' not in area.details


def test_parse_ridb_facility_joins_org_address_activities_campsites() -> None:
    export = _ridb_export()
    campground = parse_ridb_facility(
        export.facilities[0], export, {'1000': 'Stanislaus National Forest'}
    )
    assert campground.source_kind == 'campground'
    assert campground.source_id == 'fac:10'
    assert campground.park_code == '1000'
    assert campground.details['org'] == 'FS'
    assert campground.details['recAreaName'] == 'Stanislaus National Forest'
    assert (campground.details['city'], campground.details['state']) == ('Bear Valley', 'CA')
    assert campground.details['reservable'] is True
    assert campground.details['campsites'] == {
        'count': 25,
        'equipment': [{'name': 'RV', 'maxLengthFt': 35.0}, {'name': 'Tent'}],
    }


def test_build_ridb_attractions_excludes_nps_and_products_and_backfills_coords() -> None:
    rows = build_ridb_attractions(_ridb_export())
    by_id = {a.source_id: a for a in rows}

    assert 'ra:2000' not in by_id  # NPS rec area excluded
    assert 'fac:20' not in by_id  # NPS facility excluded
    assert 'fac:12' not in by_id  # Activity Pass (reservation product) excluded
    assert 'fac:14' not in by_id  # nameless source junk excluded
    assert by_id['fac:13'].source_kind == 'permit'  # Permit kept as a planning signal

    trailhead = by_id['fac:11']
    assert trailhead.source_kind == 'facility'
    assert (trailhead.lat, trailhead.lon) == (38.2, -120.0)  # 0.0 → rec-area fallback

    permit = by_id['fac:13']
    assert (permit.lat, permit.lon) == (38.2, -120.0)
