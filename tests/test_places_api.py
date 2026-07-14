"""Flask-client tests for the places read API (``/api/places*``).

Seeds the isolated temp DB (the ``client`` fixture) through the importer's own
:func:`tools.import_places.load`, then exercises the bbox/kind/category/rank/
search filters, FTS prefix semantics, the OSM merge, the occurrence-window
grouping, the detail endpoints, and param validation.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from api.db import get_connection, place_wiki_key
from tools.fetch_wikipedia import Target, collect_targets
from tools.import_places import (
    GNIS_KIND_RANKS,
    Event,
    EventDate,
    Place,
    load,
    merge_osm,
    merge_wiki,
    osm_gnis_ids,
)

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


def test_search_bounded_candidate_mode(client, monkeypatch) -> None:
    """Huge match sets switch to the bounded bm25 candidate pool.

    Forced by dropping the threshold to 0 — with the pool larger than the
    seed data the results must match unbounded mode, filters included.
    """
    import api.routes.places as places_routes

    _seed()
    monkeypatch.setattr(places_routes, '_FTS_UNBOUNDED_MAX', 0)
    body = client.get('/api/places?q=holzwarth').get_json()
    assert [a['name'] for a in body['places']] == ['Holzwarth Historic Site Tour']
    body = client.get('/api/places?q=rocky&kind=park&bbox=-106,40,-105,41').get_json()
    assert [a['name'] for a in body['places']] == ['Rocky Mountain National Park']
    body = client.get('/api/places?q=a&center=-110.86,44.65').get_json()
    assert body['count'] >= 2  # ordering clauses (score/rank/distance) all apply


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


# --- facets ---------------------------------------------------------------------------


def test_facets_count_kinds_ordered_by_count_then_kind(client) -> None:
    _seed()
    body = client.get('/api/places?facets=1').get_json()
    assert body['facets_sampled'] is False
    assert body['facets'] == [
        {'kind': 'campground', 'count': 1},
        {'kind': 'park', 'count': 1},
        {'kind': 'thingstodo', 'count': 1},
        {'kind': 'tour', 'count': 1},
    ]


def test_facets_absent_without_param(client) -> None:
    _seed()
    assert 'facets' not in client.get('/api/places').get_json()


def test_facets_ignore_kind_filter_but_respect_others(client) -> None:
    """A selected kind narrows the list, never its own facet row."""
    _seed()
    body = client.get('/api/places?facets=1&kind=park&park=romo').get_json()
    assert {a['source_kind'] for a in body['places']} == {'park'}
    assert {f['kind'] for f in body['facets']} == {'park', 'tour', 'thingstodo'}


def test_facets_with_search_and_bounded_mode(client, monkeypatch) -> None:
    """Facets follow the search scope in both FTS modes (unbounded + pool)."""
    import api.routes.places as places_routes

    _seed()
    body = client.get('/api/places?facets=1&q=holzwarth').get_json()
    assert body['facets'] == [{'kind': 'tour', 'count': 1}]
    # Force bounded mode (a bbox keeps the gate on _FTS_UNBOUNDED_MAX).
    monkeypatch.setattr(places_routes, '_FTS_UNBOUNDED_MAX', 0)
    body = client.get('/api/places?facets=1&q=holzwarth&kind=park&bbox=-106,40,-105,41').get_json()
    assert body['places'] == []  # kind filter applies to the list…
    assert body['facets'] == [{'kind': 'tour', 'count': 1}]  # …not the facets


def test_search_without_bbox_uses_pool_above_candidate_limit(client, monkeypatch) -> None:
    """Unscoped searches never join more than the candidate pool."""
    import api.routes.places as places_routes

    _seed()
    # Gate is the pool size when there's no bbox: 4 matches > 0 → bounded mode.
    monkeypatch.setattr(places_routes, '_FTS_CANDIDATE_LIMIT', 0)
    assert client.get('/api/places?q=holzwarth').get_json()['count'] == 0  # pool LIMIT 0
    assert client.get('/api/places?q=holzwarth&bbox=-106,40,-105,41').get_json()['count'] == 1


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


def test_rank_partial_indexes_exist(client) -> None:
    """The per-gate partial indexes back the rank-gated viewport reads."""
    conn = get_connection()
    names = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM places_db.sqlite_master WHERE type='index'"
        ).fetchall()
    }
    conn.close()
    assert {'idx_places_latlon_r1', 'idx_places_latlon_r2', 'idx_places_latlon_r3'} <= names


# --- GNIS ---------------------------------------------------------------------------


def test_osm_gnis_ids_reads_multivalue_tags(client, tmp_path) -> None:
    transfer = tmp_path / 'osm.db'
    tagged = list(_OSM_ROW)
    tagged[8] = '{"natural": "peak", "gnis:feature_id": "123;456"}'
    _write_transfer_db(
        transfer, [tuple(tagged), _OSM_ROW[:2] + ('node/2', None, 'Plain') + _OSM_ROW[5:]]
    )
    conn = get_connection()
    merge_osm(conn, transfer)
    assert osm_gnis_ids(conn) == {'123', '456'}
    conn.close()


def test_gnis_load_stamps_community_category(client) -> None:
    town = Place(
        'populated_place',
        '789',
        None,
        'Leadville',
        39.25,
        -106.29,
        'Populated Place · Lake, Colorado',
        {'state': 'Colorado'},
    )
    conn = get_connection()
    load(conn, [town], [], source='gnis', kind_ranks=GNIS_KIND_RANKS)
    conn.close()
    found = client.get('/api/places?q=leadville').get_json()['places']
    assert [(a['category'], a['rank'], a['source']) for a in found] == [('community', 3, 'gnis')]


# --- Wikipedia cache ------------------------------------------------------------------


def _seed_wiki_place(details: dict) -> int:
    """Load one OSM-tagged place and return its row id."""
    place = Place('peak', 'wiki1', None, 'Wiki Peak', 40.0, -105.0, None, details)
    conn = get_connection()
    load(conn, [place], [], source='osm')
    row_id = conn.execute("SELECT id FROM places WHERE source_id = 'wiki1'").fetchone()[0]
    conn.close()
    return int(row_id)


def _insert_wiki(key: str, thumb: bytes | None) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            'INSERT INTO place_wiki (wiki_key, title, lang, extract, page_url, thumb, '
            "thumb_mime, fetched_at) VALUES (?, 'Wiki Peak', 'en', 'A peak.', "
            "'https://en.wikipedia.org/wiki/Wiki_Peak', ?, ?, 'T1')",
            (key, thumb, 'image/jpeg' if thumb else None),
        )
    conn.close()


def test_place_wiki_key_variants() -> None:
    assert place_wiki_key({'wikidata': 'q42'}) == 'Q42'
    assert place_wiki_key({'wikidata': 'Q1;Q2'}) == 'Q1'
    assert place_wiki_key({'wikidata': 'Q1', 'wikipedia': 'en:Other'}) == 'Q1'
    assert place_wiki_key({'wikipedia': 'en:Foo_Bar'}) == 'en:Foo Bar'
    assert place_wiki_key({'wikipedia': 'Plain Title'}) == 'en:Plain Title'
    assert place_wiki_key({'wikipedia': 'https://en.wikipedia.org/wiki/X'}) is None
    assert place_wiki_key({'wikipedia': '  '}) is None
    assert place_wiki_key({}) is None


def test_detail_joins_wiki_and_photo_serves_thumb(client) -> None:
    place_id = _seed_wiki_place({'natural': 'peak', 'wikidata': 'Q42'})
    _insert_wiki('Q42', b'\xff\xd8fakejpeg')

    body = client.get(f'/api/places/{place_id}').get_json()
    assert body['wiki']['title'] == 'Wiki Peak'
    assert body['wiki']['extract'] == 'A peak.'
    assert body['wiki']['has_thumb'] is True

    photo = client.get(f'/api/places/{place_id}/photo')
    assert photo.status_code == 200
    assert photo.data == b'\xff\xd8fakejpeg'
    assert photo.mimetype == 'image/jpeg'


def test_detail_wiki_null_and_photo_404_when_uncached(client) -> None:
    place_id = _seed_wiki_place({'natural': 'peak', 'wikipedia': 'en:Uncached'})
    body = client.get(f'/api/places/{place_id}').get_json()
    assert body['wiki'] is None
    assert client.get(f'/api/places/{place_id}/photo').status_code == 404
    assert client.get('/api/places/99999/photo').status_code == 404


def test_wiki_key_resolves_from_wikipedia_tag(client) -> None:
    place_id = _seed_wiki_place({'natural': 'peak', 'wikipedia': 'en:Wiki_Peak'})
    _insert_wiki('en:Wiki Peak', None)
    body = client.get(f'/api/places/{place_id}').get_json()
    assert body['wiki']['has_thumb'] is False
    assert client.get(f'/api/places/{place_id}/photo').status_code == 404


def test_merge_wiki_full_replaces(client, tmp_path) -> None:
    _insert_wiki('Q1', None)
    transfer = tmp_path / 'wiki.db'
    src = sqlite3.connect(transfer)
    src.execute(
        'CREATE TABLE place_wiki (wiki_key TEXT PRIMARY KEY, title TEXT NOT NULL, '
        'lang TEXT NOT NULL, extract TEXT NOT NULL, page_url TEXT, thumb BLOB, '
        'thumb_mime TEXT, fetched_at TEXT NOT NULL)'
    )
    src.execute(
        "INSERT INTO place_wiki VALUES ('Q2', 'New', 'en', 'Fresh.', NULL, NULL, NULL, 'T2')"
    )
    src.commit()
    src.close()

    conn = get_connection()
    assert merge_wiki(conn, transfer) == 1
    keys = [r[0] for r in conn.execute('SELECT wiki_key FROM place_wiki').fetchall()]
    conn.close()
    assert keys == ['Q2']


def test_collect_targets_prefers_explicit_titles(client) -> None:
    conn = get_connection()
    load(
        conn,
        [
            Place('peak', 'w1', None, 'A', 40.0, -105.0, None, {'wikidata': 'Q7'}),
            Place(
                'peak',
                'w2',
                None,
                'B',
                40.1,
                -105.1,
                None,
                {'wikidata': 'Q7', 'wikipedia': 'en:Seven'},
            ),
            Place('peak', 'w3', None, 'C', 40.2, -105.2, None, {'wikipedia': 'fr:Sept'}),
        ],
        [],
        source='osm',
    )
    targets = collect_targets(conn)
    conn.close()
    assert targets['Q7'] == Target('Q7', 'en', 'Seven')
    assert targets['fr:Sept'] == Target('fr:Sept', 'fr', 'Sept')
