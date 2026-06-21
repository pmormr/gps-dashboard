"""GPS track processor: derive the processed tier from raw ``gps_points``.

Phase 3 — **online denoise filter**. A single causal state machine tails raw
``gps_points`` by a persisted id cursor and derives the processed tier the
frontend reads: software static-hold for stops + Reumann–Witkam line
simplification for moving segments, with per-fix accuracy gating. Each emitted
row carries ``n_raw`` (raw fixes subsumed) and ``importance`` (decimation
priority); regime transitions emit ``track_events`` (``stop_start`` /
``stop_end``).

Writer invariant: the logger owns raw (``gps_points``, ``receiver_metadata``);
this process owns the processed tier (``track_points``, ``track_events``) only.
It never touches gpsd and never blocks the logger.

Determinism / idempotency (C7): output is a pure function of the raw prefix
ordered by ``id`` — state transitions depend only on raw fields and the frozen
``Thresholds`` (no wall-clock in the algorithm, no RNG). The committed cursor
advances only to the last *finalized* emit; an open stop and the open moving
segment are provisional. On startup (or ``--rebuild``) provisional rows beyond
the cursor are discarded and the moving anchor is reconstructed from the last
committed vertex, so replaying from the cursor reproduces identical content.
Replay cost is bounded by the longest open stop, not the loaded tail. Changing a
threshold is the intended trigger to rebuild — a feature, not a violation.

Run::

    uv run processor/gps_processor.py
    uv run processor/gps_processor.py --rebuild   # truncate processed tier, reprocess from id 0
"""

import argparse
import math
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from api.db import get_connection, init_db
from processor.simplify import (
    EARTH_RADIUS_M,
)
from processor.simplify import (
    local_meters as _local_meters,
)
from processor.simplify import (
    perp_distance_m as _perp_distance_m,
)

CURSOR_KEY = 'last_committed_raw_id'
POLL_INTERVAL_SECONDS = 2.0
BATCH_SIZE = 5000
HEARTBEAT_SECONDS = 60
ERROR_BACKOFF_SECONDS = 5

# Weight clamp for the accuracy-weighted stop estimate: w = 1/max(eph, floor)^2.
# Floors how much a single very-precise fix can dominate and avoids div-by-zero.
EPH_FLOOR_M = 1.0


@dataclass(frozen=True)
class Thresholds:
    """Tuning knobs for the denoise state machine (the plan's C-table starts).

    Frozen because output is deterministic only *per threshold set* (C7):
    changing any value is the intended trigger to rebuild the processed tier, not
    a bug. Tuned against real trips in Phase 5.

    Attributes:
        stop_speed_enter: Doppler speed (m/s) below which a stop candidate opens.
        stop_speed_exit: Speed (m/s) above which a parked van resumes (hysteresis).
        stop_min_duration: Seconds a candidate must persist (within radius) to confirm.
        stop_radius: Metres; a dwell stays within this of its running centroid.
        simplify_epsilon: Metres of perpendicular deviation that emits a moving vertex.
        move_emit_max_gap: Seconds; keep-alive emit on a long straightaway.
        accuracy_reject: Metres; fixes with eph above this are dropped.
    """

    stop_speed_enter: float = 0.5
    stop_speed_exit: float = 1.0
    stop_min_duration: float = 30.0
    stop_radius: float = 15.0
    simplify_epsilon: float = 2.5
    move_emit_max_gap: float = 60.0
    accuracy_reject: float = 50.0


class State(Enum):
    """Causal state-machine regime."""

    MOVING = 'moving'
    CANDIDATE = 'candidate'
    PARKED = 'parked'


