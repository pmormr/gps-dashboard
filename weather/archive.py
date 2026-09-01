"""Per-frame PMTiles packing + rolling-retention prune for a raster layer.

Each frame is one PMTiles archive named by its epoch-ms frame key, written
tmp-then-renamed so a reader never sees a partial file (the ``_atomic_write``
convention). One file per frame keeps the inode count flat and reuses the exact
``osm.pmtiles`` byte-range serving path. Tiles are PNG with ``Compression.NONE``
(PNG is already deflated) and written in ascending tile-id order so the archive
is clustered.
"""

from __future__ import annotations

import io
import os
from collections.abc import Iterable

from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer

from weather import registry
from weather.registry import RasterLayer

#: Milliseconds per day (frame keys and retention are epoch-ms).
DAY_MS = 86_400_000


def _encode_png(tile: Image.Image) -> bytes:
    """Encode a tile as an optimized PNG (smaller archive + wire cost)."""
    buf = io.BytesIO()
    tile.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def pack_frame(
    layer: RasterLayer, frame_ms: int, tiles: Iterable[tuple[int, int, int, Image.Image]]
) -> int:
    """Pack one frame's tiles into a PMTiles archive, written atomically.

    Args:
        layer: The layer being captured (id, bbox, attribution).
        frame_ms: The frame instant / archive key (epoch-ms).
        tiles: ``(z, x, y, image)`` tuples — the sparse pyramid.

    Returns:
        The number of tiles written. Zero means the frame carried no data
        (nothing over the whole bbox); no archive is written in that case, so
        the frame is re-attempted on a later run.
    """
    encoded = sorted(
        ((zxy_to_tileid(z, x, y), _encode_png(img)) for z, x, y, img in tiles),
        key=lambda t: t[0],
    )
    if not encoded:
        return 0

    dest = registry.frame_path(layer.id, frame_ms)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f'{dest.name}.{os.getpid()}.tmp')
    try:
        with open(tmp, 'wb') as f:
            writer = Writer(f)
            for tileid, data in encoded:
                writer.write_tile(tileid, data)
            header = {
                'tile_type': TileType.PNG,
                'tile_compression': Compression.NONE,
                'min_lon_e7': round(layer.lon_bounds[0] * 1e7),
                'min_lat_e7': round(layer.lat_bounds[0] * 1e7),
                'max_lon_e7': round(layer.lon_bounds[1] * 1e7),
                'max_lat_e7': round(layer.lat_bounds[1] * 1e7),
            }
            writer.finalize(header, {'attribution': layer.attribution, 'frame_ms': frame_ms})
        # nginx direct-serves the archive as www-data — every frame must be
        # world-readable by construction (not umask-dependent), or it 403s.
        os.chmod(tmp, 0o644)
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return len(encoded)


def existing_frames(layer_id: str) -> set[int]:
    """Frame keys already on disk for a layer (epoch-ms stems of ``*.pmtiles``)."""
    directory = registry.layer_dir(layer_id)
    if not directory.is_dir():
        return set()
    frames: set[int] = set()
    for path in directory.glob('*.pmtiles'):
        try:
            frames.add(int(path.stem))
        except ValueError:
            continue
    return frames


def select_expired(frame_keys: Iterable[int], now_ms: int, retention_days: int) -> list[int]:
    """Frame keys older than the retention window (pure — testable without disk).

    Args:
        frame_keys: Candidate frame keys (epoch-ms).
        now_ms: Current time in epoch-ms.
        retention_days: The rolling-archive window in days.

    Returns:
        Expired keys, ascending.
    """
    cutoff = now_ms - retention_days * DAY_MS
    return sorted(k for k in frame_keys if k < cutoff)


def prune(layer: RasterLayer, now_ms: int) -> list[int]:
    """Delete frames older than the layer's retention window.

    Pruned before fetching so a full disk can't wedge capture.

    Args:
        layer: The layer to prune.
        now_ms: Current time in epoch-ms.

    Returns:
        The frame keys removed.
    """
    expired = select_expired(existing_frames(layer.id), now_ms, layer.retention_days)
    for key in expired:
        registry.frame_path(layer.id, key).unlink(missing_ok=True)
    return expired


def frame_span(layer_id: str) -> tuple[int, int, int]:
    """Archive summary for a layer: ``(count, oldest_ms, newest_ms)``.

    Used by the ``/data`` freshness probe. ``(0, 0, 0)`` when empty.
    """
    frames = existing_frames(layer_id)
    if not frames:
        return 0, 0, 0
    return len(frames), min(frames), max(frames)
