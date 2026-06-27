"""Import DJI drone telemetry into the drone tier of ``gps_history.db``.

Offline batch importer (see ``.claude/modules/drone.md``) — the fourth
data stream beyond GPS / sensors / OBD. Each DJI clip embeds a protobuf telemetry
track (``dvtm_*``) that ExifTool ≥13.x decodes to per-frame UTC time + full-precision
lat/lon + absolute altitude. This tool discovers telemetry-bearing clips, extracts
and thins their tracks, and loads one ``drone_flights`` row + its
``drone_track_points`` per clip, idempotent on the ``(model_code, first_fix_utc)``
natural key.

Two extraction backends:

* ``--source DIR`` runs the host's own ``exiftool`` over a local directory — the
  laptop manual path (attached SD/drive), or any host with ExifTool ≥13.x.
* ``--ssh HOST --remote-dir DIR`` runs ExifTool inside a transient container on the
  remote host (``docker run --rm -v DIR:/data:ro <image>``). The video bytes stay
  local to that host (the NAS); only the telemetry text crosses the network. This is
  the home-sync path: the Pi drives the NAS, which holds the canonical media.

Extraction is single-core CPU-bound per clip, so clips fan out across ``--jobs``
workers; DB writes are serialized on the main thread (single SQLite writer).

Two write sinks pair with the backends: a local SQLite write (default — the Pi-side
home sync) or ``--api URL``, which POSTs each thinned flight to a running dashboard
(``POST /api/drone/flights``). The API sink is the off-grid laptop path — scan an
attached card and ship the flights to the Pi over the van LAN, no internet — and the
server dedups on the same natural key, so it is idempotent without ``--incremental``.

Examples::

    # Home sync: Pi extracts on the NAS in a container, writes its local DB
    uv run tools/import_drone.py --ssh rex-nas --remote-dir /volume2/misc/Drone

    # Laptop manual path: scan an attached card, POST flights to the Pi over the LAN
    uv run tools/import_drone.py --source /Volumes/DJI_SD --api http://192.168.42.178:5000

    uv run tools/import_drone.py --ssh rex-nas --remote-dir /volume2/misc/Drone \\
        --limit 2 --dry-run        # discover + extract, no write
"""

from __future__ import annotations

import argparse
import shlex
import sqlite3
import subprocess
import sys
from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import requests

from api.db import canonical_timestamp, get_connection, init_db, now_canonical
from common.cli import run_cli
from processor.simplify import reumann_witkam

DEFAULT_IMAGE = 'exiftool:13x'
DEFAULT_EPSILON = 2.5
DEFAULT_JOBS = 4
VIDEO_EXTS = ('mp4', 'mov')

# Fallback model names by code, for the rare clip whose Encoder tag is absent but
# whose Category still names the dvtm model. Codes seen on the real store
# (2026-06-20): FC9313 Mini 5 Pro, FC8485 Avata 2, FC8671 Neo.
MODEL_BY_CODE = {
    'FC9313': 'DJI Mini5Pro',
    'FC8485': 'DJI Avata2',
    'FC8671': 'DJI NEO',
}

# ExifTool format strings. The delimiter is a literal '|' (never in a DJI path or a
# numeric field). Discovery is a cheap whole-tree pass (no -ee); telemetry is the
# per-frame -ee extraction. -f emits '-' for absent tags so a per-model field gap
# yields a placeholder rather than dropping the row.
_DISCOVER_FMT = '$Directory|$FileName|$Encoder|$Category'
_TELEMETRY_FMT = '$GPSDateTime|$GPSLatitude|$GPSLongitude|$AbsoluteAltitude'
_DISCOVER_ARGS = ['-r', '-q', '-f', '-s3']
_TELEMETRY_ARGS = ['-ee', '-n', '-q', '-f', '-api', 'QuickTimeUTC=1']


@dataclass(frozen=True)
class Clip:
    """A discovered telemetry-bearing clip.

    Attributes:
        ref: The path the active backend extracts from (a local path, or a
            ``/data/...`` path inside the container).
        name: The clip's basename, stored as ``source_name`` provenance.
        media_path: The canonical media path to persist, or None when the source
            is non-canonical (e.g. an SD card) and the NAS scan will backfill it.
        model: Human model name (``DJI Avata2``).
        model_code: The ``FC####`` code — half the natural key and the style key.
    """

    ref: str
    name: str
    media_path: str | None
    model: str
    model_code: str