@dataclass(frozen=True)
class Fix:
    """A raw ``gps_points`` row, as the filter consumes it.

    Attributes:
        id: Raw ``gps_points.id`` — the determinism ordering anchor.
        timestamp: Canonical ms-UTC string.
        lat: Latitude (deg).
        lon: Longitude (deg).
        speed: Doppler speed (m/s), or None.
        altitude: Altitude (m), or None.
        track: Heading (deg), or None.
        eph: Horizontal accuracy ``hypot(epx, epy)`` (m), or None.
    """

    id: int
    timestamp: str
    lat: float
    lon: float
    speed: float | None
    altitude: float | None
    track: float | None
    eph: float | None


@dataclass(frozen=True)
class Anchor:
    """The last committed vertex — origin of the open moving segment.

    Attributes:
        lat: Latitude (deg).
        lon: Longitude (deg).
        raw_id: Raw id of the committed vertex (equals the cursor at emit time).
        ts: Canonical timestamp, for the keep-alive gap.
    """

    lat: float
    lon: float
    raw_id: int
    ts: str


@dataclass
class TrackRow:
    """A ``track_points`` row to write (a moving vertex or a stop)."""

    timestamp: str
    lat: float
    lon: float
    speed: float | None
    altitude: float | None
    track: float | None
    kind: str
    n_raw: int
    importance: float
    accuracy: float | None
    dwell_start: str | None
    dwell_end: str | None
    radius: float | None
    src_raw_id: int


@dataclass
class EventRow:
    """A ``track_events`` row to write."""

    timestamp: str
    end_time: str | None
    type: str
    magnitude: float | None
    payload: str | None
    src_raw_id: int


@dataclass
class DrainResult:
    """One batch's emits, split by commit semantics.

    ``finalized_*`` rows sit at or below ``cursor`` and are permanent;
    ``provisional_*`` rows (the open stop + its ``stop_start``) sit above it and
    are re-derived on restart.
    """

    finalized_tracks: list[TrackRow]
    finalized_events: list[EventRow]
    cursor: int
    provisional_tracks: list[TrackRow]
    provisional_events: list[EventRow]


def _eph(epx: float | None, epy: float | None) -> float | None:
    """Horizontal accuracy from the orthogonal 1-sigma errors, or None.

    Args:
        epx: Longitude 1-sigma error (m).
        epy: Latitude 1-sigma error (m).

    Returns:
        ``hypot(epx, epy)`` when both are present, else None.
    """
    if epx is None or epy is None:
        return None
    return math.hypot(epx, epy)


