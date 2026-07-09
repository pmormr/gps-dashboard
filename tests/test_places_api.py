"""Flask-client tests for the places read API (``/api/places*``).

Seeds the isolated temp DB (the ``client`` fixture) through the importer's own
:func:`tools.import_places.load`, then exercises the bbox/kind/category/rank/
search filters, FTS prefix semantics, the OSM merge, the occurrence-window
grouping, the detail endpoints, param validation, and the sidecar migrations.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from api.db import get_connection
from tools.import_places import Event, EventDate, Place, load, merge_osm

_PARK = Place(
    'park', 'P1', 'romo', 'Rocky Mountain National Park', 40.36, -105.70, 'Mountains.', {'x': 1}
)
_TOUR = Place(
    'tour',
    'TR1',
    'romo',
    'Holzwarth Historic Site Tour',
    40.42,
    -105.85,
    'A walk.',
    {'stops': [{'significance': 'First', 'lat': 40.42, 'lon': -105.85}]},
)
_CAMP = Place('campground', 'C1', 'yell', 'Madison Campground', 44.65, -110.86, 'A campground.', {})
_NOCOORD = Place('thingstodo', 'T1', 'romo', 'Stargazing', None, None, 'Look up.', {})

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
    """Full-replace the places tier of the client's temp DB."""
    conn = get_connection()
    load(conn, [_PARK, _TOUR, _CAMP, _NOCOORD], [_EVENT_A, _EVENT_B])
    conn.close()


# --- /api/places ------------------------------------------------------------


def test_list_all(client) -> None:
    _seed()
    body = client.get('/api/places').get_json()
    assert body['count'] == 4
    assert body['truncated'] is False
    assert body['places'][0]['synced_at']


def test_list_bbox_excludes_nocoord_and_out_of_box(client) -> None:
    _seed()
    body = client.get('/api/places?bbox=-106,40,-105,41').get_json()
    names = {a['name'] for a in body['places']}
    assert names == {'Rocky Mountain National Park', 'Holzwarth Historic Site Tour'}


def test_list_kind_and_park_filters(client) -> None:
    _seed()
    body = client.get('/api/places?kind=tour,campground').get_json()
    assert {a['source_kind'] for a in body['places']} == {'tour', 'campground'}
    body = client.get('/api/places?park=romo').get_json()
    assert body['count'] == 3


def test_list_name_search(client) -> None:
    _seed()
    body = client.get('/api/places?q=holzwarth').get_json()
    assert [a['name'] for a in body['places']] == ['Holzwarth Historic Site Tour']


def test_search_is_token_prefix(client) -> None:
    """FTS5 contract: 'holz' (prefix) matches, 'warth' (mid-token) does not."""
    _seed()
    assert client.get('/api/places?q=holz').get_json()['count'] == 1
    assert client.get('/api/places?q=warth').get_json()['count'] == 0


def test_search_matches_summary_text(client) -> None:
    _seed()
    body = client.get('/api/places?q=mountains').get_json()
    assert [a['name'] for a in body['places']] == ['Rocky Mountain National Park']


def test_search_operator_text_falls_back_cleanly(client) -> None:
    """Quote/star-only input has no searchable token — LIKE fallback, no 500."""
    _seed()
    assert client.get('/api/places?q=%22*%22').get_json()['count'] == 0
    body = client.get('/api/places?q=NOT holzwarth').get_json()
    assert body['count'] == 0  # tokens 'NOT'+'holzwarth' AND together: no match


def test_category_and_rank_stamped_from_kind_map(client) -> None:
    _seed()
    body = client.get('/api/places?category=park').get_json()
    assert [a['name'] for a in body['places']] == ['Rocky Mountain National Park']
    assert body['places'][0]['rank'] == 1
    body = client.get('/api/places?max_rank=2').get_json()
    assert {a['source_kind'] for a in body['places']} == {'park', 'campground'}
    assert client.get('/api/places?max_rank=x').status_code == 400


def test_default_order_is_rank_then_name(client) -> None:
    _seed()
    kinds = [a['source_kind'] for a in client.get('/api/places').get_json()['places']]
    assert kinds[0] == 'park'  # rank 1 first; the rank-3 pair follows in name order


def test_center_orders_same_rank_by_distance_nulls_last(client) -> None:
    conn = get_connection()
    far_tour = Place('tour', 'TR2', 'yell', 'Yellowstone Tour', 44.6, -110.5, None, {})
    load(conn, [_TOUR, far_tour, _NOCOORD], [])  # all rank 3 (attraction)
    conn.close()
    body = client.get('/api/places?center=-110.86,44.65').get_json()
    assert [a['name'] for a in body['places']] == [
        'Yellowstone Tour',  # nearest to center
        'Holzwarth Historic Site Tour',
        'Stargazing',  # NULL coords: no distance, sorts last
    ]
    assert client.get('/api/places?center=nope').status_code == 400


