"""Camera routes — the van's Dahua cameras for the dashboard (plans/cameras-plan.md).

Glance-first: ``GET /api/cameras`` lists the viewable fleet for the grid, and
``GET /api/cameras/<node>/snapshot`` proxies a single JPEG still from one camera
with server-side digest auth, so the shared camera password never reaches the
browser. Live video is *not* served here — that rides the MediaMTX WHEP hub
(the ``cam-<pos>`` paths in ``deploy/mediamtx.yml``); this route is only the
cheap still-image path the HaLow-friendly thumbnail grid polls.

The snapshot pulls the **sub** stream (``subtype=1``, ~D1) rather than the H.265
main, so a still is a small JPEG suited to HaLow. ``node`` is resolved through
the fixed registry (never used to build an arbitrary host), so the proxy can't
be pointed off-fleet. Error mapping mirrors the other device routes: 404 unknown
node, 502 the camera refused, 503 unreachable.
"""

import os
from dataclasses import dataclass

import requests
from flask import Blueprint, Response, abort
from requests.auth import HTTPDigestAuth

from sensors.fleet import FLEET

cameras_bp = Blueprint('cameras', __name__)

#: Per-request timeout for the upstream snapshot fetch.
SNAPSHOT_TIMEOUT_S = 5.0

#: Dahua still-image CGI. subtype=1 = the sub stream (small JPEG for HaLow).
SNAPSHOT_PATH = '/cgi-bin/snapshot.cgi?channel=1&subtype=1'


@dataclass(frozen=True)
class Camera:
    """One viewable camera: its fleet node, UI label, and MediaMTX hub path."""

    node: str
    label: str
    #: The sub/glance WHEP path in mediamtx.yml; ``f'{path}-hd'`` is the 720p expand.
    path: str


#: The four viewable cams in grid order (front, blind pair, rear). ``node`` keys
#: back to :data:`FLEET` for the host; ``path`` matches ``deploy/mediamtx.yml``.
CAMERAS: tuple[Camera, ...] = (
    Camera('van-cam-front', 'Front', 'cam-front'),
    Camera('van-cam-blind-left', 'Blind L', 'cam-blind-left'),
    Camera('van-cam-blind-right', 'Blind R', 'cam-blind-right'),
    Camera('van-cam-rear', 'Rear', 'cam-rear'),
)

_HOST_BY_NODE = {d.node: d.host for d in FLEET}
_CAMERA_BY_NODE = {c.node: c for c in CAMERAS}


@cameras_bp.get('/api/cameras')
def list_cameras() -> dict[str, list[dict[str, str]]]:
    """List the viewable cameras for the grid — node, label, and hub path."""
    return {
        'cameras': [{'node': c.node, 'label': c.label, 'path': c.path} for c in CAMERAS],
    }


@cameras_bp.get('/api/cameras/<node>/snapshot')
def snapshot(node: str) -> Response:
    """Proxy one camera's current JPEG still (server-side digest auth)."""
    camera = _CAMERA_BY_NODE.get(node)
    if camera is None:
        abort(404)
    host = _HOST_BY_NODE[camera.node]
    try:
        resp = requests.get(
            f'http://{host}{SNAPSHOT_PATH}',
            auth=HTTPDigestAuth('admin', os.environ.get('GPS_DAHUA_PASSWORD', '')),
            timeout=SNAPSHOT_TIMEOUT_S,
        )
    except requests.RequestException:
        abort(503)
    if not resp.ok:
        abort(502)
    return Response(resp.content, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})