def _ts_seconds(ts: str) -> float:
    """Parse a canonical timestamp to epoch seconds (for durations only).

    Args:
        ts: A canonical ms-UTC timestamp string.

    Returns:
        Epoch seconds as a float.
    """
    return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Planar distance in metres between two points.

    Args:
        lat1: First latitude (deg).
        lon1: First longitude (deg).
        lat2: Second latitude (deg).
        lon2: Second longitude (deg).

    Returns:
        Distance in metres.
    """
    east, north = _local_meters(lat1, lon1, lat2, lon2)
    return math.hypot(east, north)


def _weight(eph: float | None) -> float:
    """Accuracy weight ``1/max(eph, floor)^2`` for the stop estimate (1.0 if unknown).

    Args:
        eph: Horizontal accuracy (m), or None.

    Returns:
        The fix's weight in the accuracy-weighted mean.
    """
    if eph is None:
        return 1.0
    return 1.0 / (max(eph, EPH_FLOOR_M) ** 2)


class Stop:
    """Accuracy-weighted accumulator for one parked dwell.

    Holds running weighted sums rather than the fix list, so the held estimate,
    spread, and dwell are O(1) per fix and bounded in memory even on a multi-day
    stop. The estimate is an accuracy-weighted mean (w = 1/max(eph, floor)^2);
    deterministic and order-independent (a true geometric median is the deferred
    robustness upgrade — a free swap given C7 rebuilds).
    """

    def __init__(self, fix: Fix) -> None:
        """Seed the accumulator at the candidate's first fix.

        Args:
            fix: The first low-speed fix; its position is the projection origin.
        """
        self.origin_lat = fix.lat
        self.origin_lon = fix.lon
        self.start_ts = fix.timestamp
        self.start_raw_id = fix.id
        self.last_ts = fix.timestamp
        self.last_raw_id = fix.id
        self._sw = 0.0
        self._swx = 0.0
        self._swy = 0.0
        self._swxx = 0.0
        self._swyy = 0.0
        self.n_raw = 0
        self._sum_eph = 0.0
        self._n_eph = 0
        self._sum_alt = 0.0
        self._n_alt = 0
        self.add(fix)

    def add(self, fix: Fix) -> None:
        """Fold one parked fix into the running weighted estimate.

        Args:
            fix: A fix confirmed to belong to this dwell.
        """
        east, north = _local_meters(fix.lat, fix.lon, self.origin_lat, self.origin_lon)
        w = _weight(fix.eph)
        self._sw += w
        self._swx += w * east
        self._swy += w * north
        self._swxx += w * east * east
        self._swyy += w * north * north
        self.n_raw += 1
        if fix.eph is not None:
            self._sum_eph += fix.eph
            self._n_eph += 1
        if fix.altitude is not None:
            self._sum_alt += fix.altitude
            self._n_alt += 1
        self.last_ts = fix.timestamp
        self.last_raw_id = fix.id

    @property
    def lat_lon(self) -> tuple[float, float]:
        """The held position: the accuracy-weighted mean, back in lat/lon."""
        mean_e = self._swx / self._sw
        mean_n = self._swy / self._sw
        lat = self.origin_lat + math.degrees(mean_n / EARTH_RADIUS_M)
        lon = self.origin_lon + math.degrees(
            mean_e / (EARTH_RADIUS_M * math.cos(math.radians(self.origin_lat)))
        )
        return lat, lon

    @property
    def radius(self) -> float:
        """Weighted RMS spread of the dwell about its centroid, in metres."""
        mean_e = self._swx / self._sw
        mean_n = self._swy / self._sw
        var = (self._swxx / self._sw - mean_e * mean_e) + (self._swyy / self._sw - mean_n * mean_n)
        return math.sqrt(var) if var > 0.0 else 0.0

    @property
    def accuracy(self) -> float | None:
        """Representative eph: the mean of the contributing fixes' eph, or None."""
        return self._sum_eph / self._n_eph if self._n_eph else None

    @property
    def altitude(self) -> float | None:
        """Mean altitude of the contributing fixes, or None."""
        return self._sum_alt / self._n_alt if self._n_alt else None

    @property
    def dwell_seconds(self) -> float:
        """Dwell length (s), clamped to >= 0 against a rare backward clock step (C23)."""
        return max(0.0, _ts_seconds(self.last_ts) - _ts_seconds(self.start_ts))