@dataclass(frozen=True)
class TrackPoint:
    """One thinned drone track vertex.

    Attributes:
        timestamp: Canonical ms-UTC string (same axis as ``gps_points``).
        lat: Latitude (deg).
        lon: Longitude (deg).
        abs_alt: Absolute (MSL) altitude in metres, or None.
        importance: Reumann–Witkam deviation (m) that retained the vertex.
    """

    timestamp: str
    lat: float
    lon: float
    abs_alt: float | None
    importance: float


@dataclass(frozen=True)
class Flight:
    """A parsed, thinned flight ready to load.

    Attributes:
        clip: The source clip (carries identity + media path).
        first_fix_utc: Canonical ms-UTC of the first valid fix (natural-key half).
        last_fix_utc: Canonical ms-UTC of the last valid fix.
        points: The thinned track vertices in time order.
        bbox: ``(min_lat, min_lon, max_lat, max_lon)`` over the thinned track.
    """

    clip: Clip
    first_fix_utc: str
    last_fix_utc: str
    points: list[TrackPoint]
    bbox: tuple[float, float, float, float]


# --- ExifTool date conversion --------------------------------------------------


def _to_canonical(exif_dt: str) -> str:
    """Convert an ExifTool ``GPSDateTime`` to the canonical ms-UTC storage format.

    ExifTool emits ``2026:05:28 14:17:56.017Z`` (colon date separators, space, UTC
    ``Z`` under ``-api QuickTimeUTC=1``). Reshape to ISO-8601 and normalize through
    the shared :func:`canonical_timestamp`.

    Args:
        exif_dt: An ExifTool ``GPSDateTime`` value.

    Returns:
        The timestamp as fixed-width millisecond canonical UTC text.

    Raises:
        ValueError: If the value is not a parseable ExifTool datetime.
    """
    iso = exif_dt[:10].replace(':', '-') + 'T' + exif_dt[11:]
    return canonical_timestamp(iso)


def _parse_model_code(category: str) -> str | None:
    """Extract the ``FC####`` model code from the ExifTool ``Category`` tag.

    Args:
        category: The ``Category`` value, e.g.
            ``pb_file:dvtm_AVATA2.proto;model_name:FC8485;pb_version:...``.

    Returns:
        The model code, or None if the tag carries no ``model_name``.
    """
    for part in category.split(';'):
        key, _, value = part.partition(':')
        if key.strip() == 'model_name' and value.strip():
            return value.strip()
    return None


def _classify(directory: str, name: str, encoder: str, category: str) -> Clip | None:
    """Classify one discovered file as a telemetry clip, or None to skip.

    A clip carries telemetry iff its ``Category`` names a ``dvtm_*`` protobuf. The
    controller/headset screen recordings on the store have no such Category (single
    ``avc1`` track, ``Encoder: DEFAULT ENCODING``) and are skipped here.

    Args:
        directory: The file's ``Directory`` (the backend's path space).
        name: The file's basename.
        encoder: The ``Encoder`` tag, or ``-`` when absent.
        category: The ``Category`` tag, or ``-`` when absent.

    Returns:
        A :class:`Clip` for a telemetry-bearing file, or None to skip it.
    """
    if 'dvtm' not in category:
        return None
    code = _parse_model_code(category)
    if code is None:
        return None
    model = (
        encoder if encoder not in ('-', '', 'DEFAULT ENCODING') else MODEL_BY_CODE.get(code, code)
    )
    ref = f'{directory.rstrip("/")}/{name}'
    return Clip(ref=ref, name=name, media_path=None, model=model, model_code=code)


# --- Extraction backends -------------------------------------------------------


