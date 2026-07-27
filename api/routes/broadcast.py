"""Broadcast tier routes — the event-day feed config reference + live status.

``GET /api/broadcast/feeds`` renders the declarative feed registry
(``broadcast.feeds``) with the ``GPS_BROADCAST_*`` secrets interpolated
**server-side** from the process environment (loaded from
``/etc/default/gps-broadcast`` via the unit's ``EnvironmentFile`` — B3), so the
browser on the trusted LAN gets finished, copy-ready send/OBS strings without the
secrets ever living in the public repo. Fully local + offline (B4): the config is
present even with both hubs down, which is exactly when it's needed.

``GET /api/broadcast/status`` merges the two-sides live state (B6) onto the
registry for **both** hubs: the van hub over its localhost control API, the cloud
hub over the WG control tunnel (B7). ``snapshot`` and ``logs`` serve the van hub
locally and **proxy** the cloud hub through the cloud agent (``broadcast/
cloud_agent.py``) — LAN browsers can't reach the WG address, so Flask fetches over
the tunnel on their behalf. An unreachable cloud hub (off-grid / tunnel down) is
the normal resting state, reported ``reachable: false``, never a failure.
"""

from __future__ import annotations

import os

import requests
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

#: Cloud feeds that carry video (phones + drones) — the cloud snapshot proxy gate.
_CLOUD_SNAPSHOTTABLE = frozenset(
    f.path for f in FEEDS if f.hub == 'cloud' and f.slot_group != 'radio'
)

#: Log-tail bounds for the raw journal panel (B11).
_LOG_LINES_DEFAULT = 200
_LOG_LINES_MAX = 1000

#: Cloud fetch timeouts (short — a down tunnel must fail fast, not hang the poll).
_CLOUD_STATUS_TIMEOUT_S = 2.5
_CLOUD_AGENT_TIMEOUT_S = 3.0
_CLOUD_SNAPSHOT_TIMEOUT_S = 4.0


def _cloud_api_url() -> str | None:
    """The cloud MediaMTX control-API base (WG-side), or None when unconfigured."""
    return os.environ.get('GPS_BROADCAST_CLOUD_URL')


def _cloud_agent_url() -> str | None:
    """The cloud snapshot/log agent base (WG-side), or None when unconfigured."""
    return os.environ.get('GPS_BROADCAST_CLOUD_AGENT_URL')


def _log_lines_arg() -> int:
    """Parse + clamp the ``lines`` query for the log panel."""
    try:
        n = int(request.args.get('lines', _LOG_LINES_DEFAULT))
    except (TypeError, ValueError):
        n = _LOG_LINES_DEFAULT
    return max(1, min(n, _LOG_LINES_MAX))


def _cloud_active_paths() -> set[str]:
    """Paths the cloud agent is actively snapshotting (its own RTSP reader discount).

    The agent pulls over RTSP to make each JPEG, which the cloud hub counts as a
    reader; discounting these (per-hub, mirroring the van) keeps ``readers``/
    ``danger`` reflecting real consumers. Empty when the agent is unreachable —
    then it isn't snapshotting anything, so there's nothing to discount.
    """
    agent = _cloud_agent_url()
    if not agent:
        return set()
    try:
        resp = requests.get(f'{agent}/active', timeout=_CLOUD_AGENT_TIMEOUT_S)
        resp.raise_for_status()
        return set(resp.json().get('paths', []))
    except (requests.RequestException, ValueError):
        return set()


@broadcast_bp.get('/api/broadcast/feeds')
def broadcast_feeds() -> Response:
    """Every feed's copy-ready config, secrets interpolated server-side."""
    return jsonify(render_feeds(os.environ))