def test_list_rejects_bad_bbox_and_limit(client) -> None:
    assert client.get('/api/places?bbox=1,2,3').status_code == 400
    assert client.get('/api/places?limit=0').status_code == 400


def test_detail_includes_parsed_details(client) -> None:
    _seed()
    listed = client.get('/api/places?q=holzwarth').get_json()['places'][0]
    body = client.get(f'/api/places/{listed["id"]}').get_json()
    assert body['details']['stops'][0]['significance'] == 'First'


def test_detail_404(client) -> None:
    _seed()
    assert client.get('/api/places/99999').status_code == 404


# --- /api/places/events -------------------------------------------------------


def test_events_window_groups_occurrences_per_event(client) -> None:
    _seed()
    body = client.get('/api/places/events?start=2026-07-04&end=2026-07-05').get_json()
    by_name = {e['name']: e for e in body['events']}
    assert set(by_name) == {'Bird Walk', 'Geyser Talk'}
    assert by_name['Bird Walk']['dates'] == [
        {'date': '2026-07-04', 'time_start': '07:30', 'time_end': '09:00'}
    ]
    assert by_name['Bird Walk']['is_free'] == 1
    assert by_name['Geyser Talk']['dates'][0]['time_start'] is None


def test_events_park_and_bbox_filters(client) -> None:
    _seed()
    body = client.get('/api/places/events?park=yell').get_json()
    assert [e['name'] for e in body['events']] == ['Geyser Talk']
    body = client.get('/api/places/events?bbox=-106,40,-105,41').get_json()
    assert [e['name'] for e in body['events']] == ['Bird Walk']


def test_events_rejects_bad_date(client) -> None:
    assert client.get('/api/places/events?start=07/04/2026').status_code == 400


def test_event_detail_full_occurrences_and_404(client) -> None:
    _seed()
    listed = client.get('/api/places/events?park=romo').get_json()['events'][0]
    body = client.get(f'/api/places/events/{listed["id"]}').get_json()
    assert body['details'] == {'description': 'Bring binoculars.'}
    assert [d['date'] for d in body['dates']] == ['2026-07-04', '2026-07-08']
    assert client.get('/api/places/events/99999').status_code == 404


# --- import idempotency --------------------------------------------------------------


def test_load_is_full_replace(client) -> None:
    _seed()
    _seed()
    body = client.get('/api/places').get_json()
    assert body['count'] == 4
    events = client.get('/api/places/events').get_json()
    assert events['count'] == 2


# --- OSM merge -------------------------------------------------------------------------


def _write_transfer_db(path: Path, rows: list[tuple]) -> None:
    """Write a minimal transfer DB in tools/build_osm_pois.py's shape."""
    src = sqlite3.connect(path)
    src.execute(
        'CREATE TABLE places (source TEXT NOT NULL, source_kind TEXT NOT NULL, '
        'source_id TEXT NOT NULL, park_code TEXT, name TEXT NOT NULL, lat REAL, lon REAL, '
        'summary TEXT, details TEXT NOT NULL, synced_at TEXT NOT NULL, '
        'category TEXT NOT NULL, rank INTEGER NOT NULL, UNIQUE (source, source_id))'
    )
    src.executemany('INSERT INTO places VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', rows)
    src.commit()
    src.close()


_OSM_ROW = (
    'osm',
    'amenity=cafe',
    'node/1',
    None,
    'Bean There',
    39.7,
    -105.0,
    'Cafe · Coffee shop',
    '{"amenity": "cafe"}',
    'T1',
    'food_drink',
    3,
)


def test_osm_merge_full_replaces_slice_and_rebuilds_fts(client, tmp_path) -> None:
    _seed()
    transfer = tmp_path / 'osm-places.db'
    _write_transfer_db(transfer, [_OSM_ROW])
    conn = get_connection()
    assert merge_osm(conn, transfer) == 1
    conn.close()

    body = client.get('/api/places').get_json()
    assert body['count'] == 5  # 4 federal + 1 osm; merge never touches nps rows
    found = client.get('/api/places?q=coffee').get_json()['places']
    assert [a['name'] for a in found] == ['Bean There']
    assert found[0]['category'] == 'food_drink'

    replacement = tmp_path / 'osm-places-2.db'
    _write_transfer_db(replacement, [_OSM_ROW[:2] + ('node/2', None, 'Roast Haus') + _OSM_ROW[5:]])
    conn = get_connection()
    assert merge_osm(conn, replacement) == 1
    conn.close()
    assert client.get('/api/places?q=bean').get_json()['count'] == 0
    assert client.get('/api/places?q=roast').get_json()['count'] == 1


# --- sidecar migration ---------------------------------------------------------------


