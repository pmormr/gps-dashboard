"""The detached update runner: ``python -m updater.run <chunk>``.

One entry point for both surfaces (plan decision 6): the Flask route spawns
this module detached (``spawn`` — ``start_new_session`` so a deploy's
gps-dashboard restart can't kill a run in flight), and an SSH session runs it
directly. Jobs call the existing importer functions in-process — the argv
each job builds goes through the importer's own ``parse_args``, so the CLI
flags and the runner can never drift.

Bookkeeping contract (plan decision 4): the runner is the only writer of
``update_runs`` (``updater.runs``). It claims the global single-flight slot
on startup (exit :data:`updater.runs.BUSY_EXIT` when held), captures its
output to a per-run log file, marks SIGTERM/Ctrl+C as ``cancelled``, and
closes its row with the outcome. Flask only ever reads.

Phase 2 jobs (plans/data-update-plan.md): nps/ridb/gnis/satcat fetch + import
directly; osm/wiki/phone import a *staged* file from the staging dir (the
Phase 3 on-Pi OSM/wiki builds don't exist yet). The GNIS-after-OSM invariant
stays warning-driven here — auto-chaining lands with the Phase 3 OSM chain.
"""

from __future__ import annotations

import argparse
import signal
import sqlite3
import subprocess
import sys
import traceback
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any, TextIO

from api.db import get_connection, init_db
from common.cli import run_cli
from updater import fetch, runs
from updater.chunks import status_payload
from updater.paths import log_dir, staging_dir


class StagedFileMissing(RuntimeError):
    """A staged-import job found no transfer file in the staging dir."""


class _Cancelled(BaseException):
    """Raised by the SIGTERM handler to unwind into the cancel path."""


def _places_import(argv: list[str], force: bool) -> None:
    """Run ``tools/import_places.py`` in-process with the given argv."""
    from tools import import_places

    args = import_places.parse_args(argv + (['--force'] if force else []))
    code = import_places.run(args)
    if code != 0:
        raise RuntimeError(f'import_places exited {code}')


def _staged(chunk_id: str) -> Path:
    """The chunk's staged transfer file, or a named failure when absent."""
    path = fetch.staged_file(chunk_id)
    if path is None:
        expected = staging_dir() / fetch.STAGED_NAMES[chunk_id]
        raise StagedFileMissing(f'No staged file for {chunk_id}: expected {expected}')
    return path


def _job_nps(force: bool) -> None:
    """Walk the NPS API and full-replace the ``nps`` slice."""
    _places_import([], force)


def _job_ridb(force: bool) -> None:
    """Download the RIDB export into staging and import it."""
    _places_import(['--ridb-zip', str(fetch.download_ridb())], force)


def _job_gnis(force: bool) -> None:
    """Download the GNIS export into staging and import it (dedupes vs OSM)."""
    _places_import(['--gnis-zip', str(fetch.download_gnis())], force)


def _job_satcat(force: bool) -> None:
    """Force-refresh the CelesTrak SATCAT cache."""
    del force  # nothing destructive to floor-check
    from common.satcat import DEFAULT_CACHE_PATH, load_satcat

    meta = load_satcat(DEFAULT_CACHE_PATH, max_age_h=0.0, fetch=True)
    print(f'{len(meta)} satellites cached to {DEFAULT_CACHE_PATH}', flush=True)


def _job_osm(force: bool) -> None:
    """Merge a staged OSM transfer DB (re-run GNIS after — the UI warns)."""
    _places_import(['--osm-db', str(_staged('osm'))], force)


def _job_wiki(force: bool) -> None:
    """Merge a staged Wikipedia-cache transfer DB."""
    _places_import(['--wiki-db', str(_staged('wiki'))], force)


def _job_phone(force: bool) -> None:
    """Import a staged Google Takeout Timeline export (full replace)."""
    del force  # the phone importer has no sanity floor (single-user tier)
    from tools import import_phone_timeline

    args = import_phone_timeline.parse_args([str(_staged('phone'))])
    code = import_phone_timeline.run(args)
    if code != 0:
        raise RuntimeError(f'import_phone_timeline exited {code}')


#: The Phase 2 job table: chunk id → callable(force). Membership here is what
#: makes a chunk runnable via POST /api/data/update/<chunk> and this CLI.
JOBS: dict[str, Callable[[bool], None]] = {
    'nps': _job_nps,
    'ridb': _job_ridb,
    'gnis': _job_gnis,
    'satcat': _job_satcat,
    'osm': _job_osm,
    'wiki': _job_wiki,
    'phone': _job_phone,
}


def run_state(chunk_id: str) -> dict[str, Any]:
    """Derived runnability for one chunk (feeds the status payload).

    A chunk is runnable when a job exists for it and, for staged-import
    chunks, a staged file is actually present.
    """
    supported = chunk_id in JOBS
    requires_staged = chunk_id in fetch.STAGED_NAMES
    staged = fetch.staged_detail(chunk_id) if requires_staged else None
    return {
        'supported': supported,
        'requires_staged': requires_staged,
        'staged': staged,
        'runnable': supported and (not requires_staged or staged is not None),
    }


