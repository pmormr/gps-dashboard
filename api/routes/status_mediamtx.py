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

import requests
from flask import Blueprint, Response, jsonify

from common import proc

status_mediamtx_bp = Blueprint('status_mediamtx', __name__)

#: The hub's localhost control API (enabled in deploy/mediamtx.yml).
_API = 'http://127.0.0.1:9997/v3/paths/list'


def _listening() -> tuple[bool, bool]:
    """Whether the hub is serving RTSP (:8554) and WebRTC (:8889).

    Returns:
        ``(rtsp, webrtc)`` booleans from the ``ss`` TCP listening table (no
        privilege needed; the same approach the NTP/syslog routes use).
    """
    _, tcp, _ = proc.run(['ss', '-lnt'])
    return bool(re.search(r':8554\s', tcp)), bool(re.search(r':8889\s', tcp))


def _normalize_paths(items: list[dict]) -> list[dict]:
    """Reduce the control API's path items to the fields the status page shows.

    Args:
        items: The ``items`` array from ``/v3/paths/list``.

    Returns:
        One dict per path: ``name``, ``ready``, ``source`` (type string or None),
        ``tracks``, ``readers`` (viewer count), and inbound/outbound bytes.
    """
    out = []
    for it in items:
        src = it.get('source') or {}
        out.append(
            {
                'name': it.get('name'),
                'ready': bool(it.get('ready')),
                'source': src.get('type'),
                'tracks': it.get('tracks') or [],
                'readers': len(it.get('readers') or []),
                'bytes_received': it.get('bytesReceived') or 0,
                'bytes_sent': it.get('bytesSent') or 0,
            }
        )
    return out


def _paths() -> list[dict] | None:
    """Fetch + normalize the hub's path list, or None when the API is unreachable.

    Returns:
        The :func:`_normalize_paths` list, or None when the control API can't be
        reached or returns unparseable data (hub down, API disabled).
    """
    try:
        resp = requests.get(_API, timeout=4)
        resp.raise_for_status()
        items = resp.json().get('items', [])
    except (requests.RequestException, ValueError):
        return None
    return _normalize_paths(items)


def _collect() -> dict:
    """Gather the hub's service/listener/path state and the PASS/FAIL checks.

    Returns:
        The document served by ``/api/mediamtx`` and rendered by the Systems view.
    """
    service_state = proc.service_state('mediamtx')
    rtsp, webrtc = _listening()
    paths = _paths()
    api_ok = paths is not None

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