class TrackFilter:
    """The causal denoise state machine. Pure: produces emits, touches no DB.

    Feed raw fixes in ``id`` order via :meth:`feed`, then :meth:`drain` the
    finalized and provisional emits for the loop to persist. Construction
    reconstructs the moving anchor from the last committed vertex so that
    replaying from the cursor reproduces steady-state output exactly (C7).
    """

    def __init__(self, thresholds: Thresholds, cursor: int, anchor: Anchor | None) -> None:
        """Initialize at a committed boundary.

        Args:
            thresholds: The frozen tuning knobs.
            cursor: The committed cursor (last finalized raw id); 0 at cold start.
            anchor: The reconstructed moving anchor (last committed vertex), or
                None at cold start (no committed output yet).
        """
        self.t = thresholds
        self.state = State.MOVING
        self._cursor = cursor

        # Open moving segment (provisional): anchor is committed, ref/pending are not.
        self.anchor = anchor
        self.ref: Fix | None = None
        self.pending: Fix | None = None
        self.seg_n = 0
        self.seg_max_dev = 0.0

        # Stop candidate (provisional, replayable on abort) and open dwell.
        self.cand: Stop | None = None
        self.cand_fixes: list[Fix] = []
        self.open_stop: Stop | None = None

        self._finalized_tracks: list[TrackRow] = []
        self._finalized_events: list[EventRow] = []

        self.fixes_seen = 0
        self.dropped = 0
        self.vertices_emitted = 0
        self.stops_opened = 0
        self.stops_closed = 0
        self.events_emitted = 0

    def feed(self, fix: Fix) -> None:
        """Advance the state machine by one raw fix.

        Args:
            fix: The next raw fix, in ascending ``id`` order.
        """
        self.fixes_seen += 1
        if fix.eph is not None and fix.eph > self.t.accuracy_reject:
            self.dropped += 1
            return
        if self.state is State.MOVING:
            self._feed_moving(fix)
        elif self.state is State.CANDIDATE:
            self._feed_candidate(fix)
        else:
            self._feed_parked(fix)

    def drain(self) -> DrainResult:
        """Return and clear this batch's finalized emits + the provisional snapshot.

        Returns:
            A :class:`DrainResult`; the open stop (if any) is rendered as a
            provisional row + ``stop_start`` event reflecting its current state.
        """
        finalized_tracks = self._finalized_tracks
        finalized_events = self._finalized_events
        self._finalized_tracks = []
        self._finalized_events = []
        provisional_tracks: list[TrackRow] = []
        provisional_events: list[EventRow] = []
        if self.open_stop is not None:
            provisional_tracks.append(self._stop_row(self.open_stop))
            provisional_events.append(self._stop_start_event(self.open_stop))
        return DrainResult(
            finalized_tracks,
            finalized_events,
            self._cursor,
            provisional_tracks,
            provisional_events,
        )

    # --- MOVING regime -----------------------------------------------------

    def _feed_moving(self, fix: Fix) -> None:
        """Either open a stop candidate or extend the simplified moving segment."""
        if fix.speed is not None and fix.speed < self.t.stop_speed_enter:
            self.cand = Stop(fix)
            self.cand_fixes = [fix]
            self.state = State.CANDIDATE
            return
        self._step_moving(fix)

    def _step_moving(self, fix: Fix) -> None:
        """Reumann–Witkam step: subsume, break-and-emit, or keep-alive."""
        if self.anchor is None:
            # Cold start: the first moving fix anchors the trail.
            self._emit_vertex(fix, n_raw=1, importance=0.0)
            self._set_anchor(fix)
            return
        if self.ref is None:
            self.ref = fix
            self.pending = fix
            self.seg_n = 1
            self.seg_max_dev = 0.0
            return

        assert self.pending is not None
        dev = _perp_distance_m(
            (fix.lat, fix.lon),
            (self.anchor.lat, self.anchor.lon),
            (self.ref.lat, self.ref.lon),
        )
        if dev > self.t.simplify_epsilon:
            # Emit the last in-tolerance fix; the breaking fix opens a new segment.
            self._emit_vertex(self.pending, n_raw=self.seg_n, importance=dev)
            self._set_anchor(self.pending)
            self.ref = fix
            self.pending = fix
            self.seg_n = 1
            self.seg_max_dev = 0.0
            return

        self.pending = fix
        self.seg_n += 1
        self.seg_max_dev = max(self.seg_max_dev, dev)
        if _ts_seconds(fix.timestamp) - _ts_seconds(self.anchor.ts) >= self.t.move_emit_max_gap:
            self._emit_vertex(fix, n_raw=self.seg_n, importance=self.seg_max_dev)
            self._set_anchor(fix)

    def _set_anchor(self, fix: Fix) -> None:
        """Reset the open segment to a fresh anchor at ``fix``."""
        self.anchor = Anchor(fix.lat, fix.lon, fix.id, fix.timestamp)
        self.ref = None
        self.pending = None
        self.seg_n = 0
        self.seg_max_dev = 0.0

    def _emit_vertex(self, fix: Fix, n_raw: int, importance: float) -> None:
        """Finalize a moving vertex and advance the cursor to it."""
        self._finalized_tracks.append(
            TrackRow(
                timestamp=fix.timestamp,
                lat=fix.lat,
                lon=fix.lon,
                speed=fix.speed,
                altitude=fix.altitude,
                track=fix.track,
                kind='track',
                n_raw=n_raw,
                importance=importance,
                accuracy=fix.eph,
                dwell_start=None,
                dwell_end=None,
                radius=None,
                src_raw_id=fix.id,
            )
        )
        self._cursor = fix.id
        self.vertices_emitted += 1

    # --- Stop candidate ----------------------------------------------------

    def _feed_candidate(self, fix: Fix) -> None:
        """Grow the candidate, confirm it as a stop, or abort to moving."""
        assert self.cand is not None
        centroid_lat, centroid_lon = self.cand.lat_lon
        within = _distance_m(fix.lat, fix.lon, centroid_lat, centroid_lon) <= self.t.stop_radius
        slow = fix.speed is None or fix.speed <= self.t.stop_speed_exit
        if within and slow:
            self.cand.add(fix)
            self.cand_fixes.append(fix)
            if self.cand.dwell_seconds >= self.t.stop_min_duration:
                self._confirm_stop()
            return
        self._abort_candidate(fix)

    def _confirm_stop(self) -> None:
        """Promote the candidate to an open dwell, emitting the arrival vertex."""
        if self.pending is not None:
            # Finalize the drive at its last moving vertex (the arrival point).
            self._emit_vertex(self.pending, n_raw=self.seg_n, importance=self.seg_max_dev)
            self._set_anchor(self.pending)
        else:
            self._set_anchor_none()
        self.open_stop = self.cand
        self.cand = None
        self.cand_fixes = []
        self.state = State.PARKED
        self.stops_opened += 1

    def _set_anchor_none(self) -> None:
        """Clear the open segment with no anchor (cold-start / stop-adjacent)."""
        self.ref = None
        self.pending = None
        self.seg_n = 0
        self.seg_max_dev = 0.0

    def _abort_candidate(self, fix: Fix) -> None:
        """Replay buffered candidate fixes as moving, then re-handle ``fix``."""
        buffered = self.cand_fixes
        self.cand = None
        self.cand_fixes = []
        self.state = State.MOVING
        for buffered_fix in buffered:
            self._step_moving(buffered_fix)
        self._feed_moving(fix)

    # --- PARKED regime -----------------------------------------------------

    def _feed_parked(self, fix: Fix) -> None:
        """Refine the held estimate, or close the stop and resume moving."""
        assert self.open_stop is not None
        held_lat, held_lon = self.open_stop.lat_lon
        moved = _distance_m(fix.lat, fix.lon, held_lat, held_lon) > self.t.stop_radius
        resumed = fix.speed is not None and fix.speed > self.t.stop_speed_exit
        if moved or resumed:
            self._close_stop(fix)
            return
        self.open_stop.add(fix)

    def _close_stop(self, fix: Fix) -> None:
        """Finalize the dwell + its events, then process ``fix`` as the departure."""
        stop = self.open_stop
        assert stop is not None
        held_lat, held_lon = stop.lat_lon
        self._finalized_tracks.append(self._stop_row(stop))
        self._finalized_events.append(self._stop_start_event(stop))
        self._finalized_events.append(
            EventRow(
                timestamp=stop.last_ts,
                end_time=None,
                type='stop_end',
                magnitude=stop.dwell_seconds,
                payload=None,
                src_raw_id=stop.last_raw_id,
            )
        )
        self.events_emitted += 2
        self._cursor = stop.last_raw_id
        self.stops_closed += 1
        self.open_stop = None
        # Resume moving anchored at the held position so the trail stays connected.
        self.anchor = Anchor(held_lat, held_lon, stop.last_raw_id, stop.last_ts)
        self._set_anchor_none()
        self.state = State.MOVING
        self._feed_moving(fix)

    def _stop_row(self, stop: Stop) -> TrackRow:
        """Render the current state of a dwell as a ``track_points`` row."""
        lat, lon = stop.lat_lon
        return TrackRow(
            timestamp=stop.start_ts,
            lat=lat,
            lon=lon,
            speed=0.0,
            altitude=stop.altitude,
            track=None,
            kind='stop',
            n_raw=stop.n_raw,
            importance=stop.dwell_seconds,
            accuracy=stop.accuracy,
            dwell_start=stop.start_ts,
            dwell_end=stop.last_ts,
            radius=stop.radius,
            src_raw_id=stop.last_raw_id,
        )

    def _stop_start_event(self, stop: Stop) -> EventRow:
        """Build the ``stop_start`` event for a dwell (provenance at its first fix)."""
        return EventRow(
            timestamp=stop.start_ts,
            end_time=None,
            type='stop_start',
            magnitude=None,
            payload=None,
            src_raw_id=stop.start_raw_id,
        )


