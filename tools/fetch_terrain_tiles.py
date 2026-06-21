"""Download Mapzen Terrarium PNG tiles into an MBTiles archive.

Source: ``s3://elevation-tiles-prod/terrarium/`` (AWS Open Data, anonymous reads).
PNGs encode elevation per pixel as ``(R*256 + G + B/256) - 32768`` meters;
MapLibre reads this natively via ``raster-dem`` with ``encoding: 'terrarium'``.

Pipeline: this script writes MBTiles. ``pmtiles convert`` packs that into the
single ``.pmtiles`` archive Flask serves.

Concurrency model: producer pushes ``(z, x, y)`` triples into a work queue;
``--concurrency`` workers fetch via a shared ``httpx.AsyncClient`` (which pools
HTTPS connections); a single writer drains the result queue and batches
``INSERT OR IGNORE`` into SQLite. Backpressure both ways via bounded queues.

Resume: existing keys are loaded per-zoom just before fetching that zoom, so a
~50M-tile NA z13 run doesn't need to hold every key in memory at once.

MBTiles uses TMS Y origin (bottom-left), inverted from the XYZ Y origin
(top-left) used by the slippy-map source. We flip on write.
"""

import asyncio
import random
import sqlite3
import sys
import time
from pathlib import Path

import click
import httpx

from tools.precache import count_tiles, tiles_for_bbox
from tools.regions import REGIONS

TERRARIUM_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'
USER_AGENT = 'gps-dashboard/1.0 (pmormr@gmail.com)'
TERRARIUM_MAX_ZOOM = 15


def xyz_to_tms_y(z: int, y: int) -> int:
    """Convert XYZ Y (top-left origin) to MBTiles TMS Y (bottom-left origin)."""
    return (1 << z) - 1 - y


def humanize_bytes(n: float) -> str:
    """Render a byte count with the largest fitting unit."""
    for unit, scale in (('TB', 1e12), ('GB', 1e9), ('MB', 1e6), ('KB', 1e3)):
        if n >= scale:
            return f'{n / scale:.2f} {unit}'
    return f'{n:.0f} B'


def parse_zoom(zoom_str: str) -> list[int]:
    """Parse ``"0-13"`` or ``"12"`` into an inclusive integer range."""
    if '-' in zoom_str:
        lo, hi = zoom_str.split('-', 1)
        return list(range(int(lo), int(hi) + 1))
    return [int(zoom_str)]


def init_mbtiles(
    conn: sqlite3.Connection,
    bbox: tuple[float, float, float, float],
    minzoom: int,
    maxzoom: int,
) -> None:
    """Create the MBTiles 1.3 schema and seed metadata. Idempotent."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tiles (
            zoom_level INTEGER NOT NULL,
            tile_column INTEGER NOT NULL,
            tile_row INTEGER NOT NULL,
            tile_data BLOB NOT NULL,
            PRIMARY KEY (zoom_level, tile_column, tile_row)
        );
        CREATE TABLE IF NOT EXISTS metadata (
            name TEXT PRIMARY KEY,
            value TEXT
        );
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        """
    )
    meta = {
        'name': 'Mapzen Terrarium',
        'format': 'png',
        'bounds': f'{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}',
        'minzoom': str(minzoom),
        'maxzoom': str(maxzoom),
        'type': 'baselayer',
        'description': (
            'Terrarium-encoded elevation tiles from '
            's3://elevation-tiles-prod/terrarium/'
        ),
    }
    conn.executemany(
        'INSERT OR REPLACE INTO metadata(name, value) VALUES (?, ?)',
        meta.items(),
    )
    conn.commit()


def existing_xyz_keys_for_zoom(conn: sqlite3.Connection, z: int) -> set[tuple[int, int]]:
    """Return the set of ``(x, xyz_y)`` already present for one zoom level.

    Stored as TMS Y in MBTiles; we flip back to XYZ Y so callers can compare
    against tiles yielded by ``tiles_for_bbox``.
    """
    rows = conn.execute(
        'SELECT tile_column, tile_row FROM tiles WHERE zoom_level = ?',
        (z,),
    )
    return {(x, xyz_to_tms_y(z, tms_y)) for x, tms_y in rows}


