"""Unit tests for the offline-data freshness probes + status derivation.

Probes are exercised against an isolated temp DB (main + derived places
sidecar, the real ``get_connection`` shape) and tmp-path file assets;
``status_payload`` is exercised for state tiers (ok/stale/missing/error) and
the derived ordering warnings (GNIS/wiki predating the OSM slice).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

import api.db as db
from api import tile_layers
from common import satcat
from common.timefmt import now_canonical
from updater import chunks, probes

_OLD = '2020-01-01T00:00:00.000Z'
_OLDER = '2019-01-01T00:00:00.000Z'


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[sqlite3.Connection]:
    """An initialized connection against a throwaway main DB + derived sidecar."""
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(db, 'PLACES_DB_PATH', None)
    c = db.get_connection()
    db.init_db(c)
    yield c
    c.close()


def _insert_places(conn: sqlite3.Connection, source: str, synced_at: str, n: int = 1) -> None:
    """Insert ``n`` minimal rows into one places-tier source slice."""
    conn.executemany(
        'INSERT INTO places (source, source_kind, source_id, name, details, synced_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        [(source, 'park', f'{source}-{i}', f'{source} row {i}', '{}', synced_at) for i in range(n)],
    )
    conn.commit()


def _insert_wiki(conn: sqlite3.Connection, key: str, fetched_at: str) -> None:
    """Insert one minimal ``place_wiki`` cache row."""
    conn.execute(
        'INSERT INTO place_wiki (wiki_key, title, lang, extract, fetched_at) '
        'VALUES (?, ?, ?, ?, ?)',
        (key, 'Title', 'en', 'Extract.', fetched_at),
    )
    conn.commit()


# --- DB probes ----------------------------------------------------------------------


def test_places_slice_empty(conn: sqlite3.Connection) -> None:
    fresh = probes.places_slice(conn, 'nps')
    assert fresh.synced_at is None
    assert fresh.detail == {'rows': 0}


def test_places_slice_scoped_to_source(conn: sqlite3.Connection) -> None:
    _insert_places(conn, 'nps', _OLD, n=3)
    _insert_places(conn, 'osm', now_canonical(), n=2)
    fresh = probes.places_slice(conn, 'nps')
    assert fresh.synced_at == _OLD
    assert fresh.detail == {'rows': 3}


def test_place_wiki_max_fetched(conn: sqlite3.Connection) -> None:
    assert probes.place_wiki(conn).synced_at is None
    _insert_wiki(conn, 'Q1', _OLDER)
    _insert_wiki(conn, 'Q2', _OLD)
    fresh = probes.place_wiki(conn)
    assert fresh.synced_at == _OLD
    assert fresh.detail == {'rows': 2}


def test_phone_and_drone_tiers(conn: sqlite3.Connection) -> None:
    assert probes.phone_tier(conn).synced_at is None
    assert probes.drone_tier(conn).synced_at is None
    conn.execute(
        'INSERT INTO phone_paths (start_time, end_time, n_points, min_lat, min_lon, '
        'max_lat, max_lon, imported_at) VALUES (?, ?, 5, 0, 0, 1, 1, ?)',
        (_OLDER, _OLD, _OLD),
    )
    conn.execute(
        'INSERT INTO drone_flights (model, model_code, first_fix_utc, last_fix_utc, '
        'n_points, min_lat, min_lon, max_lat, max_lon, imported_at) '
        'VALUES (?, ?, ?, ?, 9, 0, 0, 1, 1, ?)',
        ('Mini 4 Pro', 'M4P', _OLDER, _OLD, _OLD),
    )
    conn.commit()
    assert probes.phone_tier(conn) == probes.Freshness(_OLD, {'paths': 1})
    assert probes.drone_tier(conn) == probes.Freshness(_OLD, {'flights': 1})


# --- File probes --------------------------------------------------------------------


def test_file_archive_missing_and_present(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / 'osm.pmtiles'
    monkeypatch.setattr(tile_layers, 'PMTILES_PATH', archive)
    fresh = probes.basemap_archive(conn)
    assert fresh.synced_at is None
    assert fresh.detail == {'path': str(archive)}

    archive.write_bytes(b'x' * 42)
    fresh = probes.basemap_archive(conn)
    assert fresh.synced_at is not None
    assert fresh.detail['size_bytes'] == 42


def test_satcat_cache_probe(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / 'satcat.json'
    monkeypatch.setattr(satcat, 'DEFAULT_CACHE_PATH', cache)
    assert probes.satcat_cache(conn).synced_at is None
    cache.write_text('{}')
    assert probes.satcat_cache(conn).synced_at is not None


def test_raster_cache_counts_tiles(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / 'tiles'
    monkeypatch.setattr(tile_layers, 'TILE_CACHE_DIR', root)
    assert probes.raster_cache(conn).synced_at is None

    tile_dir = root / 'usgs' / '10' / '1'
    tile_dir.mkdir(parents=True)
    (tile_dir / '2.png').write_bytes(b'png' * 10)
    (tile_dir / '2.etag').write_text('etag')
    fresh = probes.raster_cache(conn)
    assert fresh.synced_at is not None
    assert fresh.detail['tiles'] == 1
    assert fresh.detail['size_bytes'] == 34


def test_docs_vault_probe(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv('GPS_NETWORK_DOCS_GIT_DIR', raising=False)
    assert probes.docs_vault(conn) == probes.Freshness(None, {'configured': False})

    bare = tmp_path / 'vault.git'
    (bare / 'refs' / 'heads').mkdir(parents=True)
    monkeypatch.setenv('GPS_NETWORK_DOCS_GIT_DIR', str(bare))
    assert probes.docs_vault(conn).synced_at is None

    (bare / 'refs' / 'heads' / 'main').write_text('abc123\n')
    assert probes.docs_vault(conn).synced_at is not None


# --- Status derivation --------------------------------------------------------------


@pytest.fixture
def hermetic_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every file-backed probe at empty tmp locations."""
    monkeypatch.setattr(tile_layers, 'PMTILES_PATH', tmp_path / 'osm.pmtiles')
    monkeypatch.setattr(tile_layers, 'TERRAIN_PMTILES_PATH', tmp_path / 'terrain.pmtiles')
    monkeypatch.setattr(tile_layers, 'TILE_CACHE_DIR', tmp_path / 'tiles')
    monkeypatch.setattr(satcat, 'DEFAULT_CACHE_PATH', tmp_path / 'satcat.json')
    monkeypatch.setenv('GPS_WEATHER_ARCHIVE_DIR', str(tmp_path / 'weather'))
    monkeypatch.delenv('GPS_NETWORK_DOCS_GIT_DIR', raising=False)