def _brief(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Trim a run row to the fields the status payload carries."""
    if row is None:
        return None
    return {k: row[k] for k in ('id', 'chunk', 'status', 'started', 'finished', 'exit_code')}


def compose_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """The full ``/api/data/status`` document: freshness + runs + runnability.

    Everything stays derived at read time: freshness from the data
    (``updater.chunks``), run state from ``update_runs`` + pid liveness
    (``updater.runs``), runnability from the job table + staging dir.

    Args:
        conn: An open :func:`api.db.get_connection` connection.

    Returns:
        The Phase 1 payload with ``run``/``last_run`` per chunk and a
        top-level ``active_run``.
    """
    payload = status_payload(conn)
    last = runs.last_runs(conn)
    for entry in payload['chunks']:
        entry['run'] = run_state(entry['id'])
        entry['last_run'] = _brief(last.get(entry['id']))
    payload['active_run'] = _brief(runs.active_run(conn))
    return payload


def spawn(chunk: str, force: bool) -> tuple[subprocess.Popen[bytes], Path]:
    """Launch a detached runner for ``chunk`` (the Flask route's side).

    The child gets its own session (a deploy restart of gps-dashboard leaves
    it running), unbuffered output redirected into a fresh per-run log file,
    and ``--log`` so its run row records that same path.

    Args:
        chunk: A key of :data:`JOBS`.
        force: Pass ``--force`` through to the job.

    Returns:
        The child process handle and the log path.
    """
    logs = log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
    log_path = logs / f'{stamp}-{chunk}.log'
    cmd = [sys.executable, '-u', '-m', 'updater.run', chunk, '--log', str(log_path)]
    if force:
        cmd.append('--force')
    with log_path.open('ab') as fh:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=fh,
            start_new_session=True,
        )
    return proc, log_path


class _Tee:
    """Minimal write/flush fan-out so CLI runs land on both TTY and log."""

    def __init__(self, *streams: TextIO) -> None:
        self._streams = streams

    def write(self, data: str) -> int:
        for stream in self._streams:
            stream.write(data)
        return len(data)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()


def _open_log(explicit: str | None) -> tuple[Path, TextIO | None]:
    """Resolve this run's log path, teeing output into it for CLI runs.

    A spawned runner arrives with ``--log`` and its output already redirected
    there; a CLI run creates its own log file and mirrors stdout/stderr into
    it so the run is inspectable from the UI either way.

    Args:
        explicit: The ``--log`` value, or None for a CLI run.

    Returns:
        The log path, plus the tee'd file handle to close on exit (None when
        output was already redirected).
    """
    if explicit is not None:
        return Path(explicit), None
    logs = log_dir()
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime('%Y%m%d-%H%M%S')
    log_path = logs / f'{stamp}-cli.log'
    fh = log_path.open('a', buffering=1)
    sys.stdout = _Tee(sys.stdout, fh)
    sys.stderr = _Tee(sys.stderr, fh)
    return log_path, fh


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the runner's arguments.

    Args:
        argv: Explicit argument list (tests); None reads ``sys.argv``.

    Returns:
        The parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('chunk', choices=sorted(JOBS), help='which chunk to update')
    parser.add_argument(
        '--force',
        action='store_true',
        help='Skip the sanity floor guarding destructive full-replaces',
    )
    parser.add_argument(
        '--log',
        help='Record this log path in the run row (the spawning route redirects '
        'output there itself); default: create a log file and tee into it',
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one chunk update end to end, with full run-row bookkeeping.

    Args:
        argv: Explicit argument list (tests); None reads ``sys.argv``.

    Returns:
        The process exit code: 0 ok, 1 failed, 130 cancelled,
        :data:`updater.runs.BUSY_EXIT` when another run holds the slot.
    """
    args = parse_args(argv)
    log_path, tee_fh = _open_log(args.log)
    conn = get_connection()
    init_db(conn)
    run_id = runs.acquire(conn, args.chunk, log_path)
    if run_id is None:
        print('Another update run is in flight; retry when it finishes.', file=sys.stderr)
        return runs.BUSY_EXIT

    def _on_sigterm(signum: int, frame: FrameType | None) -> None:
        raise _Cancelled

    signal.signal(signal.SIGTERM, _on_sigterm)
    print(f'== update {args.chunk} (run {run_id}) ==', flush=True)
    try:
        JOBS[args.chunk](args.force)
    except (_Cancelled, KeyboardInterrupt):
        runs.finish(conn, run_id, 'cancelled', 130)
        print('\nCancelled.', flush=True)
        return 130
    except Exception:
        traceback.print_exc()
        runs.finish(conn, run_id, 'failed', 1)
        print(f'== update {args.chunk} failed ==', flush=True)
        return 1
    finally:
        if tee_fh is not None:
            tee_fh.flush()
    runs.finish(conn, run_id, 'ok', 0)
    print(f'== update {args.chunk} ok ==', flush=True)
    return 0


if __name__ == '__main__':
    run_cli(lambda: main())