@broadcast_bp.get('/api/broadcast/status')
def broadcast_status() -> Response:
    """Two-sides live status per feed for both hubs, merged onto the registry (B6).

    The van hub reads its localhost control API; the cloud hub reads its control
    API over the WG tunnel (timeout-guarded). Each hub discounts its own
    snapshotter's RTSP pulls from the reader count. An unreachable hub reports
    ``reachable: false`` (off-grid resting state), never a failure.
    """
    van_states = fetch_paths()
    van_reachable = van_states is not None
    van_by_name = {s.name: s for s in van_states} if van_states is not None else {}
    van_snap = get_manager().active_paths()

    cloud_url = _cloud_api_url()
    cloud_states = fetch_paths(cloud_url, timeout=_CLOUD_STATUS_TIMEOUT_S) if cloud_url else None
    cloud_reachable = cloud_states is not None
    cloud_by_name = {s.name: s for s in cloud_states} if cloud_states is not None else {}
    cloud_snap = _cloud_active_paths() if cloud_reachable else set()

    feeds = []
    for feed in FEEDS:
        if feed.hub == 'van':
            if van_reachable:
                self_readers = 1 if feed.path in van_snap else 0
                status = feed_status(feed, van_by_name.get(feed.path), self_readers)
            else:
                status = {'reachable': False}
        else:  # cloud — over the WG tunnel (B7)
            if cloud_reachable:
                self_readers = 1 if feed.path in cloud_snap else 0
                status = feed_status(feed, cloud_by_name.get(feed.path), self_readers)
            else:
                status = {'reachable': False}
        feeds.append({'hub': feed.hub, 'path': feed.path, **status})

    return jsonify(
        {
            'generated_at': now_canonical(),
            'hubs': {
                'van': {'reachable': van_reachable},
                'cloud': {'reachable': cloud_reachable, 'configured': bool(cloud_url)},
            },
            'feeds': feeds,
        }
    )


@broadcast_bp.get('/api/broadcast/snapshot/<name>')
def broadcast_snapshot(name: str) -> Response:
    """A rolling downscaled JPEG for one feed's monitor-wall tile (B9).

    Van hub served locally; cloud hub (``?hub=cloud``) proxied through the cloud
    agent over the WG tunnel. ``name`` is gated to that hub's snapshottable video
    paths (never an arbitrary RTSP target). 202 while a worker warms up (no frame
    yet), 404 for an unknown/audio-only path, 502 when the cloud agent/tunnel is
    unreachable.
    """
    if request.args.get('hub') == 'cloud':
        return _proxy_cloud_snapshot(name)
    if name not in _SNAPSHOTTABLE:
        abort(404)
    jpeg = get_manager().request(name)
    if jpeg is None:
        return Response(status=202)  # warming up — the tile keeps polling
    return Response(jpeg.read_bytes(), mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})


def _proxy_cloud_snapshot(name: str) -> Response:
    """Proxy a cloud feed's JPEG from the cloud agent over the WG tunnel."""
    if name not in _CLOUD_SNAPSHOTTABLE:
        abort(404)
    agent = _cloud_agent_url()
    if not agent:
        return Response(status=502)  # cloud agent not configured
    try:
        resp = requests.get(f'{agent}/snapshot/{name}', timeout=_CLOUD_SNAPSHOT_TIMEOUT_S)
    except requests.RequestException:
        return Response(status=502)  # agent/tunnel unreachable
    if resp.status_code != 200:
        return Response(status=resp.status_code)  # 202 warming / 404 unknown, passed through
    return Response(resp.content, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})


@broadcast_bp.get('/api/broadcast/logs')
def broadcast_logs() -> Response:
    """Recent MediaMTX journal lines — the raw diagnostic escape hatch (B11).

    Van hub read locally via ``journalctl`` (no sudo needed — the service user is
    in ``adm``); cloud hub (``?hub=cloud``) proxied from the cloud agent over the
    WG tunnel. An unreachable hub reports ``reachable: false`` with no lines.
    """
    hub = request.args.get('hub', 'van')
    n = _log_lines_arg()

    if hub == 'cloud':
        return _proxy_cloud_logs(n)

    rc, out, _ = proc.run(
        ['journalctl', '-u', 'mediamtx', '-n', str(n), '--no-pager', '-o', 'cat'],
        timeout=6,
    )
    if rc != 0:
        return jsonify({'hub': 'van', 'reachable': False, 'lines': []})
    return jsonify({'hub': 'van', 'reachable': True, 'lines': out.splitlines()})


def _proxy_cloud_logs(n: int) -> Response:
    """Proxy the cloud hub's MediaMTX journal tail from the cloud agent."""
    agent = _cloud_agent_url()
    unreachable = {'hub': 'cloud', 'reachable': False, 'lines': []}
    if not agent:
        return jsonify(unreachable)
    try:
        resp = requests.get(f'{agent}/logs', params={'lines': n}, timeout=_CLOUD_AGENT_TIMEOUT_S)
        resp.raise_for_status()
        lines = resp.json().get('lines', [])
    except (requests.RequestException, ValueError):
        return jsonify(unreachable)
    return jsonify({'hub': 'cloud', 'reachable': True, 'lines': lines})
