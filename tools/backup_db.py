"""Back up the GPS history DB to rex-nas, opportunistically.

The van is frequently off-grid; rex-nas is reachable when parked at home (HaLow)
or on-grid via WireGuard. Each run:

1. Takes a consistent Pi-side snapshot via the SQLite backup API (safe against
   the live writers, preserves page layout so rsync deltas stay cheap — never
   ``VACUUM INTO``, which rewrites pages and defeats the delta transfer). The
   snapshot doubles as an on-NVMe corruption-recovery point.
2. Preflights the NAS over SSH; unreachable (boondocking) is a clean no-op.
3. rsyncs the snapshot ``--inplace`` onto ``<remote-dir>/current/gps_history.db``
   — an append-mostly DB transfers only changed pages (HaLow-friendly). rsync
   addresses the NAS by ``--rsync-dir`` (module-relative), not ``--remote-dir``
   — see :func:`push_current` for the UGOS path-validation trap.
4. Makes the day's dated copy ``<remote-dir>/history/gps_history-YYYY-MM-DD.db``
   NAS-side (``cp --reflink=auto``: free extent sharing on btrfs, plain copy
   elsewhere; no extra wire cost). First successful run of the UTC day wins.
5. Prunes dated copies: keeps the most recent ``--keep-daily`` days plus the
   latest copy in each of the most recent ``--keep-weekly`` ISO weeks.

Driven by ``deploy/gps-db-backup.timer`` (6-hourly oneshot, same pattern as
``gps-drone-sync``).

Restore procedure: stop the writers (``gps-logger``, ``mqtt-ingest``,
``gps-processor``, ``gps-dashboard``), copy the chosen dated file from the NAS
back to ``/mnt/nvme/data/gps_history.db`` (remove stale ``-wal``/``-shm``
siblings), run ``PRAGMA integrity_check;`` before trusting it, restart.

The ``places.db`` sidecar (beside the main DB) is deliberately NOT in this
backup path: the places tier is fully rebuildable from public downloads. Its
rebuild recipe is the importers, per source — ``tools/import_places.py`` (NPS
over WAN; RIDB via ``--ridb-zip``; later OSM/Overture merges per
``plans/attractions-poi-plan.md``). A lost sidecar costs a re-import, never
data.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sqlite3
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from common.cli import run_cli

DB_NAME = 'gps_history.db'
DATED_RE = re.compile(r'^gps_history-(\d{4})-(\d{2})-(\d{2})\.db$')


# --- Pure helpers ----------------------------------------------------------------


def dated_name(day: date) -> str:
    """Return the dated-copy filename for a backup day.

    Args:
        day: The (UTC) day the copy represents.

    Returns:
        A name like ``gps_history-2026-07-07.db``.
    """
    return f'gps_history-{day.isoformat()}.db'


def parse_dated_name(name: str) -> date | None:
    """Parse a dated-copy filename back to its day.

    Args:
        name: A directory entry from the NAS ``history/`` dir.

    Returns:
        The day, or None for anything that isn't a dated copy (foreign files
        are left alone by the prune).
    """
    m = DATED_RE.match(name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def prune_keep(present: list[date], keep_daily: int, keep_weekly: int) -> set[date]:
    """Select which dated copies to keep.

    Keeps the ``keep_daily`` most recent days, plus the latest day within each
    of the ``keep_weekly`` most recent ISO weeks that have any copy. Weeks are
    counted by presence, not by calendar distance, so a long off-grid gap never
    ages the weekly history out.

    Args:
        present: Days that currently have a dated copy (any order).
        keep_daily: How many most-recent days to keep outright.
        keep_weekly: How many most-recent represented ISO weeks to keep one
            copy (the latest) of.

    Returns:
        The subset of ``present`` to keep; everything else is prunable.
    """
    days = sorted(set(present), reverse=True)
    keep = set(days[:keep_daily])
    latest_per_week: dict[tuple[int, int], date] = {}
    for day in days:
        week = day.isocalendar()[:2]
        if week not in latest_per_week:
            latest_per_week[week] = day
    for week in sorted(latest_per_week, reverse=True)[:keep_weekly]:
        keep.add(latest_per_week[week])
    return keep


# --- Steps -----------------------------------------------------------------------


def snapshot(db_path: Path, snap_path: Path) -> None:
    """Write a consistent snapshot of a live DB via the SQLite backup API.

    Backs up into a temp sibling and atomically replaces the previous snapshot,
    so the on-NVMe recovery point is never half-written.

    Args:
        db_path: The live database.
        snap_path: Where the snapshot lands.
    """
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = snap_path.with_name(snap_path.name + '.tmp')
    src = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    os.replace(tmp, snap_path)


def preflight(host: str) -> bool:
    """Whether the NAS answers over SSH (non-interactively, quickly).

    Args:
        host: The SSH destination (an ssh_config alias or user@host).

    Returns:
        True iff a trivial remote command succeeds.
    """
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', host, 'true'],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def remote_sh(host: str, script: str, timeout: float = 60) -> tuple[int, str, str]:
    """Run a shell snippet on the NAS.

    Args:
        host: The SSH destination.
        script: The shell command line to run remotely.
        timeout: Seconds before the SSH call is killed.

    Returns:
        ``(returncode, stdout, stderr)``.
    """
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', host, script],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def push_current(host: str, snap_path: Path, rsync_dir: str) -> None:
    """rsync the snapshot onto the NAS ``current/`` copy, delta-transferred.

    Args:
        host: The SSH destination.
        snap_path: The local snapshot file.
        rsync_dir: The backup root as rsync must address it. UGOS's patched
            rsync rejects absolute paths even over SSH transport and resolves
            relative paths through the ``/etc/rsyncd.conf`` module table, so
            this is module-relative (``backups/...``) while the plain SSH
            steps (mkdir/cp/ls/rm) keep the absolute ``--remote-dir``.

    Raises:
        RuntimeError: If rsync fails.
    """
    dest = f'{host}:{rsync_dir}/current/{DB_NAME}'
    result = subprocess.run(
        ['rsync', '--inplace', '--timeout=300', '-e', 'ssh -o BatchMode=yes', str(snap_path), dest],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f'rsync failed ({result.returncode}): {result.stderr.strip()}')


def ensure_dated_copy(host: str, remote_dir: str, day: date) -> bool:
    """Make the day's dated copy on the NAS if it doesn't exist yet.

    Args:
        host: The SSH destination.
        remote_dir: The NAS backup root.
        day: The (UTC) day to stamp.

    Returns:
        True if a new dated copy was created, False if today's already existed.

    Raises:
        RuntimeError: If the remote copy fails.
    """
    src = shlex.quote(f'{remote_dir}/current/{DB_NAME}')
    dst = shlex.quote(f'{remote_dir}/history/{dated_name(day)}')
    code, out, err = remote_sh(
        host,
        f'if [ -e {dst} ]; then echo exists; else cp --reflink=auto {src} {dst}; fi',
        timeout=300,
    )
    if code != 0:
        raise RuntimeError(f'dated copy failed ({code}): {err.strip()}')
    return 'exists' not in out


def prune_remote(host: str, remote_dir: str, keep_daily: int, keep_weekly: int) -> list[str]:
    """Prune dated copies on the NAS down to the retention policy.

    Args:
        host: The SSH destination.
        remote_dir: The NAS backup root.
        keep_daily: Days to keep outright.
        keep_weekly: Represented ISO weeks to keep one copy of.

    Returns:
        The filenames removed (possibly empty).

    Raises:
        RuntimeError: If listing or removal fails.
    """
    hist = f'{remote_dir}/history'
    code, out, err = remote_sh(host, f'ls -1 {shlex.quote(hist)}')
    if code != 0:
        raise RuntimeError(f'history listing failed ({code}): {err.strip()}')
    by_day = {d: name for name in out.split() if (d := parse_dated_name(name)) is not None}
    keep = prune_keep(list(by_day), keep_daily, keep_weekly)
    doomed = sorted(by_day[d] for d in by_day if d not in keep)
    if doomed:
        paths = ' '.join(shlex.quote(f'{hist}/{name}') for name in doomed)
        code, _, err = remote_sh(host, f'rm -f {paths}')
        if code != 0:
            raise RuntimeError(f'prune failed ({code}): {err.strip()}')
    return doomed


# --- Orchestration ---------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    """Snapshot, push, date, prune — the backup body.

    Args:
        args: Parsed arguments.

    Returns:
        A process exit code (0 includes the NAS-unreachable no-op).
    """
    db_path = Path(args.db)
    snap_path = Path(args.snap)
    print(f'Snapshotting {db_path} -> {snap_path}', flush=True)
    snapshot(db_path, snap_path)
    size_mb = snap_path.stat().st_size / 1e6
    print(f'Snapshot done ({size_mb:.0f} MB)', flush=True)

    if not preflight(args.ssh):
        print('Backup host unreachable — snapshot kept locally, nothing to push.', flush=True)
        return 0

    remote_dir = args.remote_dir.rstrip('/')
    subdirs = ' '.join(shlex.quote(f'{remote_dir}/{sub}') for sub in ('current', 'history'))
    code, _, err = remote_sh(args.ssh, f'mkdir -p {subdirs}')
    if code != 0:
        print(f'Remote mkdir failed ({code}): {err.strip()}', file=sys.stderr, flush=True)
        return 1

    print(f'Pushing to {args.ssh}:{args.rsync_dir}/current/{DB_NAME}', flush=True)
    push_current(args.ssh, snap_path, args.rsync_dir.rstrip('/'))

    today = datetime.now(UTC).date()
    if ensure_dated_copy(args.ssh, remote_dir, today):
        print(f'Dated copy created: {dated_name(today)}', flush=True)

    removed = prune_remote(args.ssh, remote_dir, args.keep_daily, args.keep_weekly)
    if removed:
        print(f'Pruned {len(removed)} old cop(ies): {", ".join(removed)}', flush=True)
    print('Backup complete', flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        The parsed namespace.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--db',
        default=os.environ.get('GPS_DB_PATH', str(Path.home() / DB_NAME)),
        help='live database to back up (default: $GPS_DB_PATH)',
    )
    parser.add_argument(
        '--snap',
        default='/mnt/nvme/backup/gps_history.snap.db',
        help='local snapshot path (doubles as the on-NVMe recovery point)',
    )
    parser.add_argument('--ssh', default='rex-nas', help='SSH destination for the NAS')
    parser.add_argument(
        '--remote-dir',
        default='/volume1/backups/gps-dashboard',
        help='NAS backup root (current/ + history/ live under it), as SSH commands see it',
    )
    parser.add_argument(
        '--rsync-dir',
        default='backups/gps-dashboard',
        help='the same root as rsync must address it — UGOS rsync rejects absolute'
        ' paths and maps relative ones via its rsyncd module table',
    )
    parser.add_argument('--keep-daily', type=int, default=7, help='recent days to keep')
    parser.add_argument(
        '--keep-weekly', type=int, default=8, help='recent represented ISO weeks to keep'
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint.

    Returns:
        A process exit code.
    """
    args = parse_args()
    try:
        return run(args)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f'Backup failed: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    run_cli(main)
