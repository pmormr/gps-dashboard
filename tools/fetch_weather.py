"""Capture radar (and future weather layers) into the rolling PMTiles archive.

Timer-driven oneshot (``deploy/weather-fetch.timer``), the ``gps-db-backup`` /
``gps-drone-sync`` shape. Each run:

1. Preflights the upstream service; unreachable (off-grid) is a clean no-op,
   exit 0 — only *filling* the archive needs a link.
2. Prunes frames past the retention window *before* fetching, so a full disk
   can't wedge capture.
3. Enumerates upstream frames, diffs against the on-disk archive, and captures
   each missing frame: the 2-export native mosaic → sparse tile pyramid →
   one per-frame PMTiles archive (written atomically).

Dedup by frame key makes a tick that finds nothing new a no-op. The archive is
ephemeral and rebuild-worthless — deliberately outside the DB backup path.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import httpx

from common.cli import run_cli
from weather import archive, mosaic, vector
from weather.mosaic import MasterGrid, StripSpec
from weather.registry import RASTER_LAYERS, VECTOR_LAYERS, RasterLayer, VectorLayer
from weather.source import ImageServerClient, SourceError


def _now_ms() -> int:
    """Current time in epoch-ms (PPS-disciplined wall clock on the Pi)."""
    return int(datetime.now(UTC).timestamp() * 1000)


def capture_frame(
    client: ImageServerClient,
    layer: RasterLayer,
    grid: MasterGrid,
    specs: list[StripSpec],
    frame_ms: int,
) -> int:
    """Fetch, mosaic, tile, and pack one frame. Returns the tile count."""
    strips = [client.export(spec.bbox, spec.size, frame_ms) for spec in specs]
    master = mosaic.assemble_master(grid, specs, strips)
    return archive.pack_frame(layer, frame_ms, mosaic.pyramid_tiles(master, grid, layer))


def fetch_layer(layer: RasterLayer, *, limit: int | None = None) -> int:
    """Capture all (or the newest ``limit``) missing frames for one layer.

    Args:
        layer: The raster layer to capture.
        limit: Cap on frames captured this run (newest first); None = all missing.

    Returns:
        The number of frames captured.
    """
    grid = mosaic.master_grid(layer)
    specs = mosaic.strip_specs(grid, layer.export_max_h)
    now = _now_ms()
    with ImageServerClient(layer.service_url) as client:
        if not client.reachable():
            print(f'[{layer.id}] upstream unreachable — no-op (off-grid)')
            return 0
        pruned = archive.prune(layer, now)
        if pruned:
            print(f'[{layer.id}] pruned {len(pruned)} expired frame(s)')
        available = client.list_frames(layer.subset)
        have = archive.existing_frames(layer.id)
        missing = [f for f in sorted(available, reverse=True) if f not in have]
        if limit is not None:
            missing = missing[:limit]
        print(
            f'[{layer.id}] upstream={len(available)} on-disk={len(have)} to-capture={len(missing)}'
        )
        captured = 0
        for frame_ms in missing:
            try:
                n = capture_frame(client, layer, grid, specs, frame_ms)
            except (SourceError, OSError) as exc:
                print(f'[{layer.id}] frame {frame_ms} FAILED: {exc}')
                continue
            if n == 0:
                print(f'[{layer.id}] frame {frame_ms} empty (no echo) — skipped')
                continue
            captured += 1
            print(f'[{layer.id}] captured frame {frame_ms}: {n} tiles')
        print(f'[{layer.id}] done — captured {captured}/{len(missing)}')
        return captured


def fetch_vector(layer: VectorLayer) -> int:
    """Fetch + store one vector layer's latest GeoJSON snapshot.

    A network failure is an off-grid no-op (like the raster preflight): the last
    stored snapshot stays and the run moves on.

    Args:
        layer: The vector layer to capture.

    Returns:
        The feature count stored, or 0 on an offline/failed fetch.
    """
    try:
        n = vector.fetch_and_store(layer)
    except (httpx.HTTPError, OSError) as exc:
        print(f'[{layer.id}] fetch failed (off-grid?): {exc}')
        return 0
    print(f'[{layer.id}] stored {n} features')
    return n


def main() -> int:
    """CLI entrypoint for the weather-fetch oneshot (all layers, or one by id)."""
    all_ids = sorted({*RASTER_LAYERS, *VECTOR_LAYERS})
    parser = argparse.ArgumentParser(
        description='Capture weather layers into the local archive (all layers by default).'
    )
    parser.add_argument(
        '--layer',
        default=None,
        choices=all_ids,
        help='Capture only this layer (default: every registered layer).',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=None,
        help='Max raster frames to capture this run, newest first (default: all missing).',
    )
    args = parser.parse_args()
    targets = [args.layer] if args.layer else all_ids
    for layer_id in targets:
        if layer_id in RASTER_LAYERS:
            fetch_layer(RASTER_LAYERS[layer_id], limit=args.limit)
        else:
            fetch_vector(VECTOR_LAYERS[layer_id])
    return 0


if __name__ == '__main__':
    run_cli(main)