def _entry(payload: dict, chunk_id: str) -> dict:
    return next(c for c in payload['chunks'] if c['id'] == chunk_id)


def test_status_payload_covers_registry(conn: sqlite3.Connection, hermetic_files: None) -> None:
    payload = chunks.status_payload(conn)
    assert payload['generated_at']
    assert [c['id'] for c in payload['chunks']] == [c.id for c in chunks.CHUNKS]
    assert all(c['state'] == 'missing' for c in payload['chunks'])


def test_status_states_ok_and_stale(conn: sqlite3.Connection, hermetic_files: None) -> None:
    _insert_places(conn, 'nps', now_canonical())
    _insert_places(conn, 'ridb', _OLD)
    payload = chunks.status_payload(conn)
    assert _entry(payload, 'nps')['state'] == 'ok'
    ridb = _entry(payload, 'ridb')
    assert ridb['state'] == 'stale'
    assert ridb['age_days'] > ridb['stale_days']


def test_informational_chunks_never_stale(conn: sqlite3.Connection, hermetic_files: None) -> None:
    conn.execute(
        'INSERT INTO phone_paths (start_time, end_time, n_points, min_lat, min_lon, '
        'max_lat, max_lon, imported_at) VALUES (?, ?, 5, 0, 0, 1, 1, ?)',
        (_OLDER, _OLD, _OLD),
    )
    conn.commit()
    assert _entry(chunks.status_payload(conn), 'phone')['state'] == 'ok'


def test_ordering_warnings(conn: sqlite3.Connection, hermetic_files: None) -> None:
    _insert_places(conn, 'osm', now_canonical())
    _insert_places(conn, 'gnis', _OLD)
    _insert_wiki(conn, 'Q1', _OLD)
    payload = chunks.status_payload(conn)
    assert 'OSM' in _entry(payload, 'gnis')['warnings'][0]
    assert 'OSM' in _entry(payload, 'wiki')['warnings'][0]
    assert _entry(payload, 'osm')['warnings'] == []


def test_ordering_warnings_absent_when_ordered(
    conn: sqlite3.Connection, hermetic_files: None
) -> None:
    _insert_places(conn, 'osm', _OLD)
    _insert_places(conn, 'gnis', now_canonical())
    payload = chunks.status_payload(conn)
    assert _entry(payload, 'gnis')['warnings'] == []


def test_weather_radar_probe_empty(conn: sqlite3.Connection, hermetic_files: None) -> None:
    fresh = probes.weather_radar(conn)
    assert fresh.synced_at is None
    assert fresh.detail == {'frames': 0}


def test_weather_radar_probe_reports_span_and_gap(
    conn: sqlite3.Connection, hermetic_files: None
) -> None:
    from PIL import Image

    from weather import archive
    from weather.registry import RADAR

    tile = Image.new('RGBA', (256, 256), (1, 2, 3, 255))
    hour = 3_600_000
    for ts in (1000 * hour, 1002 * hour, 1005 * hour):  # 2 h then 3 h apart
        archive.pack_frame(RADAR, ts, [(8, 40, 90, tile)])
    fresh = probes.weather_radar(conn)
    assert fresh.synced_at is not None  # newest frame drives freshness
    assert fresh.detail['frames'] == 3
    assert fresh.detail['span_hours'] == 5.0
    assert fresh.detail['largest_gap_min'] == 180.0


def test_probe_error_degrades_one_chunk(
    conn: sqlite3.Connection, hermetic_files: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(conn: sqlite3.Connection, source: str) -> probes.Freshness:
        raise RuntimeError('probe exploded')

    monkeypatch.setattr(probes, 'places_slice', boom)
    payload = chunks.status_payload(conn)
    nps = _entry(payload, 'nps')
    assert nps['state'] == 'error'
    assert nps['error'] == 'probe exploded'
    assert _entry(payload, 'phone')['state'] == 'missing'
