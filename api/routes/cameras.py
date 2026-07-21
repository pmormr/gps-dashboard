"""Camera routes — the van's Dahua cameras for the dashboard (plans/cameras-plan.md).

Glance-first: ``GET /api/cameras`` lists the viewable fleet for the grid, and
``GET /api/cameras/<node>/snapshot`` proxies a single JPEG still from one camera
with server-side digest auth, so the shared camera password never reaches the
browser. Live video is *not* served here — that rides the MediaMTX WHEP hub
(the ``cam-<pos>`` paths in ``deploy/mediamtx.yml``); this route is only the
cheap still-image path the HaLow-friendly thumbnail grid polls.

This firmware's ``snapshot.cgi`` **ignores** ``subtype`` — it always returns the
~600 kB main-res still (verified on the wire) — so the proxy **downscales** it to
a small thumbnail before answering. The big fetch stays on the van LAN; only the
small thumbnail crosses HaLow to a browser at home, which is the whole point of
the thumbnail grid. ``node`` is resolved through the fixed registry (never used
to build an arbitrary host), so the proxy can't be pointed off-fleet. Error
mapping mirrors the other device routes: 404 unknown node, 502 the camera refused
or returned a non-image, 503 unreachable.
"""

import io
import os
from dataclasses import dataclass

import requests
from flask import Blueprint, Response, abort
from PIL import Image, UnidentifiedImageError
from requests.auth import HTTPDigestAuth

from sensors.fleet import FLEET

cameras_bp = Blueprint('cameras', __name__)

#: Per-request timeout for the upstream snapshot fetch.
SNAPSHOT_TIMEOUT_S = 5.0

#: Dahua still-image CGI (main res — subtype is ignored on this firmware).
SNAPSHOT_PATH = '/cgi-bin/snapshot.cgi?channel=1'

#: Thumbnail fit box (longest side, px) and JPEG quality — tuned for HaLow: a
#: 2688×1520 still shrinks to ~480×271, ~600 kB → ~20 kB.
THUMB_BOX = (480, 480)
THUMB_QUALITY = 70


def _downscale(jpeg: bytes) -> bytes:
    """Shrink a main-res JPEG to a HaLow-sized thumbnail (aspect preserved)."""
    img = Image.open(io.BytesIO(jpeg))
    img.thumbnail(THUMB_BOX)  # in-place, only ever shrinks
    out = io.BytesIO()
    img.convert('RGB').save(out, format='JPEG', quality=THUMB_QUALITY)
    return out.getvalue()


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
    """Proxy one camera's still, downscaled to a thumbnail (server-side digest auth)."""
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
    try:
        thumb = _downscale(resp.content)
    except (UnidentifiedImageError, OSError):
        abort(502)  # camera answered 200 with something that isn't a decodable image
    return Response(thumb, mimetype='image/jpeg', headers={'Cache-Control': 'no-store'})
