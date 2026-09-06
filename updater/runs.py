"""``update_runs`` bookkeeping + derived run status.

The table (main DB, ``api.db``) is history and single-flight state only —
never a freshness source (plan decision 2). The detached runner
(``updater.run``) is the only writer: it acquires the single-flight slot with
a check-and-insert inside one ``BEGIN IMMEDIATE`` transaction and closes its
row on exit. Flask reads through the derivation helpers here: an open row
(``finished IS NULL``) whose pid is dead *reads* as failed without anyone
writing — the next runner start reconciles it into a stored ``failed`` row.
"""

from __future__ import annotations

import os
import signal
import sqlite3
from pathlib import Path
from typing import Any

from common.timefmt import now_canonical

#: The runner's exit code when another run holds the single-flight slot
#: (EX_TEMPFAIL — lets the spawning route tell "busy" from a real failure).
BUSY_EXIT = 75

_TAIL_BYTES = 64 * 1024


def pid_alive(pid: int) -> bool:
    """Whether ``pid`` is a live process (signal-0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _row_dict(row: sqlite3.Row | tuple[Any, ...], columns: list[str]) -> dict[str, Any]:
    """Materialize one ``update_runs`` row with derived status.

    Stored status stands for finished rows; an open row derives ``running``
    or ``failed`` from pid liveness.
    """
    d = dict(zip(columns, row, strict=True))
    if d['finished'] is None:
        d['status'] = 'running' if pid_alive(d['pid']) else 'failed'
    return d


_COLUMNS = ['id', 'chunk', 'started', 'finished', 'status', 'exit_code', 'pid', 'log_path']
_SELECT = f'SELECT {", ".join(_COLUMNS)} FROM update_runs'


def acquire(conn: sqlite3.Connection, chunk: str, log_path: Path) -> int | None:
    """Claim the global single-flight slot and insert this run's row.

    One ``BEGIN IMMEDIATE`` transaction covers reconcile + check + insert, so
    two concurrent runners cannot both claim the slot. Open rows with dead
    pids are reconciled to ``failed`` here (the runner is the writer; reads
    only ever derive).

    Args:
        conn: The runner's own main-DB connection (no open transaction).
        chunk: The chunk id this run updates.
        log_path: Where this run's output is captured.

    Returns:
        The new run row's id, or None when a live run already holds the slot.
    """
    conn.execute('BEGIN IMMEDIATE')
    try:
        open_rows = conn.execute(
            'SELECT id, pid FROM update_runs WHERE finished IS NULL'
        ).fetchall()
        now = now_canonical()
        for open_id, open_pid in open_rows:
            if pid_alive(open_pid):
                conn.execute('ROLLBACK')
                return None
            conn.execute(
                "UPDATE update_runs SET finished = ?, status = 'failed' WHERE id = ?",
                (now, open_id),
            )
        cursor = conn.execute(
            'INSERT INTO update_runs (chunk, started, status, pid, log_path) '
            "VALUES (?, ?, 'running', ?, ?)",
            (chunk, now, os.getpid(), str(log_path)),
        )
        conn.execute('COMMIT')
    except BaseException:
        conn.execute('ROLLBACK')
        raise
    run_id = cursor.lastrowid
    assert run_id is not None
    return run_id


def finish(conn: sqlite3.Connection, run_id: int, status: str, exit_code: int) -> None:
    """Close a run row with its outcome (``ok``/``failed``/``cancelled``)."""
    conn.execute(
        'UPDATE update_runs SET finished = ?, status = ?, exit_code = ? WHERE id = ?',
        (now_canonical(), status, exit_code, run_id),
    )
    conn.commit()


def run_row(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    """One run's row with derived status, or None when unknown."""
    row = conn.execute(f'{_SELECT} WHERE id = ?', (run_id,)).fetchone()
    return _row_dict(row, _COLUMNS) if row else None


def run_for_pid(conn: sqlite3.Connection, pid: int) -> dict[str, Any] | None:
    """The newest run row written by ``pid`` (the spawn route's handshake)."""
    row = conn.execute(f'{_SELECT} WHERE pid = ? ORDER BY id DESC LIMIT 1', (pid,)).fetchone()
    return _row_dict(row, _COLUMNS) if row else None


def request_cancel(conn: sqlite3.Connection, run_id: int) -> str | None:
    """SIGTERM a running update (the runner marks itself cancelled).

    Args:
        conn: An open main-DB connection (read-only here — no row writes).
        run_id: The run to cancel.

    Returns:
        None on success, else a human-readable reason the cancel didn't apply.
    """
    row = run_row(conn, run_id)
    if row is None:
        return 'unknown run'
    if row['status'] != 'running':
        return f'run is {row["status"]}, not running'
    try:
        os.kill(row['pid'], signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return 'process already gone'
    return None


def active_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The run currently holding the single-flight slot, or None.

    Only a *live* open row counts — an open row with a dead pid is a stale
    crash marker, not an active run.
    """
    rows = conn.execute(f'{_SELECT} WHERE finished IS NULL ORDER BY id DESC').fetchall()
    for row in rows:
        d = _row_dict(row, _COLUMNS)
        if d['status'] == 'running':
            return d
    return None


def last_runs(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """The most recent run per chunk (derived status), keyed by chunk id."""
    rows = conn.execute(
        f'{_SELECT} WHERE id IN (SELECT max(id) FROM update_runs GROUP BY chunk)'
    ).fetchall()
    return {d['chunk']: d for d in (_row_dict(r, _COLUMNS) for r in rows)}


def log_tail(log_path: str, max_lines: int = 40) -> str:
    """The last ``max_lines`` lines of a run's log file ('' when unreadable).

    Reads at most the trailing 64 KB — run logs are progress lines, so the
    tail is always within that.
    """
    path = Path(log_path)
    try:
        with path.open('rb') as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            data = fh.read()
    except OSError:
        return ''
    text = data.decode('utf-8', errors='replace')
    lines = text.splitlines()
    if size > _TAIL_BYTES and lines:
        lines = lines[1:]
    return '\n'.join(lines[-max_lines:])
