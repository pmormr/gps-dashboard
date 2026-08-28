"""Import Google Timeline location history into the phone tier of ``gps_history.db``.

Offline batch importer (see ``.claude/modules/phone.md``) — the phone's own
position history, the history sibling of the live OwnTracks tier
(``tools/sync_owntracks.py``).
The source is the **on-device Timeline export** (phone → Settings → Location →
Timeline → Export), a single ``Timeline.json`` whose ``semanticSegments`` carry:

* ``timelinePath`` — the traveled breadcrumb (one contiguous segment → one
  ``phone_paths`` row + its thinned ``phone_track_points``);
* ``visit`` — a place visit (→ ``phone_visits``);
* ``activity`` — a trip segment (→ ``phone_activities``).

``rawSignals`` is ignored: it is only the phone's recent ~30-day raw buffer, not
history, and that month is already covered by the van's own 5 Hz logger.

The tier is derived and **fully rebuildable from the export**, so each run is a
full replace (Google exports are cumulative — every export is the whole history).
Thinning reuses the shared ``processor/simplify.py`` Reumann–Witkam, so phone
tracks decimate like van and drone tracks.

Coordinates arrive as ``"40.79°, -77.86°"`` strings and timestamps as ISO-8601
with an offset; both are normalized to the canonical ms-UTC axis on the way in.

Examples::

    # Dev: parse only, against a throwaway DB
    uv run tools/import_phone_timeline.py ~/My\\ Drive/Timeline.json --db ./local.db --dry-run

    # Pi: load into the live DB (after scp'ing the export over)
    GPS_DB_PATH=/mnt/nvme/data/gps_history.db \\
        uv run tools/import_phone_timeline.py ~/Timeline.json
"""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from api.db import canonical_timestamp, get_connection, init_db, now_canonical
from common.cli import run_cli
from processor.simplify import reumann_witkam

# Reumann–Witkam tolerance (m). The Timeline breadcrumb is already coarse (median
# ~9 pts / 2-hour segment), so this only trims collinear runs on the denser urban
# segments; endpoints are always kept.
DEFAULT_EPSILON = 20.0


@dataclass(frozen=True)
class TrackPoint:
    """One thinned breadcrumb vertex.

    Attributes:
        timestamp: Canonical ms-UTC string (same axis as ``gps_points``).
        lat: Latitude (deg).
        lon: Longitude (deg).
        importance: Reumann–Witkam deviation (m) that retained the vertex.
        activity_type: Mode of the ``activity`` segment covering this point's
            time (e.g. ``IN_PASSENGER_VEHICLE``), or None between labeled trips.
            Filled by :func:`_annotate_modes`, for color-by-mode rendering.
    """

    timestamp: str
    lat: float
    lon: float
    importance: float
    activity_type: str | None = None


@dataclass(frozen=True)
class PhonePath:
    """A contiguous breadcrumb segment (one Timeline ``timelinePath``).

    Attributes:
        start_time: Canonical ms-UTC of the first point.
        end_time: Canonical ms-UTC of the last point.
        points: The thinned vertices in time order.
        bbox: ``(min_lat, min_lon, max_lat, max_lon)`` over the thinned points.
    """

    start_time: str
    end_time: str
    points: list[TrackPoint]
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class Visit:
    """A place visit (Timeline ``visit`` segment).

    Attributes:
        start_time: Canonical ms-UTC of the visit start.
        end_time: Canonical ms-UTC of the visit end.
        lat: Place latitude (deg).
        lon: Place longitude (deg).
        place_id: Google Place id, or None.
        semantic_type: Semantic place type (e.g. ``HOME``), or None.
        probability: Visit confidence in ``[0, 1]``, or None.
    """

    start_time: str
    end_time: str
    lat: float
    lon: float
    place_id: str | None
    semantic_type: str | None
    probability: float | None


