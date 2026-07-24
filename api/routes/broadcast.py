"""Broadcast tier routes — the event-day feed config reference (Broadcast tab).

``GET /api/broadcast/feeds`` renders the declarative feed registry
(``broadcast.feeds``) with the ``GPS_BROADCAST_*`` secrets interpolated
**server-side** from the process environment (loaded from
``/etc/default/gps-broadcast`` via the unit's ``EnvironmentFile`` — B3), so the
browser on the trusted LAN gets finished, copy-ready send/OBS strings without the
secrets ever living in the public repo. Fully local + offline (B4): the config is
present even with both hubs down, which is exactly when it's needed. Live status,
snapshots, and logs are separate endpoints (Phase 2+).
"""

from __future__ import annotations

import os

from flask import Blueprint, Response, jsonify

from broadcast.feeds import render_feeds

broadcast_bp = Blueprint('broadcast', __name__)


@broadcast_bp.get('/api/broadcast/feeds')
def broadcast_feeds() -> Response:
    """Every feed's copy-ready config, secrets interpolated server-side."""
    return jsonify(render_feeds(os.environ))
