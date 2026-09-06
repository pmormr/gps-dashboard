"""Flask-client tests for the offline-data status read (``/api/data/status``).

The probe internals are covered in ``test_updater_probes.py``; these tests
exercise the route end-to-end against the isolated temp DB, with every
file-backed probe pointed at tmp locations so results don't depend on the
developer machine's real caches.
"""

from __future__ import annotations

import os
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


# --- Runner HTTP surface (Phase 2) ---------------------------------------------------


@pytest.fixture
def hermetic_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Hermetic staging + log dirs for the runner-facing routes."""
    monkeypatch.setenv('GPS_STAGING_DIR', str(tmp_path / 'staging'))
    monkeypatch.setenv('GPS_UPDATE_LOG_DIR', str(tmp_path / 'update-logs'))
    return tmp_path


def _insert_run(status: str = 'running', pid: int | None = None, log_path: str = 'x.log') -> int:
    conn = get_connection()
    finished = None if status == 'running' else '2026-01-01T00:00:01.000Z'
    cursor = conn.execute(
        'INSERT INTO update_runs (chunk, started, finished, status, pid, log_path) '
        "VALUES ('satcat', '2026-01-01T00:00:00.000Z', ?, ?, ?, ?)",
        (finished, status, os.getpid() if pid is None else pid, log_path),
    )
    conn.commit()
    run_id = cursor.lastrowid
    assert run_id is not None
    conn.close()
    return run_id


def test_status_carries_run_state(client, hermetic_paths: None, hermetic_runner: Path) -> None:
    body = client.get('/api/data/status').get_json()
    assert body['active_run'] is None
    by_id = {c['id']: c for c in body['chunks']}
    assert by_id['satcat']['run']['runnable'] is True
    assert by_id['satcat']['last_run'] is None
    assert by_id['osm']['run'] == {
        'supported': True,
        'requires_staged': True,
        'staged': None,
        'runnable': False,
    }
    assert by_id['terrain']['run']['supported'] is False


def test_status_surfaces_active_and_last_runs(
    client, hermetic_paths: None, hermetic_runner: Path
) -> None:
    _insert_run(status='ok', log_path='old.log')
    _insert_run(status='running')
    body = client.get('/api/data/status').get_json()
    assert body['active_run'] is not None
    assert body['active_run']['chunk'] == 'satcat'
    by_id = {c['id']: c for c in body['chunks']}
    assert by_id['satcat']['last_run']['status'] == 'running'
    assert 'log_path' not in by_id['satcat']['last_run']


def test_update_rejects_unknown_and_unstaged(
    client, hermetic_paths: None, hermetic_runner: Path
) -> None:
    assert client.post('/api/data/update/terrain').status_code == 400
    res = client.post('/api/data/update/osm')
    assert res.status_code == 400
    assert 'staged' in res.get_json()['error']


def test_update_409_when_busy(client, hermetic_paths: None, hermetic_runner: Path) -> None:
    _insert_run(status='running')
    assert client.post('/api/data/update/satcat').status_code == 409


def test_update_spawns_and_reports_run(
    client, hermetic_paths: None, hermetic_runner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeProc:
        pid = os.getpid()

        def poll(self) -> int | None:
            return None

    def fake_spawn(chunk: str, force: bool) -> tuple[_FakeProc, Path]:
        assert (chunk, force) == ('satcat', True)
        _insert_run(status='running')
        return _FakeProc(), hermetic_runner / 'spawned.log'

    monkeypatch.setattr('api.routes.data.spawn', fake_spawn)
    res = client.post('/api/data/update/satcat', json={'force': True})
    assert res.status_code == 202
    assert res.get_json()['run']['status'] == 'running'


def test_update_maps_runner_busy_exit(
    client, hermetic_paths: None, hermetic_runner: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _DeadProc:
        pid = 999_999_999
        returncode = 75

        def poll(self) -> int | None:
            return self.returncode

    monkeypatch.setattr(
        'api.routes.data.spawn', lambda chunk, force: (_DeadProc(), hermetic_runner / 'x.log')
    )
    assert client.post('/api/data/update/satcat').status_code == 409


def test_run_read_and_log_tail(client, hermetic_paths: None, hermetic_runner: Path) -> None:
    log = hermetic_runner / 'run.log'
    log.write_text('one\ntwo\nthree\n')
    run_id = _insert_run(status='ok', log_path=str(log))
    body = client.get(f'/api/data/runs/{run_id}?lines=2').get_json()
    assert body['run']['status'] == 'ok'
    assert body['log'] == 'two\nthree'
    assert client.get('/api/data/runs/999').status_code == 404


def test_cancel_maps_states(client, hermetic_paths: None, hermetic_runner: Path) -> None:
    assert client.post('/api/data/runs/999/cancel').status_code == 404
    finished = _insert_run(status='ok')
    assert client.post(f'/api/data/runs/{finished}/cancel').status_code == 409