async def fetch_tile(
    client: httpx.AsyncClient,
    z: int,
    x: int,
    y: int,
    retries: int = 4,
) -> tuple[int, int, int, bytes | None, str]:
    """Fetch one tile.

    Returns ``(z, x, y, body | None, status)``. Status is one of:
        ``'ok'``       — 200 with PNG body
        ``'missing'``  — 404 (legitimate gap in some arctic / mid-ocean tiles)
        ``'error'``    — exhausted retries on 5xx / connection error, or 4xx ≠ 404
    """
    url = TERRARIUM_URL.format(z=z, x=x, y=y)
    delay = 1.0
    for _attempt in range(retries):
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return z, x, y, resp.content, 'ok'
            if resp.status_code == 404:
                return z, x, y, None, 'missing'
            if not (500 <= resp.status_code < 600):
                return z, x, y, None, 'error'
        except httpx.RequestError:
            pass
        await asyncio.sleep(delay)
        delay = min(30.0, delay * 2)
    return z, x, y, None, 'error'


class Stats:
    """Running counters for progress reporting and the final summary."""

    def __init__(self) -> None:
        self.fetched = 0
        self.bytes = 0
        self.missing = 0
        self.errors = 0
        self.start = time.monotonic()

    @property
    def done(self) -> int:
        """Total result items processed (every fetch resolves to exactly one)."""
        return self.fetched + self.missing + self.errors

    def line(self, total: int) -> str:
        """Render a one-line progress snapshot."""
        elapsed = max(1e-3, time.monotonic() - self.start)
        rate = self.fetched / elapsed
        eta_s = (total - self.done) / rate if rate > 0 else 0
        return (
            f'  {self.done:>10,} / {total:,}  '
            f'fetched={self.fetched:,}  404={self.missing:,}  err={self.errors:,}  '
            f'{humanize_bytes(self.bytes):>10}  '
            f'{rate:>5.0f} req/s  '
            f'ETA {eta_s / 60:.1f}m'
        )


def _flush(conn: sqlite3.Connection, batch: list[tuple[int, int, int, bytes]]) -> None:
    """Commit a batch of (z, x, tms_y, body) rows. Clears the batch in place."""
    if not batch:
        return
    conn.executemany(
        'INSERT OR IGNORE INTO tiles(zoom_level, tile_column, tile_row, tile_data) '
        'VALUES (?, ?, ?, ?)',
        batch,
    )
    conn.commit()
    batch.clear()


async def run_fetch(
    tiles: list[tuple[int, int, int]],
    conn: sqlite3.Connection,
    concurrency: int,
    progress_every: float = 10.0,
) -> Stats:
    """Run the worker pool over ``tiles`` and write fetched bodies into ``conn``.

    Args:
        tiles: List of ``(z, x, y)`` triples in XYZ orientation to fetch.
        conn: Open MBTiles SQLite connection.
        concurrency: Number of worker coroutines.
        progress_every: Seconds between progress lines / batch flushes.

    Returns:
        Final ``Stats`` object.
    """
    work_q: asyncio.Queue = asyncio.Queue(maxsize=concurrency * 4)
    result_q: asyncio.Queue = asyncio.Queue(maxsize=concurrency * 4)
    stats = Stats()
    total = len(tiles)

    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )

    async with httpx.AsyncClient(
        headers={'User-Agent': USER_AGENT},
        limits=limits,
        timeout=httpx.Timeout(30.0, connect=15.0),
    ) as client:

        async def producer() -> None:
            for triple in tiles:
                await work_q.put(triple)
            for _ in range(concurrency):
                await work_q.put(None)

        async def worker() -> None:
            while True:
                item = await work_q.get()
                if item is None:
                    await result_q.put(None)
                    return
                z, x, y = item
                res = await fetch_tile(client, z, x, y)
                await result_q.put(res)

        async def writer() -> None:
            sentinels = 0
            batch: list[tuple[int, int, int, bytes]] = []
            last_progress = time.monotonic()
            while sentinels < concurrency:
                item = await result_q.get()
                if item is None:
                    sentinels += 1
                    continue
                z, x, y, body, status = item
                if status == 'ok' and body is not None:
                    batch.append((z, x, xyz_to_tms_y(z, y), body))
                    stats.fetched += 1
                    stats.bytes += len(body)
                elif status == 'missing':
                    stats.missing += 1
                else:
                    stats.errors += 1
                now = time.monotonic()
                if now - last_progress >= progress_every:
                    _flush(conn, batch)
                    click.echo(stats.line(total), err=True)
                    last_progress = now
            _flush(conn, batch)

        await asyncio.gather(
            producer(),
            writer(),
            *(worker() for _ in range(concurrency)),
        )

    return stats