@dataclass(frozen=True)
class Activity:
    """A trip segment (Timeline ``activity`` segment).

    Attributes:
        start_time: Canonical ms-UTC of the trip start.
        end_time: Canonical ms-UTC of the trip end.
        start_lat: Trip-start latitude (deg).
        start_lon: Trip-start longitude (deg).
        end_lat: Trip-end latitude (deg).
        end_lon: Trip-end longitude (deg).
        distance_m: Travelled distance in metres, or None.
        activity_type: Detected mode (e.g. ``IN_PASSENGER_VEHICLE``), or None.
        probability: Mode confidence in ``[0, 1]``, or None.
    """

    start_time: str
    end_time: str
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    distance_m: float | None
    activity_type: str | None
    probability: float | None


@dataclass(frozen=True)
class Timeline:
    """The parsed, thinned export ready to load.

    Attributes:
        paths: The breadcrumb segments.
        visits: The place visits.
        activities: The trip segments.
    """

    paths: list[PhonePath]
    visits: list[Visit]
    activities: list[Activity]


# --- Parse ---------------------------------------------------------------------


def parse_latlng(value: str) -> tuple[float, float]:
    """Parse a Timeline ``"lat°, lng°"`` coordinate string to ``(lat, lon)``.

    Args:
        value: A coordinate string, e.g. ``"40.793251°, -77.859949°"``.

    Returns:
        The ``(lat, lon)`` pair in decimal degrees.

    Raises:
        ValueError: If the string is not two ``°``-suffixed comma-separated floats.
    """
    lat_s, lon_s = value.replace('°', '').split(',')
    return float(lat_s), float(lon_s)


