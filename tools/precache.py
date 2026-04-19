"""Pre-download OSM tiles for offline use."""

import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import click
import requests

TILE_CACHE_DIR = Path(
    os.environ.get('GPS_TILE_CACHE_DIR', Path.home() / '.cache' / 'gps-dashboard' / 'tiles')
)

OSM_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
USER_AGENT = 'gps-dashboard/1.0 (pmormr@gmail.com)'

# (min_lon, min_lat, max_lon, max_lat)
REGIONS = {
    'arizona':      (-114.82, 31.33, -109.04, 37.00),
    'california':   (-124.41, 32.53, -114.13, 42.01),
    'colorado':     (-109.06, 36.99, -102.04, 41.00),
    'idaho':        (-117.24, 41.99, -111.04, 49.00),
    'montana':      (-116.05, 44.36, -104.04, 49.00),
    'nevada':       (-120.00, 35.00, -114.03, 42.00),
    'new_mexico':   (-109.05, 31.33, -103.00, 37.00),
    'oregon':       (-124.57, 41.99, -116.46, 46.26),
    'utah':         (-114.05, 36.99, -109.04, 42.00),
    'washington':   (-124.73, 45.54, -116.92, 49.00),
    'wyoming':      (-111.06, 40.99, -104.05, 45.01),
}


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


def download_tile(z, x, y):
    cache_path = TILE_CACHE_DIR / str(z) / str(x) / f'{y}.png'
    if cache_path.exists():
        return 'cached'
    try:
        resp = requests.get(
            OSM_URL.format(z=z, x=x, y=y),
            timeout=10,
            headers={'User-Agent': USER_AGENT},
        )
        resp.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(resp.content)
        time.sleep(0.05)  # respect OSM rate limits
        return 'downloaded'
    except Exception as e:
        return f'error: {e}'


def parse_zoom(zoom_str: str) -> list[int]:
    if '-' in zoom_str:
        lo, hi = zoom_str.split('-', 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(zoom_str)]


@click.command()
@click.option('--region', default=None, help='Named region (see --list-regions)')
@click.option('--bbox', default=None, help='Bounding box: "min_lon,min_lat,max_lon,max_lat"')
@click.option('--zoom', default='8-14', show_default=True, help='Zoom range, e.g. 8-14 or 12')
@click.option('--list-regions', 'list_regions', is_flag=True, help='List available regions')
@click.option('--workers', default=4, show_default=True, help='Parallel download workers')
def main(region, bbox, zoom, list_regions, workers):
    """Pre-download OSM map tiles for offline use."""
    if list_regions:
        click.echo('Available regions:')
        for name, (min_lon, min_lat, max_lon, max_lat) in sorted(REGIONS.items()):
            click.echo(f'  {name:<15} ({min_lat:.1f}°N–{max_lat:.1f}°N, {min_lon:.1f}°–{max_lon:.1f}°)')
        return

    if region and bbox:
        raise click.UsageError('Specify --region or --bbox, not both.')
    if not region and not bbox:
        raise click.UsageError('Specify --region or --bbox.')

    if region:
        region = region.lower().replace(' ', '_')
        if region not in REGIONS:
            raise click.BadParameter(f"Unknown region '{region}'. Use --list-regions to see options.")
        selected_bbox = REGIONS[region]
    else:
        try:
            parts = [float(p) for p in bbox.split(',')]
            if len(parts) != 4:
                raise ValueError
            selected_bbox = tuple(parts)
        except ValueError:
            raise click.BadParameter('--bbox must be "min_lon,min_lat,max_lon,max_lat"')

    zoom_levels = parse_zoom(zoom)
    total = count_tiles(selected_bbox, zoom_levels)

    click.echo(f'Zoom levels: {zoom_levels[0]}–{zoom_levels[-1]}')
    click.echo(f'Cache dir:   {TILE_CACHE_DIR}')
    click.echo(f'Tiles to download: ~{total:,} (skips already cached)')
    click.confirm('Proceed?', abort=True)

    all_tiles = [
        tile
        for z in zoom_levels
        for tile in tiles_for_bbox(*selected_bbox, z)
    ]

    downloaded = cached = errors = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(download_tile, z, x, y): (z, x, y) for z, x, y in all_tiles}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            if result == 'downloaded':
                downloaded += 1
            elif result == 'cached':
                cached += 1
            else:
                errors += 1
            if i % 100 == 0 or i == total:
                click.echo(f'\r  {i:,}/{total:,} tiles  ({downloaded} downloaded, {cached} cached, {errors} errors)', nl=False)

    click.echo(f'\nDone. {downloaded} downloaded, {cached} already cached, {errors} errors.')


if __name__ == '__main__':
    main()