async def dry_run(
    bbox: tuple[float, float, float, float],
    zoom_levels: list[int],
    concurrency: int,
    samples_per_zoom: int,
) -> None:
    """Probe a random sample at each zoom; project per-zoom and total archive size."""
    rng = random.Random(0xCAFE)
    samples: list[tuple[int, int, int]] = []
    zoom_tile_counts: dict[int, int] = {}
    for z in zoom_levels:
        for_zoom = list(tiles_for_bbox(*bbox, z))
        zoom_tile_counts[z] = len(for_zoom)
        k = min(samples_per_zoom, len(for_zoom))
        samples.extend(rng.sample(for_zoom, k))

    limits = httpx.Limits(max_connections=concurrency)
    by_zoom: dict[int, list[int]] = {}
    async with httpx.AsyncClient(
        headers={'User-Agent': USER_AGENT},
        limits=limits,
        timeout=httpx.Timeout(30.0, connect=15.0),
    ) as client:

        async def probe(z: int, x: int, y: int) -> None:
            res = await fetch_tile(client, z, x, y, retries=2)
            if res[4] == 'ok' and res[3] is not None:
                by_zoom.setdefault(z, []).append(len(res[3]))

        # Fan out the sample. The sample is small (a few thousand at most),
        # so a single gather is fine.
        sem = asyncio.Semaphore(concurrency)

        async def one(z: int, x: int, y: int) -> None:
            async with sem:
                await probe(z, x, y)

        await asyncio.gather(*(one(z, x, y) for z, x, y in samples))

    click.echo('')
    click.echo(
        f'Sample: up to {samples_per_zoom} tiles/zoom, bbox '
        f'({bbox[0]}, {bbox[1]}, {bbox[2]}, {bbox[3]})'
    )
    click.echo('')
    click.echo(f'  {"zoom":>4} {"tiles":>14} {"samples":>8} {"mean KB":>10} {"projected":>14}')
    click.echo(f'  {"-" * 4} {"-" * 14} {"-" * 8} {"-" * 10} {"-" * 14}')
    grand_total = 0
    for z in zoom_levels:
        n = zoom_tile_counts[z]
        sizes = by_zoom.get(z, [])
        mean_b = sum(sizes) / len(sizes) if sizes else 0
        proj_b = int(mean_b * n)
        grand_total += proj_b
        click.echo(
            f'  {z:>4} {n:>14,} {len(sizes):>8} {mean_b / 1024:>10.1f} '
            f'{humanize_bytes(proj_b):>14}'
        )
    click.echo(f'  {"-" * 4} {"-" * 14} {"-" * 8} {"-" * 10} {"-" * 14}')
    click.echo(f'  Projected archive size: {humanize_bytes(grand_total)}')


def _resolve_bbox(
    bbox: str | None, region: str | None
) -> tuple[tuple[float, float, float, float], str | None]:
    """Resolve --bbox / --region to a 4-float tuple. Returns (bbox, region_name)."""
    if bool(bbox) == bool(region):
        raise click.UsageError('Specify exactly one of --bbox or --region.')
    if region:
        return REGIONS[region].bbox, region
    try:
        parts = [float(p) for p in bbox.split(',')]  # type: ignore[union-attr]
    except ValueError as e:
        raise click.BadParameter(
            '--bbox must be "min_lon,min_lat,max_lon,max_lat"'
        ) from e
    if len(parts) != 4:
        raise click.BadParameter('--bbox must have exactly four comma-separated floats')
    return (parts[0], parts[1], parts[2], parts[3]), None