def get_cursor(conn: sqlite3.Connection) -> int:
    """Return the last committed raw id, or 0 if processing has never run.

    Args:
        conn: Open SQLite connection.

    Returns:
        The raw ``gps_points.id`` up to which the processed tier is finalized.
    """
    row = conn.execute('SELECT value FROM processing_state WHERE key = ?', (CURSOR_KEY,)).fetchone()
    return int(row['value']) if row else 0


def set_cursor(conn: sqlite3.Connection, raw_id: int) -> None:
    """Upsert the committed-cursor value (no commit — caller owns the transaction).

    Args:
        conn: Open SQLite connection.
        raw_id: The raw id up to which the processed tier is now finalized.
    """
    conn.execute(
        'INSERT INTO processing_state (key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (CURSOR_KEY, str(raw_id)),
    )


def discard_provisional(conn: sqlite3.Connection, cursor: int) -> int:
    """Delete processed rows derived from raw beyond the cursor (committed).

    These are rows emitted in a batch whose cursor advance never committed (a
    crash), the open-stop snapshot, or — when ``cursor`` is 0 — the entire
    processed tier for a rebuild. Dropping them before resuming is what makes
    reprocessing idempotent (C7).

    Args:
        conn: Open SQLite connection.
        cursor: The committed cursor; rows with ``src_raw_id`` above it are stale.

    Returns:
        The number of ``track_points`` rows discarded.
    """
    n = conn.execute('DELETE FROM track_points WHERE src_raw_id > ?', (cursor,)).rowcount
    conn.execute('DELETE FROM track_events WHERE src_raw_id > ?', (cursor,))
    conn.commit()
    return n


