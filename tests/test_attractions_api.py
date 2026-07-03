"""Flask-client tests for the attractions read API (``/api/attractions*``).

Seeds the isolated temp DB (the ``client`` fixture) through the importer's own
:func:`tools.import_attractions.load`, then exercises the bbox/kind/park/name
filters, the occurrence-window grouping, the detail endpoints, and param
validation.
"""

from __future__ import annotations

from api.db import get_connection
from tools.import_attractions import Attraction, Event, EventDate, load

_PARK = Attraction(
    'park', 'P1', 'romo', 'Rocky Mountain National Park', 40.36, -105.70, 'Mountains.', {'x': 1}
)
_TOUR = Attraction(
    'tour',
    'TR1',
    'romo',
    'Holzwarth Historic Site Tour',
    40.42,
    -105.85,
    'A walk.',
    {'stops': [{'significance': 'First', 'lat': 40.42, 'lon': -105.85}]},
)
_CAMP = Attraction(
    'campground', 'C1', 'yell', 'Madison Campground', 44.65, -110.86, 'A campground.', {}
)
_NOCOORD = Attraction('thingstodo', 'T1', 'romo', 'Stargazing', None, None, 'Look up.', {})

_EVENT_A = Event(
    'E1',
    'romo',
    'Bird Walk',
    40.4,
    -105.5,
    'West Alluvial Fan Parking',
    True,
    False,
    (
        EventDate('2026-07-04', '07:30', '09:00'),
        EventDate('2026-07-08', '07:30', '09:00'),
    ),
    {'description': 'Bring binoculars.'},
)
_EVENT_B = Event(
    'E2',
    'yell',
    'Geyser Talk',
    44.46,
    -110.83,
    None,
    None,
    None,
    (EventDate('2026-07-05', None, None),),
    {},
)


def _seed() -> None:
    """Full-replace the attractions tier of the client's temp DB."""
    conn = get_connection()
    load(conn, [_PARK, _TOUR, _CAMP, _NOCOORD], [_EVENT_A, _EVENT_B])
    conn.close()


# --- /api/attractions ------------------------------------------------------------


def test_list_all(client) -> None:
    _seed()
    body = client.get('/api/attractions').get_json()
    assert body['count'] == 4
    assert body['truncated'] is False
    assert body['attractions'][0]['synced_at']


def test_list_bbox_excludes_nocoord_and_out_of_box(client) -> None:
    _seed()
    body = client.get('/api/attractions?bbox=-106,40,-105,41').get_json()
    names = {a['name'] for a in body['attractions']}
    assert names == {'Rocky Mountain National Park', 'Holzwarth Historic Site Tour'}


def test_list_kind_and_park_filters(client) -> None:
    _seed()
    body = client.get('/api/attractions?kind=tour,campground').get_json()
    assert {a['source_kind'] for a in body['attractions']} == {'tour', 'campground'}
    body = client.get('/api/attractions?park=romo').get_json()
    assert body['count'] == 3


def test_list_name_search(client) -> None:
    _seed()
    body = client.get('/api/attractions?q=holzwarth').get_json()
    assert [a['name'] for a in body['attractions']] == ['Holzwarth Historic Site Tour']


def test_list_rejects_bad_bbox_and_limit(client) -> None:
    assert client.get('/api/attractions?bbox=1,2,3').status_code == 400
    assert client.get('/api/attractions?limit=0').status_code == 400


def test_detail_includes_parsed_details(client) -> None:
    _seed()
    listed = client.get('/api/attractions?q=holzwarth').get_json()['attractions'][0]
    body = client.get(f'/api/attractions/{listed["id"]}').get_json()
    assert body['details']['stops'][0]['significance'] == 'First'


def test_detail_404(client) -> None:
    _seed()
    assert client.get('/api/attractions/99999').status_code == 404


# --- /api/attractions/events -------------------------------------------------------


def test_events_window_groups_occurrences_per_event(client) -> None:
    _seed()
    body = client.get('/api/attractions/events?start=2026-07-04&end=2026-07-05').get_json()
    by_name = {e['name']: e for e in body['events']}
    assert set(by_name) == {'Bird Walk', 'Geyser Talk'}
    assert by_name['Bird Walk']['dates'] == [
        {'date': '2026-07-04', 'time_start': '07:30', 'time_end': '09:00'}
    ]
    assert by_name['Bird Walk']['is_free'] == 1
    assert by_name['Geyser Talk']['dates'][0]['time_start'] is None


def test_events_park_and_bbox_filters(client) -> None:
    _seed()
    body = client.get('/api/attractions/events?park=yell').get_json()
    assert [e['name'] for e in body['events']] == ['Geyser Talk']
    body = client.get('/api/attractions/events?bbox=-106,40,-105,41').get_json()
    assert [e['name'] for e in body['events']] == ['Bird Walk']


def test_events_rejects_bad_date(client) -> None:
    assert client.get('/api/attractions/events?start=07/04/2026').status_code == 400


def test_event_detail_full_occurrences_and_404(client) -> None:
    _seed()
    listed = client.get('/api/attractions/events?park=romo').get_json()['events'][0]
    body = client.get(f'/api/attractions/events/{listed["id"]}').get_json()
    assert body['details'] == {'description': 'Bring binoculars.'}
    assert [d['date'] for d in body['dates']] == ['2026-07-04', '2026-07-08']
    assert client.get('/api/attractions/events/99999').status_code == 404


# --- import idempotency --------------------------------------------------------------


def test_load_is_full_replace(client) -> None:
    _seed()
    _seed()
    body = client.get('/api/attractions').get_json()
    assert body['count'] == 4
    events = client.get('/api/attractions/events').get_json()
    assert events['count'] == 2
