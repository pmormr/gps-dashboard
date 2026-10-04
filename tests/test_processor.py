"""Tests for the denoise state machine's data-gap handling (processor/gps_processor.py).

Drives the pure ``TrackFilter`` with synthetic fixes, plus the DB round-trip
(``apply_drain`` / ``reconstruct_anchor``) for the restart-replay invariant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from api.db import get_connection
from common.timefmt import format_canonical
from processor.gps_processor import (
    DrainResult,
    Fix,
    Thresholds,
    TrackFilter,
    apply_drain,
    discard_provisional,
    reconstruct_anchor,
)

T0 = datetime(2026, 10, 3, 15, 46, 0, tzinfo=UTC)

#: Degrees of longitude per second at ~25 m/s near 39.26°N (the drive direction).
LON_PER_S = 25.0 / 86_200.0


def _fix(fix_id: int, t: float, lon_s: float, speed: float = 25.0, lat: float = 39.26) -> Fix:
    """Build a fix ``t`` seconds after T0, ``lon_s`` seconds of driving east of the origin."""
    return Fix(
        id=fix_id,
        timestamp=format_canonical(T0 + timedelta(seconds=t)),
        lat=lat,
        lon=-76.6 + lon_s * LON_PER_S,
        speed=speed,
        altitude=10.0,
        track=90.0,
        eph=2.0,
    )


def _drive(start_id: int, start_t: float, seconds: int) -> list[Fix]:
    """A straight 1 Hz drive east: one fix per second, position tracking time."""
    return [_fix(start_id + i, start_t + i, start_t + i) for i in range(seconds)]


def _run(fixes: list[Fix], filt: TrackFilter | None = None) -> tuple[TrackFilter, DrainResult]:
    """Feed fixes through a (fresh) filter and drain it."""
    filt = filt or TrackFilter(Thresholds(), cursor=0, anchor=None)
    for fix in fixes:
        filt.feed(fix)
    return filt, filt.drain()


def _gaps(result: DrainResult) -> list[tuple[str, str | None, float | None]]:
    """The finalized ``gap`` events as ``(start, end, seconds)``."""
    return [
        (e.timestamp, e.end_time, e.magnitude) for e in result.finalized_events if e.type == 'gap'
    ]


def test_continuous_drive_has_no_gap():
    """Fixes arriving every second never break the trail."""
    _, result = _run(_drive(1, 0, 120))

    assert _gaps(result) == []


def test_hole_while_moving_breaks_the_trail():
    """A tunnel-length hole ends the trail at the last fix and restarts it at the next."""
    before, after = _drive(1, 0, 30), _drive(100, 127, 30)
    _, result = _run(before + after)

    assert _gaps(result) == [(before[-1].timestamp, after[0].timestamp, 98.0)]
    stamps = [r.timestamp for r in result.finalized_tracks]
    assert before[-1].timestamp in stamps and after[0].timestamp in stamps
    # Both ends rank by the jump they bound, so decimation keeps the break honest.
    ends = [
        r
        for r in result.finalized_tracks
        if r.timestamp in (before[-1].timestamp, after[0].timestamp)
    ]
    assert all(r.importance > 2000 for r in ends)
    # The gap is keyed to the first post-gap fix, which is committed.
    assert result.cursor >= after[0].id


def test_hole_shorter_than_threshold_is_bridged():
    """A hole under ``gap_min_seconds`` stays one continuous segment."""
    _, result = _run(_drive(1, 0, 30) + _drive(100, 38, 30))

    assert _gaps(result) == []


def test_parked_hole_resuming_in_place_is_one_dwell():
    """The Pi off overnight while parked is still a single stop, not a gap."""
    parked = [_fix(i, i, 0, speed=0.0) for i in range(1, 60)]
    resumed = [_fix(100 + i, 40_000 + i, 0, speed=0.0) for i in range(30)]
    filt, result = _run(parked + resumed)

    assert _gaps(result) == []
    assert filt.stops_opened == 1 and filt.stops_closed == 0
    assert result.provisional_tracks[0].dwell_end == resumed[-1].timestamp


def test_parked_hole_resuming_elsewhere_closes_the_stop_and_gaps():
    """Parked, then next heard from far away: the dwell ends at its last fix."""
    parked = [_fix(i, i, 0, speed=0.0) for i in range(1, 60)]
    after = _drive(100, 40_000, 20)
    filt, result = _run(parked + after)

    assert _gaps(result) == [(parked[-1].timestamp, after[0].timestamp, 39_941.0)]
    assert filt.stops_closed == 1
    stop = next(r for r in result.finalized_tracks if r.kind == 'stop')
    assert stop.dwell_end == parked[-1].timestamp


def test_slow_first_fix_after_gap_does_not_double_as_a_stop_start():
    """A slow post-gap fix is committed as a vertex; the dwell opens on the next fix."""
    before = _drive(1, 0, 30)
    crawl = [_fix(100 + i, 50_000 + i, 9_000, speed=0.2) for i in range(60)]
    filt, result = _run(before + crawl)

    assert len(_gaps(result)) == 1
    assert filt.stops_opened == 1
    assert result.provisional_events[0].src_raw_id > result.cursor


def test_restart_after_gap_replays_identically(client):
    """Resuming from the committed cursor reproduces the steady-state output exactly."""
    fixes = (
        _drive(1, 0, 30)
        + _drive(100, 127, 90)
        + [_fix(300 + i, 217 + i, 217, speed=0.0) for i in range(120)]
        + _drive(500, 5_000, 40)
    )
    split = 45

    def rows(conn) -> tuple[list[tuple], list[tuple]]:
        tracks = conn.execute(
            'SELECT timestamp, lat, lon, kind, n_raw, importance, src_raw_id '
            'FROM track_points ORDER BY src_raw_id, kind'
        ).fetchall()
        events = conn.execute(
            'SELECT timestamp, end_time, type, magnitude, src_raw_id '
            'FROM track_events ORDER BY src_raw_id, type'
        ).fetchall()
        return [tuple(r) for r in tracks], [tuple(r) for r in events]

    conn = get_connection()
    _, straight = _run(fixes)
    apply_drain(conn, 0, straight)
    expected = rows(conn)
    discard_provisional(conn, 0)

    # Same input, but the process dies after `split` fixes and resumes from its cursor.
    _, first = _run(fixes[:split])
    apply_drain(conn, 0, first)
    discard_provisional(conn, first.cursor)
    resumed = TrackFilter(Thresholds(), first.cursor, reconstruct_anchor(conn, first.cursor))
    _, second = _run([f for f in fixes if f.id > first.cursor], resumed)
    apply_drain(conn, first.cursor, second)

    assert rows(conn) == expected
    assert [e[2] for e in expected[1]].count('gap') == 2
