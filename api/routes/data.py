"""Offline-data freshness + the update runner's HTTP surface (data-update plan).

Reads are fully derived (``updater.run.compose_status``: freshness from the
data, run state from ``update_runs`` + pid liveness, runnability from the job
table + staging dir). The write-shaped endpoints never touch the DB from
Flask: POST update spawns the detached runner (which does its own
bookkeeping) and cancel sends it SIGTERM — plan decision 4's "Flask is a pure
reader" holds even here.
"""

from __future__ import annotations

import time
from typing import Any

from flask import Blueprint, Response, jsonify, request

from api.db import get_connection
from updater import runs
from updater.run import JOBS, compose_status, run_state, spawn

data_bp = Blueprint('data', __name__)

#: How long POST update waits for the spawned runner to write its run row.
_SPAWN_WAIT_S = 3.0
_SPAWN_POLL_S = 0.05


@data_bp.get('/api/data/status')
def data_status() -> Response:
    """Every chunk: derived freshness, run state, runnability, active run."""
    conn = get_connection()
    return jsonify(compose_status(conn))


@data_bp.post('/api/data/update/<chunk>')
def data_update(chunk: str) -> tuple[Response, int]:
    """Spawn a detached update run for one chunk.

    Body (optional JSON): ``{"force": true}`` to skip the sanity floor.
    202 with the new run on success; 400 when the chunk isn't runnable
    (no Phase 2 job / staged file missing); 409 when a run is in flight;
    500 with the log tail when the runner died before claiming the slot.
    """
    if chunk not in JOBS:
        return jsonify({'error': f'no runnable job for chunk {chunk!r}'}), 400
    state = run_state(chunk)
    if not state['runnable']:
        return jsonify({'error': f'{chunk} needs a staged file first', 'run': state}), 400
    conn = get_connection()
    if runs.active_run(conn) is not None:
        return jsonify({'error': 'another update run is in flight'}), 409

    body: dict[str, Any] = request.get_json(silent=True) or {}
    proc, log_path = spawn(chunk, force=bool(body.get('force')))
    deadline = time.monotonic() + _SPAWN_WAIT_S
    while time.monotonic() < deadline:
        row = runs.run_for_pid(conn, proc.pid)
        if row is not None:
            return jsonify({'run': row}), 202
        if proc.poll() is not None:
            if proc.returncode == runs.BUSY_EXIT:
                return jsonify({'error': 'another update run is in flight'}), 409
            return jsonify(
                {
                    'error': f'runner exited {proc.returncode} before starting',
                    'log': runs.log_tail(str(log_path)),
                }
            ), 500
        time.sleep(_SPAWN_POLL_S)
    return jsonify({'error': 'runner did not report in time', 'log_path': str(log_path)}), 500


@data_bp.get('/api/data/runs/<int:run_id>')
def data_run(run_id: int) -> tuple[Response, int]:
    """One run's row (derived status) + its log tail (``?lines=``, default 40)."""
    conn = get_connection()
    row = runs.run_row(conn, run_id)
    if row is None:
        return jsonify({'error': 'unknown run'}), 404
    lines = min(request.args.get('lines', default=40, type=int) or 40, 400)
    return jsonify({'run': row, 'log': runs.log_tail(row['log_path'], max_lines=lines)}), 200


@data_bp.post('/api/data/runs/<int:run_id>/cancel')
def data_run_cancel(run_id: int) -> tuple[Response, int]:
    """SIGTERM a running update; the runner records the cancelled outcome."""
    conn = get_connection()
    error = runs.request_cancel(conn, run_id)
    if error == 'unknown run':
        return jsonify({'error': error}), 404
    if error is not None:
        return jsonify({'error': error}), 409
    return jsonify({'cancelled': run_id}), 202