class Extractor(ABC):
    """An ExifTool execution context (local binary, or remote container)."""

    def preflight(self) -> bool:
        """Return True if the backend's host is reachable (default: always).

        The home-sync timer fires regardless of where the van is; an unreachable
        backend (boondocking) is a clean no-op, not an error.
        """
        return True

    @abstractmethod
    def _run(self, exif_args: list[str]) -> str:
        """Run ExifTool with ``exif_args`` and return its stdout.

        Args:
            exif_args: Arguments passed to ExifTool (paths are in this backend's
                path space).

        Returns:
            ExifTool's stdout as text.
        """

    @abstractmethod
    def discover(self) -> list[Clip]:
        """Scan the source and return its telemetry-bearing clips (recordings skipped)."""

    def telemetry_lines(self, clip: Clip) -> list[str]:
        """Extract one clip's per-frame telemetry as ``GPSDateTime|lat|lon|alt`` lines.

        Args:
            clip: The clip to extract.

        Returns:
            ExifTool's per-frame output lines (non-empty).
        """
        out = self._run([*_TELEMETRY_ARGS, '-p', _TELEMETRY_FMT, clip.ref])
        return [line for line in out.splitlines() if line]

    def _discover_from(self, root: str, to_media_path) -> list[Clip]:
        """Run the discovery pass under ``root`` and classify each file.

        Args:
            root: The scan root in this backend's path space.
            to_media_path: Maps a classified ``Clip`` to its canonical media path
                (or None) before it is returned.

        Returns:
            The telemetry-bearing clips found under ``root``.
        """
        ext_args: list[str] = []
        for ext in VIDEO_EXTS:
            ext_args += ['-ext', ext]
        out = self._run([*_DISCOVER_ARGS, *ext_args, '-p', _DISCOVER_FMT, root])
        clips: list[Clip] = []
        for line in out.splitlines():
            if not line or line.count('|') != 3:
                continue
            directory, name, encoder, category = line.split('|')
            clip = _classify(directory, name, encoder, category)
            if clip is not None:
                clips.append(
                    Clip(
                        ref=clip.ref,
                        name=clip.name,
                        media_path=to_media_path(clip),
                        model=clip.model,
                        model_code=clip.model_code,
                    )
                )
        return clips


