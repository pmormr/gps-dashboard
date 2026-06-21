"""Pre-download map tiles for offline use."""

import math
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
import requests

from api.tile_layers import LAYERS
from common.cli import run_click
from tools.regions import REGIONS

TILE_CACHE_DIR = Path(
    os.environ.get('GPS_TILE_CACHE_DIR', Path.home() / '.cache' / 'gps-dashboard' / 'tiles')
)

USER_AGENT = 'gps-dashboard/1.0 (pmormr@gmail.com)'


class RateLimiter:
    """Global pacer: caps request *starts* to at most `rate` per second.

    Shared across all worker threads, so the effective request rate is
    independent of `--workers`. A rate of 0 disables limiting.
    """

    def __init__(self, rate: float) -> None:
        """Initialize the limiter.

        Args:
            rate: Maximum request starts per second across all threads. 0 or
                negative disables throttling.
        """
        self._min_interval = 1.0 / rate if rate > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        """Block until the caller is allowed to issue its next request."""
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                time.sleep(delay)
                self._next += self._min_interval
            else:
                self._next = now + self._min_interval

def lat_lon_to_tile(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    y = int((1 - math.asinh(math.tan(lat_r)) / math.pi) / 2 * n)
    return x, y


def tiles_for_bbox(min_lon, min_lat, max_lon, max_lat, z):
    x_min, y_max = lat_lon_to_tile(min_lat, min_lon, z)
    x_max, y_min = lat_lon_to_tile(max_lat, max_lon, z)
    for x in range(x_min, x_max + 1):
        for y in range(y_min, y_max + 1):
            yield z, x, y


def count_tiles(bbox, zoom_levels):
    return sum(
        1
        for z in zoom_levels
        for _ in tiles_for_bbox(*bbox, z)
    )


def _atomic_write(path, data):
    # Write to a thread-unique sibling, then rename. Same-filesystem rename is
    # atomic, so a Ctrl+C or crash never leaves a half-written tile or etag.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{threading.get_ident()}.tmp')
    if isinstance(data, str):
        tmp.write_text(data)
    else:
        tmp.write_bytes(data)
    tmp.replace(path)


def download_tile(layer, z, x, y, limiter):
    cache_path = TILE_CACHE_DIR / layer / str(z) / str(x) / f'{y}.png'
    etag_path = cache_path.with_suffix('.etag')
    url = LAYERS[layer]['url'].format(z=z, x=x, y=y)

    if cache_path.exists():
        if etag_path.exists():
            return 'cached'
        # Backfill missing ETag via HEAD request
        try:
            limiter.wait()
            resp = requests.head(url, timeout=10, headers={'User-Agent': USER_AGENT})
            etag = resp.headers.get('ETag')
            if etag:
                _atomic_write(etag_path, etag)
        except Exception:
            pass
        return 'etag-added'

    try:
        limiter.wait()
        resp = requests.get(url, timeout=10, headers={'User-Agent': USER_AGENT})
        resp.raise_for_status()
        _atomic_write(cache_path, resp.content)
        etag = resp.headers.get('ETag')
        if etag:
            _atomic_write(etag_path, etag)
        return 'downloaded'
    except Exception as e:
        return f'error: {e}'


def get_current_location() -> tuple[float, float]:
    db_path = Path(os.environ.get('GPS_DB_PATH', Path.home() / 'gps_history.db'))
    if not db_path.exists():
        raise click.ClickException(f'Database not found: {db_path}')
    conn = sqlite3.connect(db_path)
    row = conn.execute('SELECT lat, lon FROM gps_points ORDER BY timestamp DESC LIMIT 1').fetchone()
    conn.close()
    if row is None:
        raise click.ClickException('No GPS data in database.')
    return row[0], row[1]


def bbox_from_center(lat: float, lon: float, radius_km: float) -> tuple:
    delta_lat = radius_km / 111.0
    delta_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
    return (lon - delta_lon, lat - delta_lat, lon + delta_lon, lat + delta_lat)


def parse_zoom(zoom_str: str) -> list[int]:
    if '-' in zoom_str:
        lo, hi = zoom_str.split('-', 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(zoom_str)]


@click.command()
@click.option('--layer', default='usgs', show_default=True, type=click.Choice(sorted(LAYERS)), help='Tile layer to cache')
@click.option('--region', default=None, help='Named region (see --list-regions)')
@click.option('--bbox', default=None, help='Bounding box: "min_lon,min_lat,max_lon,max_lat"')
@click.option('--local', 'use_local', is_flag=True, help='Cache tiles around current GPS position')
@click.option('--radius', default=50.0, show_default=True, type=float, help='Radius in km (used with --local)')
@click.option('--zoom', default='8-14', show_default=True, help='Zoom range, e.g. 8-14 or 12')
@click.option('--list-regions', 'list_regions', is_flag=True, help='List available regions')
@click.option('--workers', default=4, show_default=True, help='Parallel download workers')
@click.option('--rate', default=20.0, show_default=True, type=float, help='Max requests/sec across all workers (0 = unlimited)')
def main(layer, region, bbox, use_local, radius, zoom, list_regions, workers, rate):
    """Pre-download map tiles for offline use."""
    if list_regions:
        click.echo('Available regions:')
        for name, r in sorted(REGIONS.items()):
            click.echo(f'  {name:<15} ({r.min_lat:.1f}°N–{r.max_lat:.1f}°N, {r.min_lon:.1f}°–{r.max_lon:.1f}°)')
        return

    sources = sum([bool(region), bool(bbox), use_local])
    if sources > 1:
        raise click.UsageError('Specify only one of --region, --bbox, or --local.')
    if sources == 0:
        raise click.UsageError('Specify --region, --bbox, or --local.')

    if region:
        region = region.lower().replace(' ', '_')
        if region not in REGIONS:
            raise click.BadParameter(f"Unknown region '{region}'. Use --list-regions to see options.")
        selected_bbox = REGIONS[region].bbox
    elif bbox:
        try:
            parts = [float(p) for p in bbox.split(',')]
            if len(parts) != 4:
                raise ValueError
            selected_bbox = tuple(parts)
        except ValueError:
            raise click.BadParameter('--bbox must be "min_lon,min_lat,max_lon,max_lat"')
    else:
        lat, lon = get_current_location()
        selected_bbox = bbox_from_center(lat, lon, radius)
        click.echo(f'Current location: {lat:.5f}, {lon:.5f}  (radius {radius} km)')

    zoom_levels = parse_zoom(zoom)
    max_zoom = LAYERS[layer]['max_zoom']
    if max(zoom_levels) > max_zoom:
        raise click.BadParameter(f"--zoom exceeds max zoom for layer '{layer}' ({max_zoom})")
    total = count_tiles(selected_bbox, zoom_levels)

    click.echo(f'Layer:       {layer}')
    click.echo(f'Zoom levels: {zoom_levels[0]}–{zoom_levels[-1]}')
    click.echo(f'Cache dir:   {TILE_CACHE_DIR / layer}')
    click.echo(f'Tiles to download: ~{total:,} (skips already cached)')
    if rate > 0:
        eta_s = total / rate
        click.echo(f'Rate limit:  {rate:g} req/s  (worst-case ETA ~{eta_s / 3600:.1f}h for a full run)')
    else:
        click.echo('Rate limit:  unlimited')
    click.confirm('Proceed?', abort=True)

    limiter = RateLimiter(rate)

    all_tiles = [
        tile
        for z in zoom_levels
        for tile in tiles_for_bbox(*selected_bbox, z)
    ]

    downloaded = cached = etag_added = errors = 0
    interrupted = False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_tile, layer, z, x, y, limiter): (z, x, y) for z, x, y in all_tiles}
        try:
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                if result == 'downloaded':
                    downloaded += 1
                elif result == 'cached':
                    cached += 1
                elif result == 'etag-added':
                    etag_added += 1
                else:
                    errors += 1
                if i % 100 == 0 or i == total:
                    click.echo(f'\r  {i:,}/{total:,} tiles  ({downloaded} downloaded, {cached} cached, {etag_added} etag-added, {errors} errors)', nl=False)
        except KeyboardInterrupt:
            interrupted = True
            for f in futures:
                f.cancel()

    if interrupted:
        click.echo(f'\nInterrupted. {downloaded} downloaded, {cached} already cached, {etag_added} etags backfilled, {errors} errors.')
        sys.exit(130)
    else:
        click.echo(f'\nDone. {downloaded} downloaded, {cached} already cached, {etag_added} etags backfilled, {errors} errors.')


if __name__ == '__main__':
    run_click(main)