def _as_float(value: Any) -> float | None:
    """Coerce an optional JSON scalar to float, mapping absent/bad values to None.

    Args:
        value: A JSON value (number, string, or None).

    Returns:
        The value as a float, or None if it is absent or not numeric.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_path(seg: dict, epsilon: float) -> PhonePath | None:
    """Parse + thin one ``timelinePath`` segment into a :class:`PhonePath`.

    Args:
        seg: A ``semanticSegments`` entry carrying ``timelinePath``.
        epsilon: Reumann–Witkam tolerance in metres.

    Returns:
        The thinned path, or None if the segment carried no valid point.
    """
    raw: list[TrackPoint] = []
    for p in seg['timelinePath']:
        try:
            lat, lon = parse_latlng(p['point'])
            timestamp = canonical_timestamp(p['time'])
        except (KeyError, ValueError):
            continue
        raw.append(TrackPoint(timestamp, lat, lon, 0.0))
    if not raw:
        return None
    raw.sort(key=lambda pt: pt.timestamp)
    kept = reumann_witkam([(pt.lat, pt.lon) for pt in raw], epsilon)
    points = [TrackPoint(raw[i].timestamp, raw[i].lat, raw[i].lon, imp) for i, imp in kept]
    lats = [pt.lat for pt in points]
    lons = [pt.lon for pt in points]
    return PhonePath(
        start_time=points[0].timestamp,
        end_time=points[-1].timestamp,
        points=points,
        bbox=(min(lats), min(lons), max(lats), max(lons)),
    )


def _parse_visit(seg: dict) -> Visit | None:
    """Parse one ``visit`` segment into a :class:`Visit`.

    Args:
        seg: A ``semanticSegments`` entry carrying ``visit``.

    Returns:
        The visit, or None if it carries no place location or bounds.
    """
    visit = seg['visit']
    top = visit.get('topCandidate', {})
    loc = top.get('placeLocation', {}).get('latLng')
    if not loc:
        return None
    try:
        lat, lon = parse_latlng(loc)
        start = canonical_timestamp(seg['startTime'])
        end = canonical_timestamp(seg['endTime'])
    except (KeyError, ValueError):
        return None
    return Visit(
        start_time=start,
        end_time=end,
        lat=lat,
        lon=lon,
        place_id=top.get('placeId'),
        semantic_type=top.get('semanticType'),
        probability=_as_float(visit.get('probability')),
    )


def _parse_activity(seg: dict) -> Activity | None:
    """Parse one ``activity`` segment into an :class:`Activity`.

    Args:
        seg: A ``semanticSegments`` entry carrying ``activity``.

    Returns:
        The trip, or None if it lacks a start/end location or bounds.
    """
    act = seg['activity']
    start_loc = act.get('start', {}).get('latLng')
    end_loc = act.get('end', {}).get('latLng')
    if not start_loc or not end_loc:
        return None
    try:
        start_lat, start_lon = parse_latlng(start_loc)
        end_lat, end_lon = parse_latlng(end_loc)
        start = canonical_timestamp(seg['startTime'])
        end = canonical_timestamp(seg['endTime'])
    except (KeyError, ValueError):
        return None
    top = act.get('topCandidate', {})
    return Activity(
        start_time=start,
        end_time=end,
        start_lat=start_lat,
        start_lon=start_lon,
        end_lat=end_lat,
        end_lon=end_lon,
        distance_m=_as_float(act.get('distanceMeters')),
        activity_type=top.get('type'),
        probability=_as_float(top.get('probability')),
    )


def _annotate_modes(paths: list[PhonePath], activities: list[Activity]) -> list[PhonePath]:
    """Tag each breadcrumb point with the mode of the ``activity`` covering it.

    The breadcrumb (``timelinePath``) and the labeled trips (``activity``) are
    separate Timeline segments, so color-by-mode needs a time join: a point takes
    the ``activity_type`` of the activity interval containing its timestamp, or
    None if it falls between labeled trips (≈40% of points — visits, short hops).
    Activities are effectively non-overlapping, so a ``bisect`` on their sorted
    starts plus a single containment check resolves each point.

    Args:
        paths: The thinned breadcrumb paths.
        activities: The parsed trip segments.

    Returns:
        New paths whose points carry ``activity_type`` (unchanged if there are no
        activities to join against).
    """
    if not activities:
        return paths
    ordered = sorted(activities, key=lambda a: a.start_time)
    starts = [a.start_time for a in ordered]

    def mode_at(timestamp: str) -> str | None:
        i = bisect.bisect_right(starts, timestamp) - 1
        if i >= 0 and ordered[i].start_time <= timestamp <= ordered[i].end_time:
            return ordered[i].activity_type
        return None

    return [
        replace(path, points=[replace(p, activity_type=mode_at(p.timestamp)) for p in path.points])
        for path in paths
    ]


def parse_timeline(data: dict, epsilon: float) -> Timeline:
    """Parse a Timeline export dict into thinned paths + semantic segments.

    Dispatches each ``semanticSegments`` entry by its discriminating key
    (``timelinePath`` / ``visit`` / ``activity``); other kinds (e.g.
    ``timelineMemory``) and ``rawSignals`` are ignored. Malformed segments are
    skipped rather than fatal — a 15-year export tolerates a few bad rows. A final
    pass tags each breadcrumb point with its activity mode (color-by-mode).

    Args:
        data: The decoded ``Timeline.json`` object.
        epsilon: Reumann–Witkam tolerance in metres.

    Returns:
        The parsed :class:`Timeline`.
    """
    paths: list[PhonePath] = []
    visits: list[Visit] = []
    activities: list[Activity] = []
    for seg in data.get('semanticSegments', []):
        if 'timelinePath' in seg:
            path = _parse_path(seg, epsilon)
            if path is not None:
                paths.append(path)
        elif 'visit' in seg:
            visit = _parse_visit(seg)
            if visit is not None:
                visits.append(visit)
        elif 'activity' in seg:
            activity = _parse_activity(seg)
            if activity is not None:
                activities.append(activity)
    return Timeline(paths=_annotate_modes(paths, activities), visits=visits, activities=activities)


# --- Load ----------------------------------------------------------------------

_PHONE_TABLES = ('phone_track_points', 'phone_paths', 'phone_visits', 'phone_activities')


def load_timeline(conn: sqlite3.Connection, timeline: Timeline) -> None:
    """Full-replace the phone tier with ``timeline`` in one transaction.

    Every ``phone_*`` table is truncated and reloaded — the export is cumulative,
    so a replace is both correct and the whole idempotency story (no natural key).

    Args:
        conn: An open, initialized connection (the single writer).
        timeline: The parsed export to load.
    """
    now = now_canonical()
    with conn:
        for table in _PHONE_TABLES:
            conn.execute(f'DELETE FROM {table}')
        for path in timeline.paths:
            cursor = conn.execute(
                'INSERT INTO phone_paths (start_time, end_time, n_points, '
                'min_lat, min_lon, max_lat, max_lon, imported_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (path.start_time, path.end_time, len(path.points), *path.bbox, now),
            )
            conn.executemany(
                'INSERT INTO phone_track_points '
                '(path_id, timestamp, lat, lon, importance, activity_type) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                [
                    (cursor.lastrowid, p.timestamp, p.lat, p.lon, p.importance, p.activity_type)
                    for p in path.points
                ],
            )
        conn.executemany(
            'INSERT INTO phone_visits (start_time, end_time, lat, lon, place_id, '
            'semantic_type, probability) VALUES (?, ?, ?, ?, ?, ?, ?)',
            [
                (v.start_time, v.end_time, v.lat, v.lon, v.place_id, v.semantic_type, v.probability)
                for v in timeline.visits
            ],
        )
        conn.executemany(
            'INSERT INTO phone_activities (start_time, end_time, start_lat, start_lon, '
            'end_lat, end_lon, distance_m, activity_type, probability) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (
                    a.start_time,
                    a.end_time,
                    a.start_lat,
                    a.start_lon,
                    a.end_lat,
                    a.end_lon,
                    a.distance_m,
                    a.activity_type,
                    a.probability,
                )
                for a in timeline.activities
            ],
        )


# --- Orchestration -------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """Read the export, parse + thin it, and (unless ``--dry-run``) load it.

    Args:
        args: Parsed arguments.

    Returns:
        A process exit code.
    """
    import api.db

    source = Path(args.file).expanduser()
    print(f'Reading {source} ...', flush=True)
    with source.open() as handle:
        data = json.load(handle)
    if args.limit:
        data['semanticSegments'] = data.get('semanticSegments', [])[: args.limit]

    timeline = parse_timeline(data, args.epsilon)
    n_points = sum(len(p.points) for p in timeline.paths)
    print(
        f'Parsed {len(timeline.paths)} paths ({n_points} pts), '
        f'{len(timeline.visits)} visits, {len(timeline.activities)} activities',
        flush=True,
    )
    if args.dry_run:
        print('Dry run — not writing.', flush=True)
        return 0

    conn = get_connection()
    init_db(conn)
    load_timeline(conn, timeline)
    print(f'Loaded into {api.db.DB_PATH}', flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        The parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('file', help='Path to the Google Timeline export (Timeline.json)')
    parser.add_argument('--db', help='SQLite DB path (default: GPS_DB_PATH / ~/gps_history.db)')
    parser.add_argument(
        '--epsilon',
        type=float,
        default=DEFAULT_EPSILON,
        help=f'Reumann–Witkam tolerance in metres (default: {DEFAULT_EPSILON})',
    )
    parser.add_argument('--limit', type=int, help='Parse at most N semanticSegments (testing)')
    parser.add_argument(
        '--dry-run', action='store_true', help='Parse + report, but do not write the DB'
    )
    return parser.parse_args()


def main() -> None:
    """Entry point: parse args, set the DB path, run the importer."""
    args = parse_args()
    import api.db

    api.db.apply_path_overrides(args.db)
    sys.exit(run(args))


if __name__ == '__main__':
    run_cli(main)