def reconstruct_anchor(conn: sqlite3.Connection, cursor: int) -> Anchor | None:
    """Rebuild the moving anchor from the last committed ``track_points`` row.

    The simplifier's anchor is an already-committed vertex sitting at the cursor,
    so a fresh replay (which reads only rows after the cursor) would otherwise
    lose it and diverge. Reading it back makes replay match steady state (C7). A
    committed ``stop`` row anchors the resumed drive at the held position.

    Args:
        conn: Open SQLite connection.
        cursor: The committed cursor.

    Returns:
        The reconstructed anchor, or None when no committed output exists.
    """
    row = conn.execute(
        'SELECT lat, lon, timestamp, src_raw_id FROM track_points '
        'WHERE src_raw_id <= ? ORDER BY src_raw_id DESC LIMIT 1',
        (cursor,),
    ).fetchone()
    if row is None:
        return None
    return Anchor(row['lat'], row['lon'], row['src_raw_id'], row['timestamp'])


_TRACK_COLUMNS = (
    'timestamp, lat, lon, speed, altitude, track, kind, n_raw, importance, '
    'accuracy, dwell_start, dwell_end, radius, src_raw_id'
)
_TRACK_PLACEHOLDERS = '?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?'
_EVENT_COLUMNS = 'timestamp, end_time, type, magnitude, payload, src_raw_id'
_EVENT_PLACEHOLDERS = '?, ?, ?, ?, ?, ?'