@click.command()
@click.option(
    '--bbox',
    default=None,
    help='Bounding box: "min_lon,min_lat,max_lon,max_lat"',
)
@click.option(
    '--region',
    default=None,
    type=click.Choice(sorted(REGIONS)),
    help='Named region (see tools/regions.py)',
)
@click.option('--zoom', default='0-13', show_default=True, help='Zoom range, e.g. 0-13 or 12')
@click.option(
    '--output',
    '-o',
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help='Output MBTiles path (required for a real fetch; omit for --dry-run)',
)
@click.option(
    '--concurrency',
    default=32,
    show_default=True,
    type=int,
    help='Max in-flight HTTPS requests',
)
@click.option(
    '--dry-run',
    'dry_run_flag',
    is_flag=True,
    help='Probe a random sample, project archive size, do not write.',
)
@click.option(
    '--samples-per-zoom',
    default=64,
    show_default=True,
    type=int,
    help='Probe sample size per zoom level (dry-run only)',
)
@click.option(
    '--yes',
    '-y',
    'assume_yes',
    is_flag=True,
    help='Skip the interactive Proceed? prompt (for nohup / scripted runs).',
)
def main(
    bbox: str | None,
    region: str | None,
    zoom: str,
    output: Path | None,
    concurrency: int,
    dry_run_flag: bool,
    samples_per_zoom: int,
    assume_yes: bool,
) -> None:
    """Download Mapzen Terrarium PNG tiles into an MBTiles archive."""
    selected_bbox, region_name = _resolve_bbox(bbox, region)

    zoom_levels = parse_zoom(zoom)
    if min(zoom_levels) < 0 or max(zoom_levels) > TERRARIUM_MAX_ZOOM:
        raise click.BadParameter(
            f'--zoom must be in 0..{TERRARIUM_MAX_ZOOM} (Mapzen Terrarium tops out there)'
        )

    total = count_tiles(selected_bbox, zoom_levels)

    click.echo('Source:       Mapzen Terrarium (s3://elevation-tiles-prod/terrarium/)')
    bbox_label = f'{selected_bbox}' + (f'  [{region_name}]' if region_name else '')
    click.echo(f'Bbox:         {bbox_label}')
    click.echo(f'Zoom levels:  {zoom_levels[0]}–{zoom_levels[-1]}')
    click.echo(f'Tile count:   {total:,}')
    click.echo(f'Concurrency:  {concurrency}')

    if dry_run_flag:
        asyncio.run(dry_run(selected_bbox, zoom_levels, concurrency, samples_per_zoom))
        return

    if output is None:
        raise click.UsageError('--output is required for a real fetch (or use --dry-run).')
    output.parent.mkdir(parents=True, exist_ok=True)
    click.echo(f'Output:       {output}')
    if not assume_yes:
        click.confirm('Proceed?', abort=True)

    conn = sqlite3.connect(output, isolation_level=None)
    try:
        init_mbtiles(conn, selected_bbox, zoom_levels[0], zoom_levels[-1])
        remaining: list[tuple[int, int, int]] = []
        for z in zoom_levels:
            existing = existing_xyz_keys_for_zoom(conn, z)
            for _, x, y in tiles_for_bbox(*selected_bbox, z):
                if (x, y) not in existing:
                    remaining.append((z, x, y))
        skipped = total - len(remaining)
        click.echo(f'Resume:       {skipped:,} tiles already present, will skip')
        click.echo(f'To fetch:     {len(remaining):,} tiles')
        if not remaining:
            click.echo('Nothing to do.')
            return
        stats = asyncio.run(run_fetch(remaining, conn, concurrency))
        click.echo('')
        click.echo(f'Done. {stats.line(len(remaining))}')
    finally:
        conn.close()


if __name__ == '__main__':
    try:
        main(standalone_mode=False)
    except click.exceptions.Abort as e:
        # standalone_mode=False lets us tell Ctrl+C apart from "n" at confirm:
        # Ctrl+C → Abort chained from KeyboardInterrupt; 'n' → bare Abort.
        if isinstance(e.__cause__, KeyboardInterrupt):
            click.echo('\nInterrupted.', err=True)
            sys.exit(130)
        click.echo('Aborted.', err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo('\nInterrupted.', err=True)
        sys.exit(130)
    except click.exceptions.ClickException as e:
        e.show()
        sys.exit(e.exit_code)
