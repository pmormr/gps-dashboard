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

from flask import Blueprint, Response, abort, jsonify, request

from broadcast.feeds import FEEDS, render_feeds
from broadcast.snapshots import get_manager
from broadcast.status import feed_status
from common import proc
from common.mediamtx import fetch_paths
from common.timefmt import now_canonical

broadcast_bp = Blueprint('broadcast', __name__)

#: Van feeds that carry video and so can be snapshotted (radio is audio-only).
_SNAPSHOTTABLE = frozenset(f.path for f in FEEDS if f.hub == 'van' and f.slot_group != 'radio')

#: Log-tail bounds for the raw journal panel (B11).
_LOG_LINES_DEFAULT = 200
_LOG_LINES_MAX = 1000


@broadcast_bp.get('/api/broadcast/feeds')
def broadcast_feeds() -> Response:
    """Every feed's copy-ready config, secrets interpolated server-side."""
    return jsonify(render_feeds(os.environ))


@broadcast_bp.get('/api/broadcast/status')
def broadcast_status() -> Response:
    """Two-sides live status per feed, merged onto the registry (B6).

    The van hub reads its localhost control API; each van feed gets its
    ingest/egress/codec state. The cloud hub is marked unreachable until the WG
    control tunnel lands (P3) — an unreachable hub is the normal resting state
    (off-grid), reported as ``reachable: false``, never a failure.
    """
    van_states = fetch_paths()
    van_reachable = van_states is not None
    by_name = {s.name: s for s in van_states} if van_states else {}
    cloud_configured = bool(os.environ.get('GPS_BROADCAST_CLOUD_URL'))
    # The wall's own snapshotter pulls over RTSP and counts as a reader; discount it.
    snap_paths = get_manager().active_paths()

    feeds = []
    for feed in FEEDS:
        if feed.hub == 'van':
            if van_reachable:
                self_readers = 1 if feed.path in snap_paths else 0
                status = feed_status(feed, by_name.get(feed.path), self_readers)
            else:
                status = {'reachable': False}
        else:  # cloud — reached over the WG tunnel in P3
            status = {'reachable': False}
        feeds.append({'hub': feed.hub, 'path': feed.path, **status})

    return jsonify(
        {
            'generated_at': now_canonical(),
            'hubs': {
                'van': {'reachable': van_reachable},
                'cloud': {'reachable': False, 'configured': cloud_configured},
            },
            'feeds': feeds,
        }
    )


@broadcast_bp.get('/api/broadcast/snapshot/<name>')
def broadcast_snapshot(name: str) -> Response:
    """A rolling downscaled JPEG for one van feed's monitor-wall tile (B9).

    ``name`` is gated to the known snapshottable van paths (never an arbitrary
    RTSP target). 202 while a worker is still warming up (no frame yet), 404 for
    an unknown/audio-only path.
    """
    if name not in _SNAPSHOTTABLE:
        abort(404)
    jpeg = get_manager().request(name)
    if jpeg is None:
        return Response(status=202)  # warming up — the tile keeps polling
    return Response(jpeg.read_bytes(), mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})


@broadcast_bp.get('/api/broadcast/logs')
def broadcast_logs() -> Response:
    """Recent MediaMTX journal lines — the raw diagnostic escape hatch (B11).

    Van hub only for now (read locally via ``journalctl``; no sudo needed — the
    service user is in ``adm``). The cloud hub's log endpoint arrives over the WG
    tunnel in P3, so a non-van hub reports ``reachable: false``.
    """
    hub = request.args.get('hub', 'van')
    try:
        n = int(request.args.get('lines', _LOG_LINES_DEFAULT))
    except (TypeError, ValueError):
        n = _LOG_LINES_DEFAULT
    n = max(1, min(n, _LOG_LINES_MAX))

    if hub != 'van':
        return jsonify({'hub': hub, 'reachable': False, 'lines': []})

    rc, out, _ = proc.run(
        ['journalctl', '-u', 'mediamtx', '-n', str(n), '--no-pager', '-o', 'cat'],
        timeout=6,
    )
    if rc != 0:
        return jsonify({'hub': hub, 'reachable': False, 'lines': []})
    return jsonify({'hub': hub, 'reachable': True, 'lines': out.splitlines()})
