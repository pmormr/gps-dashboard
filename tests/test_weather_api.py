"""Flask-client tests for the weather delivery routes.

Exercises the frame index (windowing, ordering, empty archive) and the per-frame
PMTiles route (existence + layer gating + byte-range support), plus the ``/data``
chunk surfacing the radar archive.
"""

from __future__ import annotations

import pytest
from PIL import Image

from weather import archive
from weather.registry import RADAR


@pytest.fixture
def wclient(tmp_path, monkeypatch):
    """The real app wired to a temp DB and a temp weather archive dir."""
    import api.db as db

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setenv('GPS_WEATHER_ARCHIVE_DIR', str(tmp_path / 'weather'))
    from api.app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _stage_frame(frame_ms: int) -> None:
    """Write a minimal real per-frame PMTiles archive for the radar layer."""
    tile = Image.new('RGBA', (256, 256), (255, 0, 0, 255))
    archive.pack_frame(RADAR, frame_ms, [(8, 40, 90, tile)])


def test_frames_empty_archive(wclient):
    resp = wclient.get('/api/weather/radar/frames')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == {
        'layer': 'radar',
        'generated_at': body['generated_at'],
        'count': 0,
        'newest': None,
        'oldest': None,
        'frames': [],
    }


def test_frames_lists_newest_first(wclient):
    for ts in (1000, 3000, 2000):
        _stage_frame(ts)
    body = wclient.get('/api/weather/radar/frames').get_json()
    assert body['frames'] == [3000, 2000, 1000]
    assert body['count'] == 3
    assert body['newest'] == 3000
    assert body['oldest'] == 1000


def test_frames_window_trims_to_recent(wclient):
    import time

    now_ms = int(time.time() * 1000)
    recent = now_ms - 30 * 60_000  # 30 min ago
    old = now_ms - 5 * 3_600_000  # 5 h ago
    _stage_frame(recent)
    _stage_frame(old)
    body = wclient.get('/api/weather/radar/frames?window=1').get_json()
    assert body['frames'] == [recent]


def test_frames_unknown_layer_404(wclient):
    assert wclient.get('/api/weather/nope/frames').status_code == 404


def test_pmtiles_missing_frame_404(wclient):
    assert wclient.get('/tiles/weather/radar/123.pmtiles').status_code == 404


def test_pmtiles_unknown_layer_404(wclient):
    _stage_frame(123)
    assert wclient.get('/tiles/weather/nope/123.pmtiles').status_code == 404


def test_pmtiles_served_with_range_support(wclient):
    _stage_frame(1785267488000)
    resp = wclient.get('/tiles/weather/radar/1785267488000.pmtiles')
    assert resp.status_code == 200
    assert resp.headers['Accept-Ranges'] == 'bytes'
    assert resp.data[:7] == b'PMTiles'  # spec magic

    ranged = wclient.get(
        '/tiles/weather/radar/1785267488000.pmtiles', headers={'Range': 'bytes=0-6'}
    )
    assert ranged.status_code == 206
    assert ranged.data == b'PMTiles'


def test_geojson_empty_when_never_fetched(wclient):
    body = wclient.get('/api/weather/warnings/geojson').get_json()
    assert body == {'type': 'FeatureCollection', 'features': [], 'fetched_at': None}


def test_geojson_serves_snapshot_with_fetched_at(wclient, tmp_path):
    import httpx

    from weather import registry, vector
    from weather.registry import WARNINGS

    fc = {
        'type': 'FeatureCollection',
        'features': [{'type': 'Feature', 'geometry': None, 'properties': {}}],
    }
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=fc)))
    vector.fetch_and_store(WARNINGS, client=client)
    assert registry.vector_path('warnings').exists()

    body = wclient.get('/api/weather/warnings/geojson').get_json()
    assert body['type'] == 'FeatureCollection'
    assert len(body['features']) == 1
    assert body['fetched_at'] is not None  # file mtime, canonical ms-UTC


def test_geojson_unknown_layer_404(wclient):
    assert wclient.get('/api/weather/radar/geojson').status_code == 404


def test_data_status_includes_radar_chunk(wclient):
    _stage_frame(1000)
    _stage_frame(2000)
    chunks = wclient.get('/api/data/status').get_json()['chunks']
    radar = next(c for c in chunks if c['id'] == 'weather_radar')
    assert radar['section'] == 'map'
    assert radar['action'] == 'readonly'
    assert radar['state'] == 'ok'
    assert radar['detail']['frames'] == 2