class LocalExtractor(Extractor):
    """Runs the host's own ``exiftool`` over a local directory.

    The laptop manual path: the source is treated as non-canonical (e.g. an SD
    card), so flights import with a NULL ``media_path`` for the NAS scan to backfill.
    """

    def __init__(self, source: str, exiftool: str) -> None:
        """Initialize the backend.

        Args:
            source: The local directory to scan.
            exiftool: The ExifTool executable (path or name on ``PATH``).
        """
        self._source = source
        self._exiftool = exiftool

    def _run(self, exif_args: list[str]) -> str:
        result = subprocess.run(
            [self._exiftool, *exif_args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode not in (0, 1):  # 1 = "minor" (e.g. unsupported files)
            raise RuntimeError(f'exiftool failed ({result.returncode}): {result.stderr.strip()}')
        return result.stdout

    def discover(self) -> list[Clip]:
        return self._discover_from(self._source, lambda clip: None)


class SshDockerExtractor(Extractor):
    """Runs ExifTool in a transient container on a remote host over SSH.

    The home-sync path: the remote host (the NAS) holds the canonical media and does
    the CPU-bound decode locally; only telemetry text crosses the network. The remote
    command is shell-quoted so the ExifTool ``-p`` format (``$``, ``|``) survives the
    remote shell intact.
    """

    def __init__(self, host: str, remote_dir: str, image: str) -> None:
        """Initialize the backend.

        Args:
            host: SSH destination (an ``ssh`` config alias or ``user@host``).
            remote_dir: The directory on ``host`` to scan and mount read-only.
            image: The container image whose ENTRYPOINT is ``exiftool`` (≥13.x).
        """
        self._host = host
        self._remote_dir = remote_dir.rstrip('/')
        self._image = image

    def _run(self, exif_args: list[str]) -> str:
        docker = [
            'docker',
            'run',
            '--rm',
            '-v',
            f'{self._remote_dir}:/data:ro',
            self._image,
            *exif_args,
        ]
        remote_cmd = ' '.join(shlex.quote(arg) for arg in docker)
        result = subprocess.run(
            ['ssh', self._host, remote_cmd],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode not in (0, 1):
            raise RuntimeError(
                f'ssh/docker exiftool failed ({result.returncode}): {result.stderr.strip()}'
            )
        return result.stdout

    def preflight(self) -> bool:
        result = subprocess.run(
            ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', self._host, 'true'],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0

    def discover(self) -> list[Clip]:
        return self._discover_from('/data', self._to_media_path)

    def _to_media_path(self, clip: Clip) -> str:
        """Map a container ``/data/...`` ref back to the canonical remote path."""
        return self._remote_dir + clip.ref[len('/data') :]


# --- Parse / thin --------------------------------------------------------------


def parse_track(lines: list[str]) -> list[TrackPoint]:
    """Parse + dedup ExifTool telemetry lines into a raw (untinned) track.

    Drops camera-only frames (lat/lon ``-``) and collapses runs of consecutive
    identical positions — DJI samples telemetry at ~60 Hz but GPS updates ~10 Hz, so
    most frames repeat the prior fix. ``importance`` is filled later by thinning.

    Args:
        lines: ExifTool ``GPSDateTime|lat|lon|abs_alt`` lines for one clip.

    Returns:
        The deduped track in time order; empty if the clip has no valid fix.
    """
    points: list[TrackPoint] = []
    last_pos: tuple[float, float] | None = None
    for line in lines:
        parts = line.split('|')
        if len(parts) != 4:
            continue
        dt, lat_s, lon_s, alt_s = parts
        if lat_s == '-' or lon_s == '-' or dt == '-':
            continue
        try:
            lat, lon = float(lat_s), float(lon_s)
            timestamp = _to_canonical(dt)
        except ValueError:
            continue
        if (lat, lon) == last_pos:
            continue
        last_pos = (lat, lon)
        abs_alt = None if alt_s == '-' else float(alt_s)
        points.append(TrackPoint(timestamp, lat, lon, abs_alt, 0.0))
    return points


def build_flight(clip: Clip, raw: list[TrackPoint], epsilon: float) -> Flight | None:
    """Thin a raw track and assemble the loadable flight.

    Applies the shared Reumann–Witkam simplifier (the van tier's decimation, so
    drone tracks behave the same) and carries each kept vertex's deviation as
    ``importance``. The first/last *valid* fixes set the time bounds — the first is
    the natural-key half and is always retained by the simplifier.

    Args:
        clip: The source clip.
        raw: The deduped track from :func:`parse_track`.
        epsilon: Reumann–Witkam tolerance in metres.

    Returns:
        The assembled :class:`Flight`, or None if the clip carried no valid fix.
    """
    if not raw:
        return None
    kept = reumann_witkam([(p.lat, p.lon) for p in raw], epsilon)
    points = [
        TrackPoint(raw[i].timestamp, raw[i].lat, raw[i].lon, raw[i].abs_alt, importance)
        for i, importance in kept
    ]
    lats = [p.lat for p in points]
    lons = [p.lon for p in points]
    return Flight(
        clip=clip,
        first_fix_utc=raw[0].timestamp,
        last_fix_utc=raw[-1].timestamp,
        points=points,
        bbox=(min(lats), min(lons), max(lats), max(lons)),
    )


def extract_flight(extractor: Extractor, clip: Clip, epsilon: float) -> Flight | None:
    """Extract, parse, and thin one clip into a flight (the parallelized unit).

    Args:
        extractor: The active extraction backend.
        clip: The clip to process.
        epsilon: Reumann–Witkam tolerance in metres.

    Returns:
        The assembled flight, or None if the clip carried no valid fix.
    """
    return build_flight(clip, parse_track(extractor.telemetry_lines(clip)), epsilon)


# --- Load ----------------------------------------------------------------------


def load_flight(conn: sqlite3.Connection, flight: Flight) -> str:
    """Insert a flight + its track points, idempotent on the natural key.

    On a natural-key hit the points are not re-inserted; a known canonical
    ``media_path`` backfills a previously NULL one (the SD-now / NAS-later
    convergence). Writes in one transaction.

    Args:
        conn: Open SQLite connection (the single writer).
        flight: The flight to load.

    Returns:
        A one-word outcome: ``imported``, ``backfilled``, or ``skipped``.
    """
    clip = flight.clip
    existing = conn.execute(
        'SELECT id, media_path FROM drone_flights WHERE model_code = ? AND first_fix_utc = ?',
        (clip.model_code, flight.first_fix_utc),
    ).fetchone()
    if existing is not None:
        if clip.media_path and existing['media_path'] is None:
            conn.execute(
                'UPDATE drone_flights SET media_path = ? WHERE id = ?',
                (clip.media_path, existing['id']),
            )
            conn.commit()
            return 'backfilled'
        return 'skipped'

    cursor = conn.execute(
        'INSERT INTO drone_flights (model, model_code, first_fix_utc, last_fix_utc, '
        'media_path, source_name, n_points, min_lat, min_lon, max_lat, max_lon, imported_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            clip.model,
            clip.model_code,
            flight.first_fix_utc,
            flight.last_fix_utc,
            clip.media_path,
            clip.name,
            len(flight.points),
            *flight.bbox,
            now_canonical(),
        ),
    )
    flight_id = cursor.lastrowid
    conn.executemany(
        'INSERT INTO drone_track_points (flight_id, timestamp, lat, lon, abs_alt, importance) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        [(flight_id, p.timestamp, p.lat, p.lon, p.abs_alt, p.importance) for p in flight.points],
    )
    conn.commit()
    return 'imported'


def flight_payload(flight: Flight) -> dict:
    """Serialize a flight for the ``POST /api/drone/flights`` ingest body.

    Carries only identity + the thinned points; the server derives the time bounds,
    bbox, and point count from the points (Reumann–Witkam keeps both endpoints, so
    they bound the track). Kept symmetric with the route's ``_flight_from_json``.

    Args:
        flight: The flight to serialize.

    Returns:
        A JSON-serializable request body.
    """
    clip = flight.clip
    return {
        'model': clip.model,
        'model_code': clip.model_code,
        'media_path': clip.media_path,
        'source_name': clip.name,
        'points': [
            {
                'timestamp': p.timestamp,
                'lat': p.lat,
                'lon': p.lon,
                'abs_alt': p.abs_alt,
                'importance': p.importance,
            }
            for p in flight.points
        ],
    }


# --- Write sinks ---------------------------------------------------------------


class Loader(ABC):
    """A write sink for parsed flights (local SQLite, or a remote dashboard API)."""

    @abstractmethod
    def load(self, flight: Flight) -> str:
        """Persist one flight and return its outcome word.

        Args:
            flight: The flight to write.

        Returns:
            ``imported``, ``backfilled``, or ``skipped``.
        """

    def imported_media_paths(self) -> set[str]:
        """Return canonical media paths already loaded (for ``--incremental``).

        Empty for sinks that cannot cheaply answer it; only the local DB sink
        supports incremental skipping (the API path forbids it at parse time).
        """
        return set()


class DbLoader(Loader):
    """Writes flights to the local SQLite DB — the Pi-side home sync."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Initialize the sink.

        Args:
            conn: An open, initialized connection (the single writer).
        """
        self._conn = conn

    def load(self, flight: Flight) -> str:
        return load_flight(self._conn, flight)

    def imported_media_paths(self) -> set[str]:
        return {
            row['media_path']
            for row in self._conn.execute(
                'SELECT media_path FROM drone_flights WHERE media_path IS NOT NULL'
            )
        }


class ApiLoader(Loader):
    """POSTs flights to a running dashboard's ingest API — the laptop LAN path."""

    def __init__(self, base_url: str) -> None:
        """Initialize the sink.

        Args:
            base_url: The dashboard base URL, e.g. ``http://192.168.42.178:5000``.
        """
        self._url = base_url.rstrip('/') + '/api/drone/flights'

    def load(self, flight: Flight) -> str:
        response = requests.post(self._url, json=flight_payload(flight), timeout=30)
        response.raise_for_status()
        return response.json()['outcome']


# --- Orchestration -------------------------------------------------------------


def build_extractor(args: argparse.Namespace) -> Extractor:
    """Construct the extraction backend selected by the CLI args.

    Args:
        args: Parsed arguments.

    Returns:
        A :class:`LocalExtractor` (``--source``) or :class:`SshDockerExtractor`
        (``--ssh``).
    """
    if args.ssh:
        return SshDockerExtractor(args.ssh, args.remote_dir, args.image)
    return LocalExtractor(args.source, args.exiftool)


def build_loader(args: argparse.Namespace) -> Loader:
    """Construct the write sink selected by the CLI args.

    Args:
        args: Parsed arguments.

    Returns:
        An :class:`ApiLoader` (``--api``), or a :class:`DbLoader` over the local DB.
    """
    if args.api:
        return ApiLoader(args.api)
    conn = get_connection()
    init_db(conn)
    return DbLoader(conn)


def run(args: argparse.Namespace) -> int:
    """Discover, extract in parallel, and load — the importer body.

    Args:
        args: Parsed arguments.

    Returns:
        A process exit code.
    """
    extractor = build_extractor(args)
    if not extractor.preflight():
        print('Extraction host unreachable — nothing to do.', flush=True)
        return 0
    print('Discovering telemetry clips...', flush=True)
    clips = extractor.discover()
    by_model: dict[str, int] = {}
    for clip in clips:
        by_model[clip.model] = by_model.get(clip.model, 0) + 1
    summary = ', '.join(f'{n} {m}' for m, n in sorted(by_model.items())) or 'none'
    print(f'Found {len(clips)} telemetry clip(s): {summary}', flush=True)
    if not clips:
        return 0

    loader = None if args.dry_run else build_loader(args)

    if args.incremental and loader is not None:
        imported = loader.imported_media_paths()
        kept = [clip for clip in clips if clip.media_path not in imported]
        print(
            f'Incremental: {len(clips) - len(kept)} already imported, {len(kept)} to extract',
            flush=True,
        )
        clips = kept
        if not clips:
            return 0

    if args.limit:
        clips = clips[: args.limit]

    counts = {'imported': 0, 'backfilled': 0, 'skipped': 0, 'empty': 0}
    n_points = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures: dict[Future[Flight | None], Clip] = {
            pool.submit(extract_flight, extractor, clip, args.epsilon): clip for clip in clips
        }
        try:
            for future in _as_completed(futures):
                clip = futures[future]
                flight = future.result()
                if flight is None:
                    counts['empty'] += 1
                    print(f'  {clip.name}: no valid GPS fix — skipped', flush=True)
                    continue
                if loader is None:
                    print(
                        f'  {clip.name}: {len(flight.points)} pts '
                        f'[{flight.first_fix_utc}] (dry-run)',
                        flush=True,
                    )
                    n_points += len(flight.points)
                    continue
                outcome = loader.load(flight)
                counts[outcome] += 1
                if outcome == 'imported':
                    n_points += len(flight.points)
                print(
                    f'  {clip.name}: {outcome} ({len(flight.points)} pts, {clip.model})', flush=True
                )
        except KeyboardInterrupt:
            for future in futures:
                future.cancel()
            print('\nInterrupted.', flush=True)
            _print_summary(counts, n_points)
            return 130

    _print_summary(counts, n_points)
    return 0


def _as_completed(futures: dict[Future, Clip]):
    """Yield futures as they finish (thin wrapper for the KeyboardInterrupt path)."""
    from concurrent.futures import as_completed

    yield from as_completed(futures)


def _print_summary(counts: dict[str, int], n_points: int) -> None:
    """Print the run tally.

    Args:
        counts: Per-outcome flight counts.
        n_points: Total track points imported.
    """
    print(
        f'Done: {counts["imported"]} imported ({n_points} pts), '
        f'{counts["backfilled"]} backfilled, {counts["skipped"]} skipped, '
        f'{counts["empty"]} empty',
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        The parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--source', help='Local directory to scan with the host exiftool')
    source.add_argument(
        '--ssh', help='SSH host: extract in a container on the remote (e.g. rex-nas)'
    )
    parser.add_argument(
        '--remote-dir', help='Directory on the --ssh host to scan/mount (required with --ssh)'
    )
    parser.add_argument(
        '--image',
        default=DEFAULT_IMAGE,
        help=f'Container image for --ssh (default: {DEFAULT_IMAGE})',
    )
    parser.add_argument(
        '--exiftool',
        default='exiftool',
        help='ExifTool executable for --source (default: exiftool)',
    )
    parser.add_argument(
        '--db', help='SQLite DB path for the local sink (default: GPS_DB_PATH / ~/gps_history.db)'
    )
    parser.add_argument(
        '--api', help='POST flights to a dashboard URL instead of a local DB (laptop LAN path)'
    )
    parser.add_argument(
        '--epsilon',
        type=float,
        default=DEFAULT_EPSILON,
        help=f'Reumann–Witkam tolerance in metres (default: {DEFAULT_EPSILON})',
    )
    parser.add_argument(
        '--jobs',
        type=int,
        default=DEFAULT_JOBS,
        help=f'Parallel extractions (default: {DEFAULT_JOBS})',
    )
    parser.add_argument(
        '--incremental',
        action='store_true',
        help='Skip clips whose media_path is already imported (home-sync timer)',
    )
    parser.add_argument('--limit', type=int, help='Process at most N clips (testing)')
    parser.add_argument(
        '--dry-run', action='store_true', help='Discover + extract, but do not write the DB'
    )
    args = parser.parse_args()
    if args.ssh and not args.remote_dir:
        parser.error('--remote-dir is required with --ssh')
    if args.api and args.db:
        parser.error('--db is the local sink and cannot combine with --api')
    if args.api and args.incremental:
        parser.error('--incremental is the local-DB home-sync path and cannot combine with --api')
    return args


def main() -> None:
    """Entry point: parse args, set the DB path, run the importer."""
    args = parse_args()
    if args.db:
        from pathlib import Path

        import api.db

        api.db.DB_PATH = Path(args.db)
    sys.exit(run(args))


if __name__ == '__main__':
    run_cli(main)
