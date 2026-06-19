"""GPS track processor: derive the processed tier from raw ``gps_points``.

Phase 2 skeleton — **copy-through only** (no denoise yet). It tails raw
``gps_points`` by a persisted id cursor and emits one ``track_points`` row per raw
fix, to validate the plumbing end to end: cursor persistence, WAL concurrency with
the logger, idempotent rebuilds, and deploy. The online denoise filter (software
static hold + line simplification) replaces the copy-through in Phase 3.

Writer invariant: the logger owns raw (``gps_points``, ``receiver_metadata``); this
process owns the processed tier (``track_points``, ``track_events``) only. It never
touches gpsd and never blocks the logger.

Determinism / idempotency (C7): output is a pure function of the raw prefix ordered
by ``id``. Each batch's emits and the cursor advance commit in **one** transaction,
so a crash rolls back to a clean boundary. On startup any provisional rows beyond
the cursor are discarded, then processing resumes — so a restart (or ``--rebuild``)
reproduces identical content. ``id`` is the ordering anchor; the wall-clock
``timestamp`` tracks it (C23) but is never used to order.

Run::

    uv run processor/gps_processor.py
    uv run processor/gps_processor.py --rebuild   # truncate processed tier, reprocess from id 0
"""

import argparse
import math
import sqlite3
import sys
import time

from api.db import get_connection, init_db

CURSOR_KEY = 'last_committed_raw_id'
POLL_INTERVAL_SECONDS = 2.0
BATCH_SIZE = 5000
HEARTBEAT_SECONDS = 60
ERROR_BACKOFF_SECONDS = 5


def get_cursor(conn: sqlite3.Connection) -> int:
    """Return the last committed raw id, or 0 if processing has never run.

    Args:
        conn: Open SQLite connection.

    Returns:
        The raw ``gps_points.id`` up to which the processed tier is finalized.
    """
    row = conn.execute(
        "SELECT value FROM processing_state WHERE key = ?", (CURSOR_KEY,)
    ).fetchone()
    return int(row['value']) if row else 0


def set_cursor(conn: sqlite3.Connection, raw_id: int) -> None:
    """Upsert the committed-cursor value (no commit — caller owns the transaction).

    Args:
        conn: Open SQLite connection.
        raw_id: The raw id up to which the processed tier is now finalized.
    """
    conn.execute(
        "INSERT INTO processing_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (CURSOR_KEY, str(raw_id)),
    )


def discard_provisional(conn: sqlite3.Connection, cursor: int) -> int:
    """Delete processed rows derived from raw beyond the cursor (committed).

    These are rows emitted in a batch whose cursor advance never committed (a
    crash), or — when ``cursor`` is 0 — the entire processed tier for a rebuild.
    Dropping them before resuming is what makes reprocessing idempotent (C7).

    Args:
        conn: Open SQLite connection.
        cursor: The committed cursor; rows with ``src_raw_id`` above it are stale.

    Returns:
        The number of ``track_points`` rows discarded.
    """
    n = conn.execute(
        "DELETE FROM track_points WHERE src_raw_id > ?", (cursor,)
    ).rowcount
    conn.execute("DELETE FROM track_events WHERE src_raw_id > ?", (cursor,))
    conn.commit()
    return n


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


def process_batch(conn: sqlite3.Connection, cursor: int) -> tuple[int, int]:
    """Copy one batch of raw fixes after the cursor into ``track_points`` (1:1).

    Phase 2 copy-through: every raw fix becomes a ``kind='track'`` vertex with
    ``n_raw=1`` and ``importance=0`` (the denoise filter will set these
    meaningfully in Phase 3). The inserts and the cursor advance commit together,
    so the processed tier never diverges from the cursor.

    Args:
        conn: Open SQLite connection.
        cursor: Raw id to resume after.

    Returns:
        A ``(rows_emitted, new_cursor)`` pair. ``new_cursor`` equals ``cursor``
        when there is nothing new.
    """
    rows = conn.execute(
        "SELECT id, timestamp, lat, lon, speed, altitude, track, epx, epy "
        "FROM gps_points WHERE id > ? ORDER BY id LIMIT ?",
        (cursor, BATCH_SIZE),
    ).fetchall()
    if not rows:
        return 0, cursor
    conn.executemany(
        "INSERT INTO track_points "
        "(timestamp, lat, lon, speed, altitude, track, kind, n_raw, importance, "
        "accuracy, dwell_start, dwell_end, radius, src_raw_id) "
        "VALUES (?, ?, ?, ?, ?, ?, 'track', 1, 0, ?, NULL, NULL, NULL, ?)",
        [
            (r['timestamp'], r['lat'], r['lon'], r['speed'], r['altitude'],
             r['track'], _eph(r['epx'], r['epy']), r['id'])
            for r in rows
        ],
    )
    new_cursor = rows[-1]['id']
    set_cursor(conn, new_cursor)
    conn.commit()
    return len(rows), new_cursor


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
    note = f"; discarded {discarded} provisional" if discarded else ""
    print(f"processor: resuming after raw id {cursor}{note}", flush=True)

    window_emitted = 0
    last_heartbeat = time.monotonic()
    while True:
        emitted, cursor = process_batch(conn, cursor)
        window_emitted += emitted

        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_SECONDS:
            max_raw = conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM gps_points"
            ).fetchone()[0]
            print(
                f"heartbeat: emitted={window_emitted} cursor={cursor} "
                f"backlog={max_raw - cursor}",
                flush=True,
            )
            window_emitted = 0
            last_heartbeat = now

        if emitted < BATCH_SIZE:
            # Caught up to the tail; poll. A full batch means more backlog — loop
            # immediately to drain it.
            time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    """Run the processor loop, recovering from transient errors."""
    parser = argparse.ArgumentParser(
        description="GPS track processor (denoise / two-tier)."
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Truncate the processed tier and reprocess from raw id 0.",
    )
    args = parser.parse_args()

    conn = get_connection()
    print("GPS processor started" + (" (rebuild)" if args.rebuild else ""),
          flush=True)
    rebuild = args.rebuild
    while True:
        try:
            run(conn, rebuild)
        except KeyboardInterrupt:
            print("GPS processor stopped", flush=True)
            break
        except Exception as e:
            print(f"processor error: {e}; retrying in {ERROR_BACKOFF_SECONDS}s",
                  file=sys.stderr, flush=True)
            time.sleep(ERROR_BACKOFF_SECONDS)
        rebuild = False  # rebuild applies only to the first pass


if __name__ == '__main__':
    main()