def test_sidecar_migration_moves_legacy_tier(tmp_path, monkeypatch) -> None:
    """A main DB holding the legacy attractions tables migrates into the sidecar.

    Builds the pre-sidecar layout (attractions/attraction_events/
    attraction_event_dates in the main file), then runs the real startup path
    and asserts the rows moved (ids preserved — event_dates references them),
    the legacy tables dropped, and a second startup is a no-op.
    """
    import sqlite3

    import api.db as db

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'main.db')
    monkeypatch.setattr(db, 'PLACES_DB_PATH', None)
    legacy = sqlite3.connect(tmp_path / 'main.db')
    legacy.executescript("""
        CREATE TABLE attractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
            source_kind TEXT NOT NULL, source_id TEXT NOT NULL, park_code TEXT,
            name TEXT NOT NULL, lat REAL, lon REAL, summary TEXT,
            details TEXT NOT NULL, synced_at TEXT NOT NULL
        );
        CREATE TABLE attraction_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
            source_id TEXT NOT NULL, park_code TEXT, name TEXT NOT NULL,
            lat REAL, lon REAL, location_text TEXT, is_free INTEGER,
            needs_reservation INTEGER, details TEXT NOT NULL, synced_at TEXT NOT NULL
        );
        CREATE TABLE attraction_event_dates (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id INTEGER NOT NULL,
            date TEXT NOT NULL, time_start TEXT, time_end TEXT
        );
        INSERT INTO attractions (id, source, source_kind, source_id, park_code,
            name, lat, lon, summary, details, synced_at)
            VALUES (7, 'nps', 'park', 'P1', 'romo', 'Rocky', 40.4, -105.7, 'S', '{}', 'T');
        INSERT INTO attraction_events (id, source, source_id, park_code, name,
            lat, lon, location_text, is_free, needs_reservation, details, synced_at)
            VALUES (9, 'nps', 'E1', 'romo', 'Walk', 40.4, -105.5, NULL, 1, 0, '{}', 'T');
        INSERT INTO attraction_event_dates (id, event_id, date, time_start, time_end)
            VALUES (3, 9, '2026-07-04', '07:30', '09:00');
    """)
    legacy.commit()
    legacy.close()

    conn = db.get_connection()
    db.init_db(conn)
    assert [tuple(r) for r in conn.execute('SELECT id, name FROM places')] == [(7, 'Rocky')]
    stamped = conn.execute('SELECT category, rank FROM places WHERE id = 7').fetchone()
    assert tuple(stamped) == ('park', 1)  # legacy copies get the kind map applied
    assert [tuple(r) for r in conn.execute('SELECT id, name FROM place_events')] == [(9, 'Walk')]
    assert [tuple(r) for r in conn.execute('SELECT id, event_id, date FROM place_event_dates')] == [
        (3, 9, '2026-07-04')
    ]
    legacy_left = conn.execute(
        "SELECT name FROM main.sqlite_master WHERE name LIKE 'attraction%'"
    ).fetchall()
    assert legacy_left == []
    assert (tmp_path / 'places.db').exists()

    db.init_db(conn)  # steady-state re-run: gate is cold, nothing changes
    assert conn.execute('SELECT COUNT(*) FROM places').fetchone()[0] == 1


def test_broad_columns_migration_backfills_existing_sidecar(tmp_path, monkeypatch) -> None:
    """A pre-Phase-2 sidecar (no category/rank) gains the columns + backfill + FTS.

    Mirrors the Pi's state at deploy: the sidecar exists with federal rows but
    predates the broad-POI columns. Startup must add the columns, stamp
    category/rank from the kind map (unknown kinds stay NULL), and leave the
    FTS index queryable. A second startup is a no-op.
    """
    import api.db as db

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'main.db')
    monkeypatch.setattr(db, 'PLACES_DB_PATH', None)
    old = sqlite3.connect(tmp_path / 'places.db')
    old.executescript("""
        CREATE TABLE places (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
            source_kind TEXT NOT NULL, source_id TEXT NOT NULL, park_code TEXT,
            name TEXT NOT NULL, lat REAL, lon REAL, summary TEXT,
            details TEXT NOT NULL, synced_at TEXT NOT NULL
        );
        INSERT INTO places (source, source_kind, source_id, park_code, name,
            lat, lon, summary, details, synced_at)
        VALUES ('nps', 'park', 'P1', 'romo', 'Rocky', 40.4, -105.7, 'S', '{}', 'T'),
               ('ridb', 'facility', 'F1', '123', 'Some Trailhead', 39, -106, NULL, '{}', 'T'),
               ('nps', 'mystery_kind', 'M1', NULL, 'Unknown', 1, 1, NULL, '{}', 'T');
    """)
    old.commit()
    old.close()

    conn = db.get_connection()
    db.init_db(conn)
    rows = {
        r['source_id']: (r['category'], r['rank'])
        for r in conn.execute('SELECT source_id, category, rank FROM places')
    }
    assert rows == {'P1': ('park', 1), 'F1': ('outdoors', 3), 'M1': (None, None)}
    hit = conn.execute('SELECT rowid FROM places_fts WHERE places_fts MATCH \'"trailh"*\'')
    assert len(hit.fetchall()) == 1

    db.init_db(conn)  # steady-state: columns present, migration skips
    assert conn.execute('SELECT COUNT(*) FROM places').fetchone()[0] == 3
