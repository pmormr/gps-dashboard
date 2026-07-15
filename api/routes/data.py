"""Offline-data freshness: the /data drill-in's read (plans/data-update-plan.md).

One endpoint serving the whole chunk registry with freshness derived at read
time (``updater.chunks``) — the "am I ready to go dark?" glance. Read-only:
the runner endpoints (POST update/cancel, run log tails) arrive with Phase 2.
"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify

from api.db import get_connection
from updater.chunks import status_payload

data_bp = Blueprint('data', __name__)


@data_bp.get('/api/data/status')
def data_status() -> Response:
    """Every registered chunk: derived freshness, staleness state, ordering warnings."""
    conn = get_connection()
    return jsonify(status_payload(conn))