def _track_tuple(row: TrackRow) -> tuple:
    """Flatten a :class:`TrackRow` to the ``track_points`` column order."""
    return (
        row.timestamp,
        row.lat,
        row.lon,
        row.speed,
        row.altitude,
        row.track,
        row.kind,
        row.n_raw,
        row.importance,
        row.accuracy,
        row.dwell_start,
        row.dwell_end,
        row.radius,
        row.src_raw_id,
    )


def _event_tuple(row: EventRow) -> tuple:
    """Flatten an :class:`EventRow` to the ``track_events`` column order."""
    return (
        row.timestamp,
        row.end_time,
        row.type,
        row.magnitude,
        row.payload,
        row.src_raw_id,
    )


def apply_drain(conn: sqlite3.Connection, prev_cursor: int, result: DrainResult) -> None:
    """Persist one drained batch atomically (clear → finalize → advance → snapshot).

    The pre-write clear uses ``prev_cursor`` (not the new cursor) so a closing
    batch removes the previous batch's open-stop snapshot before re-inserting it
    as finalized — its ``src_raw_id`` then falls at/under the new cursor and the
    new-cursor clear would miss it. This mirrors :func:`discard_provisional`, so a
    crash between batches recovers to the same state a restart would.

    Args:
        conn: Open SQLite connection.
        prev_cursor: The committed cursor before this batch.
        result: The batch's drained emits.
    """
    conn.execute('DELETE FROM track_points WHERE src_raw_id > ?', (prev_cursor,))
    conn.execute('DELETE FROM track_events WHERE src_raw_id > ?', (prev_cursor,))
    if result.finalized_tracks:
        conn.executemany(
            f'INSERT INTO track_points ({_TRACK_COLUMNS}) VALUES ({_TRACK_PLACEHOLDERS})',
            [_track_tuple(r) for r in result.finalized_tracks],
        )
    if result.finalized_events:
        conn.executemany(
            f'INSERT INTO track_events ({_EVENT_COLUMNS}) VALUES ({_EVENT_PLACEHOLDERS})',
            [_event_tuple(r) for r in result.finalized_events],
        )
    set_cursor(conn, result.cursor)
    if result.provisional_tracks:
        conn.executemany(
            f'INSERT INTO track_points ({_TRACK_COLUMNS}) VALUES ({_TRACK_PLACEHOLDERS})',
            [_track_tuple(r) for r in result.provisional_tracks],
        )
    if result.provisional_events:
        conn.executemany(
            f'INSERT INTO track_events ({_EVENT_COLUMNS}) VALUES ({_EVENT_PLACEHOLDERS})',
            [_event_tuple(r) for r in result.provisional_events],
        )
    conn.commit()


def _make_fix(row: sqlite3.Row) -> Fix:
    """Build a :class:`Fix` from a raw ``gps_points`` row."""
    return Fix(
        id=row['id'],
        timestamp=row['timestamp'],
        lat=row['lat'],
        lon=row['lon'],
        speed=row['speed'],
        altitude=row['altitude'],
        track=row['track'],
        eph=_eph(row['epx'], row['epy']),
    )


