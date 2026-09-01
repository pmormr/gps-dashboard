"""Tests for per-frame PMTiles packing + rolling retention.

The pack round-trip reads the archive back with the pmtiles reader to prove the
written file is valid and range-addressable (the same reader pmtiles.js is a
port of), and the retention helpers are pinned as pure math plus a disk prune.
"""

import io

import pytest
from PIL import Image
from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import Compression, TileType

from weather import archive, registry
from weather.registry import RADAR


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    monkeypatch.setenv('GPS_WEATHER_ARCHIVE_DIR', str(tmp_path))
    return tmp_path


def _tile(color) -> Image.Image:
    return Image.new('RGBA', (256, 256), color)


def test_select_expired_is_pure():
    now = 1_000 * archive.DAY_MS  # day 1000
    keys = [now, now - 5 * archive.DAY_MS, now - 20 * archive.DAY_MS]
    expired = archive.select_expired(keys, now, retention_days=14)
    assert expired == [now - 20 * archive.DAY_MS]


def test_pack_frame_empty_writes_nothing(archive_root):
    n = archive.pack_frame(RADAR, 1234, [])
    assert n == 0
    assert not registry.frame_path(RADAR.id, 1234).exists()


def test_pack_frame_roundtrip(archive_root):
    tiles = [
        (8, 40, 90, _tile((255, 0, 0, 255))),
        (8, 41, 90, _tile((0, 255, 0, 255))),
        (2, 0, 1, _tile((0, 0, 255, 255))),
    ]
    n = archive.pack_frame(RADAR, 1785267488000, tiles)
    assert n == 3
    path = registry.frame_path(RADAR.id, 1785267488000)
    assert path.exists()

    with open(path, 'rb') as f:
        reader = Reader(MmapSource(f))
        header = reader.header()
        assert header['tile_type'] == TileType.PNG
        assert header['tile_compression'] == Compression.NONE
        assert header['min_zoom'] == 2
        assert header['max_zoom'] == 8
        assert reader.metadata()['frame_ms'] == 1785267488000
        # Every written tile is addressable and decodes.
        for z, x, y, _img in tiles:
            data = reader.get(z, x, y)
            assert data is not None
            assert Image.open(io.BytesIO(data)).size == (256, 256)
        # A tile we never wrote is absent.
        assert reader.get(8, 42, 90) is None


def test_pack_frame_atomic_no_tmp_left(archive_root):
    archive.pack_frame(RADAR, 42, [(8, 40, 90, _tile((1, 2, 3, 255)))])
    leftovers = list(registry.layer_dir(RADAR.id).glob('*.tmp'))
    assert leftovers == []


def test_existing_frames_and_span(archive_root):
    assert archive.existing_frames(RADAR.id) == set()
    assert archive.frame_span(RADAR.id) == (0, 0, 0)
    for key in (100, 200, 300):
        archive.pack_frame(RADAR, key, [(8, 40, 90, _tile((1, 1, 1, 255)))])
    assert archive.existing_frames(RADAR.id) == {100, 200, 300}
    assert archive.frame_span(RADAR.id) == (3, 100, 300)


def test_prune_removes_only_expired(archive_root):
    now = 1_000 * archive.DAY_MS
    fresh = now - RADAR.retention_days * archive.DAY_MS // 2
    stale = now - (RADAR.retention_days + 29) * archive.DAY_MS
    for key in (fresh, stale):
        archive.pack_frame(RADAR, key, [(8, 40, 90, _tile((1, 1, 1, 255)))])
    removed = archive.prune(RADAR, now)
    assert removed == [stale]
    assert archive.existing_frames(RADAR.id) == {fresh}
