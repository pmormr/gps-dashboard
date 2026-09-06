"""Tests for the update runner: ``update_runs`` bookkeeping + the CLI lifecycle.

The single-flight lock, dead-pid derivation, and log tailing are pinned with
direct calls (``updater.runs``); the runner's end-to-end row lifecycle
(``updater.run.main``) runs in-process against a stubbed job table — no
subprocesses, no network.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from updater import runs
from updater.run import JOBS, main, run_state


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    """A real main+sidecar pair in tmp with the full schema, plus hermetic dirs."""
    import api.db as db

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'main.db')
    monkeypatch.setattr(db, 'PLACES_DB_PATH', None)
    monkeypatch.setenv('GPS_STAGING_DIR', str(tmp_path / 'staging'))
    monkeypatch.setenv('GPS_UPDATE_LOG_DIR', str(tmp_path / 'update-logs'))
    connection = db.get_connection()
    db.init_db(connection)
    return connection


def _dead_pid() -> int:
    """A pid that is certainly not alive (a just-reaped child's)."""
    proc = subprocess.Popen([sys.executable, '-c', 'pass'])
    proc.wait()
    return proc.pid


def test_acquire_finish_roundtrip(conn: sqlite3.Connection, tmp_path: Path) -> None:
    run_id = runs.acquire(conn, 'satcat', tmp_path / 'run.log')
    assert run_id is not None
    row = runs.run_row(conn, run_id)
    assert row is not None
    assert row['status'] == 'running'  # our own (live) pid
    assert runs.active_run(conn) is not None

    runs.finish(conn, run_id, 'ok', 0)
    row = runs.run_row(conn, run_id)
    assert row is not None
    assert (row['status'], row['exit_code']) == ('ok', 0)
    assert row['finished'] is not None
    assert runs.active_run(conn) is None


def test_acquire_is_single_flight(conn: sqlite3.Connection, tmp_path: Path) -> None:
    first = runs.acquire(conn, 'satcat', tmp_path / 'a.log')
    assert first is not None
    assert runs.acquire(conn, 'nps', tmp_path / 'b.log') is None


def test_acquire_reconciles_dead_open_row(conn: sqlite3.Connection, tmp_path: Path) -> None:
    conn.execute(
        'INSERT INTO update_runs (chunk, started, status, pid, log_path) '
        "VALUES ('nps', '2026-01-01T00:00:00.000Z', 'running', ?, 'x.log')",
        (_dead_pid(),),
    )
    conn.commit()
    run_id = runs.acquire(conn, 'satcat', tmp_path / 'run.log')
    assert run_id is not None, 'a dead open row must not hold the slot'
    stale = conn.execute("SELECT status, finished FROM update_runs WHERE chunk = 'nps'").fetchone()
    assert stale['status'] == 'failed'
    assert stale['finished'] is not None


def test_open_row_with_dead_pid_reads_failed(conn: sqlite3.Connection) -> None:
    conn.execute(
        'INSERT INTO update_runs (chunk, started, status, pid, log_path) '
        "VALUES ('nps', '2026-01-01T00:00:00.000Z', 'running', ?, 'x.log')",
        (_dead_pid(),),
    )
    conn.commit()
    row = runs.run_row(conn, 1)
    assert row is not None
    assert row['status'] == 'failed'
    assert runs.active_run(conn) is None


def test_last_runs_latest_per_chunk(conn: sqlite3.Connection, tmp_path: Path) -> None:
    for status in ('failed', 'ok'):
        run_id = runs.acquire(conn, 'satcat', tmp_path / 'x.log')
        assert run_id is not None
        runs.finish(conn, run_id, status, 0)
    last = runs.last_runs(conn)
    assert last['satcat']['status'] == 'ok'


def test_request_cancel_rejects_finished(conn: sqlite3.Connection, tmp_path: Path) -> None:
    run_id = runs.acquire(conn, 'satcat', tmp_path / 'x.log')
    assert run_id is not None
    runs.finish(conn, run_id, 'ok', 0)
    assert runs.request_cancel(conn, run_id) == 'run is ok, not running'
    assert runs.request_cancel(conn, 999) == 'unknown run'


def test_log_tail(tmp_path: Path) -> None:
    log = tmp_path / 'run.log'
    log.write_text('\n'.join(f'line {i}' for i in range(100)) + '\n')
    tail = runs.log_tail(str(log), max_lines=3)
    assert tail == 'line 97\nline 98\nline 99'
    assert runs.log_tail(str(tmp_path / 'absent.log')) == ''


# --- Runner lifecycle (in-process main) ---------------------------------------------


def test_main_ok_lifecycle(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    monkeypatch.setitem(JOBS, 'satcat', calls.append)
    log = tmp_path / 'run.log'
    assert main(['satcat', '--log', str(log)]) == 0
    assert calls == [False]
    row = runs.run_row(conn, 1)
    assert row is not None
    assert (row['chunk'], row['status'], row['log_path']) == ('satcat', 'ok', str(log))


def test_main_force_reaches_job(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[bool] = []
    monkeypatch.setitem(JOBS, 'satcat', calls.append)
    assert main(['satcat', '--force', '--log', str(tmp_path / 'run.log')]) == 0
    assert calls == [True]


def test_main_failure_marks_row_failed(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(force: bool) -> None:
        raise RuntimeError('no network')

    monkeypatch.setitem(JOBS, 'satcat', boom)
    assert main(['satcat', '--log', str(tmp_path / 'run.log')]) == 1
    row = runs.run_row(conn, 1)
    assert row is not None
    assert (row['status'], row['exit_code']) == ('failed', 1)


def test_main_busy_exits_tempfail(
    conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert runs.acquire(conn, 'nps', tmp_path / 'a.log') is not None
    monkeypatch.setitem(JOBS, 'satcat', lambda force: None)
    assert main(['satcat', '--log', str(tmp_path / 'b.log')]) == runs.BUSY_EXIT
    assert conn.execute('SELECT count(*) FROM update_runs').fetchone()[0] == 1


def test_run_state_staged_gating(conn: sqlite3.Connection, tmp_path: Path) -> None:
    assert run_state('satcat')['runnable'] is True
    assert run_state('terrain') == {
        'supported': False,
        'requires_staged': False,
        'staged': None,
        'runnable': False,
    }
    state = run_state('osm')
    assert (state['runnable'], state['requires_staged']) == (False, True)

    staging = tmp_path / 'staging'
    staging.mkdir()
    (staging / 'osm-places.db').write_bytes(b'stub')
    state = run_state('osm')
    assert state['runnable'] is True
    assert state['staged'] is not None
    assert state['staged']['size_bytes'] == 4
