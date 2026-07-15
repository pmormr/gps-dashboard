"""Flask-client tests for the offline-data status read (``/api/data/status``).

The probe internals are covered in ``test_updater_probes.py``; these tests
exercise the route end-to-end against the isolated temp DB, with every
file-backed probe pointed at tmp locations so results don't depend on the
developer machine's real caches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.db import get_connection
from updater.chunks import CHUNKS


@pytest.fixture
def hermetic_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the file-backed probes at empty tmp locations."""
    from api import tile_layers
    from common import satcat

    monkeypatch.setattr(tile_layers, 'PMTILES_PATH', tmp_path / 'osm.pmtiles')
    monkeypatch.setattr(tile_layers, 'TERRAIN_PMTILES_PATH', tmp_path / 'terrain.pmtiles')
    monkeypatch.setattr(tile_layers, 'TILE_CACHE_DIR', tmp_path / 'tiles')
    monkeypatch.setattr(satcat, 'DEFAULT_CACHE_PATH', tmp_path / 'satcat.json')
    monkeypatch.delenv('GPS_NETWORK_DOCS_GIT_DIR', raising=False)


def _seed_places(source: str, synced_at: str) -> None:
    """Insert one minimal row into a places-tier source slice of the temp DB."""
    conn = get_connection()
    conn.execute(
        'INSERT INTO places (source, source_kind, source_id, name, details, synced_at) '
        "VALUES (?, 'park', ?, ?, '{}', ?)",
        (source, f'{source}-1', f'{source} row', synced_at),
    )
    conn.commit()
    conn.close()


def test_status_shape(client, hermetic_paths: None) -> None:
    res = client.get('/api/data/status')
    assert res.status_code == 200
    body = res.get_json()
    assert body['generated_at']
    assert [c['id'] for c in body['chunks']] == [c.id for c in CHUNKS]
    for entry in body['chunks']:
        for key in ('label', 'section', 'action', 'cadence', 'state', 'detail', 'warnings'):
            assert key in entry


def test_empty_db_reads_missing(client, hermetic_paths: None) -> None:
    body = client.get('/api/data/status').get_json()
    by_id = {c['id']: c for c in body['chunks']}
    for chunk_id in ('nps', 'ridb', 'osm', 'gnis'):
        assert by_id[chunk_id]['state'] == 'missing'
        assert by_id[chunk_id]['detail'] == {'rows': 0}
    assert by_id['basemap']['state'] == 'missing'


def test_freshness_and_warnings_flow_through(client, hermetic_paths: None) -> None:
    _seed_places('osm', '2026-01-02T00:00:00.000Z')
    _seed_places('gnis', '2026-01-01T00:00:00.000Z')
    body = client.get('/api/data/status').get_json()
    by_id = {c['id']: c for c in body['chunks']}
    assert by_id['osm']['synced_at'] == '2026-01-02T00:00:00.000Z'
    assert by_id['osm']['age_days'] > 0
    assert by_id['gnis']['warnings'], 'GNIS predating OSM must derive an ordering warning'


def test_present_archive_reports_size(
    client, hermetic_paths: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from api import tile_layers

    archive = tmp_path / 'osm.pmtiles'
    archive.write_bytes(b'x' * 1024)
    monkeypatch.setattr(tile_layers, 'PMTILES_PATH', archive)
    body = client.get('/api/data/status').get_json()
    basemap = next(c for c in body['chunks'] if c['id'] == 'basemap')
    assert basemap['state'] == 'ok'
    assert basemap['detail']['size_bytes'] == 1024