def run(conn: sqlite3.Connection, rebuild: bool) -> None:
    """Resume (or rebuild) the processed tier, then tail raw forever.

    Args:
        conn: Open SQLite connection.
        rebuild: When True, truncate the processed tier and reprocess from id 0.
    """
    init_db(conn)  # create-only; raw-data migrations stay owned by logger/app
    cursor = 0 if rebuild else get_cursor(conn)
    if rebuild:
        set_cursor(conn, 0)
        conn.commit()
    discarded = discard_provisional(conn, cursor)
    anchor = reconstruct_anchor(conn, cursor)
    note = f'; discarded {discarded} provisional' if discarded else ''
    print(f'processor: resuming after raw id {cursor}{note}', flush=True)

    filt = TrackFilter(Thresholds(), cursor, anchor)
    committed = cursor
    read_id = cursor

    last = _Counters.snapshot(filt)
    last_heartbeat = time.monotonic()
    while True:
        rows = conn.execute(
            'SELECT id, timestamp, lat, lon, speed, altitude, track, epx, epy '
            'FROM gps_points WHERE id > ? ORDER BY id LIMIT ?',
            (read_id, BATCH_SIZE),
        ).fetchall()
        if rows:
            for row in rows:
                filt.feed(_make_fix(row))
                read_id = row['id']
            result = filt.drain()
            apply_drain(conn, committed, result)
            committed = result.cursor

        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_SECONDS:
            cur = _Counters.snapshot(filt)
            max_raw = conn.execute('SELECT COALESCE(MAX(id), 0) FROM gps_points').fetchone()[0]
            print(cur.heartbeat_line(last, committed, max_raw, filt.state), flush=True)
            last = cur
            last_heartbeat = now

        if len(rows) < BATCH_SIZE:
            time.sleep(POLL_INTERVAL_SECONDS)


@dataclass
class _Counters:
    """Snapshot of the filter's cumulative counters, for windowed heartbeats."""

    fixes: int
    dropped: int
    vertices: int
    stops_opened: int
    stops_closed: int
    events: int

    @classmethod
    def snapshot(cls, filt: TrackFilter) -> '_Counters':
        """Capture the filter's current cumulative counters."""
        return cls(
            filt.fixes_seen,
            filt.dropped,
            filt.vertices_emitted,
            filt.stops_opened,
            filt.stops_closed,
            filt.events_emitted,
        )

    def heartbeat_line(self, prev: '_Counters', cursor: int, max_raw: int, state: State) -> str:
        """Format a one-line window summary against the previous snapshot.

        Args:
            prev: The snapshot at the start of the window.
            cursor: The committed cursor.
            max_raw: The newest raw id, for the backlog figure.
            state: The current regime.

        Returns:
            A human-readable heartbeat string for the journal.
        """
        return (
            f'heartbeat: fixes={self.fixes - prev.fixes} '
            f'vertices={self.vertices - prev.vertices} '
            f'stops_opened={self.stops_opened - prev.stops_opened} '
            f'stops_closed={self.stops_closed - prev.stops_closed} '
            f'events={self.events - prev.events} '
            f'dropped={self.dropped - prev.dropped} '
            f'state={state.value} cursor={cursor} backlog={max_raw - cursor}'
        )


def main() -> None:
    """Run the processor loop, recovering from transient errors."""
    parser = argparse.ArgumentParser(description='GPS track processor (denoise / two-tier).')
    parser.add_argument(
        '--rebuild',
        action='store_true',
        help='Truncate the processed tier and reprocess from raw id 0.',
    )
    args = parser.parse_args()

    conn = get_connection()
    print('GPS processor started' + (' (rebuild)' if args.rebuild else ''), flush=True)
    rebuild = args.rebuild
    while True:
        try:
            run(conn, rebuild)
        except KeyboardInterrupt:
            print('GPS processor stopped', flush=True)
            break
        except Exception as e:
            print(
                f'processor error: {e}; retrying in {ERROR_BACKOFF_SECONDS}s',
                file=sys.stderr,
                flush=True,
            )
            time.sleep(ERROR_BACKOFF_SECONDS)
        rebuild = False  # rebuild applies only to the first pass


if __name__ == '__main__':
    main()
