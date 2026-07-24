"""MediaMTX media-hub status for the Van OS Systems view.

The Pi runs MediaMTX as the van's media hub — it fans out the live radio audio,
the hill-climb SRT camera ingests, and the four Dahua cams (pulled on-demand) over
RTSP/WebRTC. This route surfaces the hub's health the way the NTP/syslog pages do:
service liveness, the RTSP/WebRTC listeners, and — from the hub's localhost control
API (``127.0.0.1:9997``) — the per-path stream state (ready, tracks, viewers).

The camera paths are ``sourceOnDemand``: idle (``ready: false``, no source pulled)
until a viewer attaches, which is the normal resting state — so an idle path is
never a failure, only information. Only a stopped service, an unreachable control
API, or a missing listener fails.
"""

from __future__ import annotations

import re

from flask import Blueprint, Response, jsonify

from common import proc
from common.mediamtx import PathState, fetch_paths

status_mediamtx_bp = Blueprint('status_mediamtx', __name__)


def _listening() -> tuple[bool, bool]:
    """Whether the hub is serving RTSP (:8554) and WebRTC (:8889).

    Returns:
        ``(rtsp, webrtc)`` booleans from the ``ss`` TCP listening table (no
        privilege needed; the same approach the NTP/syslog routes use).
    """
    _, tcp, _ = proc.run(['ss', '-lnt'])
    return bool(re.search(r':8554\s', tcp)), bool(re.search(r':8889\s', tcp))


def _path_dict(state: PathState) -> dict:
    """Map a shared :class:`PathState` to the /api/mediamtx per-path shape."""
    return {
        'name': state.name,
        'ready': state.ready,
        'source': state.source_type,
        'tracks': list(state.tracks),
        'readers': state.readers,
        'bytes_received': state.bytes_received,
        'bytes_sent': state.bytes_sent,
    }


def _collect() -> dict:
    """Gather the hub's service/listener/path state and the PASS/FAIL checks.

    Returns:
        The document served by ``/api/mediamtx`` and rendered by the Systems view.
    """
    service_state = proc.service_state('mediamtx')
    rtsp, webrtc = _listening()
    states = fetch_paths()
    api_ok = states is not None
    paths = [_path_dict(s) for s in states] if states else []

    checks = [
        {'name': 'mediamtx service', 'ok': service_state == 'active'},
        {'name': 'control API reachable', 'ok': api_ok},
        {'name': 'RTSP listening (:8554)', 'ok': rtsp},
        {'name': 'WebRTC listening (:8889)', 'ok': webrtc},
    ]

    ready = sum(1 for p in paths if p['ready']) if paths else 0
    readers = sum(p['readers'] for p in paths) if paths else 0

    return {
        'overall_ok': all(c['ok'] for c in checks),
        'checks': checks,
        'service_state': service_state,
        'api_ok': api_ok,
        'listening': {'rtsp': rtsp, 'webrtc': webrtc},
        'summary': {'total': len(paths) if paths else 0, 'ready': ready, 'readers': readers},
        'paths': paths or [],
    }


@status_mediamtx_bp.get('/api/mediamtx')
def mediamtx_api() -> Response:
    """MediaMTX media-hub health + per-path stream state as JSON."""
    return jsonify(_collect())
