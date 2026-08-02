"""Tests for the camera routes (``api/routes/cameras.py``).

Exercised through the real Flask app with ``requests.get`` replaced by a fake —
no cameras on the LAN. Covers the registry list, the snapshot proxy's downscale
success, and its 404/502/503 error mapping (including the NVR, present in the
fleet but not a viewable camera, and a 200 that isn't a decodable image).
"""

from __future__ import annotations

import io

import requests
from PIL import Image

import api.routes.cameras as cameras


def _jpeg(width: int, height: int) -> bytes:
    """A real JPEG of the given size (the upstream main-res still stand-in)."""
    buf = io.BytesIO()
    Image.new('RGB', (width, height), (10, 120, 200)).save(buf, format='JPEG')
    return buf.getvalue()


class FakeResponse:
    """Minimal stand-in for a ``requests`` response (only ``.ok``/``.content``)."""

    def __init__(self, *, ok: bool = True, content: bytes = b'') -> None:
        self.ok = ok
        self.content = content


def test_list_cameras(client):
    resp = client.get('/api/cameras')
    assert resp.status_code == 200
    data = resp.get_json()['cameras']
    assert [c['node'] for c in data] == [c.node for c in cameras.CAMERAS]
    assert data[0] == {
        'node': 'van-cam-front',
        'label': 'Front',
        'path': 'cam-front',
        'driving': False,
    }
    # The blind-spot + rear feeds carry the driving-wall flag; the front does not.
    driving = {c['node'] for c in data if c['driving']}
    assert driving == {'van-cam-blind-left', 'van-cam-blind-right', 'van-cam-rear'}


def test_snapshot_downscales_main_still(client, monkeypatch):
    captured: dict[str, str] = {}

    def fake_get(url, **kwargs):
        captured['url'] = url
        return FakeResponse(content=_jpeg(2688, 1520))

    monkeypatch.setattr(cameras.requests, 'get', fake_get)
    resp = client.get('/api/cameras/van-cam-front/snapshot')
    assert resp.status_code == 200
    assert resp.mimetype == 'image/jpeg'
    # main still (subtype is ignored on this firmware), resolved to the fleet host
    assert captured['url'] == 'http://192.168.42.51/cgi-bin/snapshot.cgi?channel=1'
    # returned a smaller, still-valid JPEG (the thumbnail fits the box)
    thumb = Image.open(io.BytesIO(resp.data))
    assert max(thumb.size) <= max(cameras.THUMB_BOX)
    assert thumb.size < (2688, 1520)


def test_snapshot_unknown_node_404(client):
    assert client.get('/api/cameras/van-cam-nope/snapshot').status_code == 404


def test_snapshot_nvr_is_not_a_camera_404(client):
    # van-nvr is in FLEET but not a viewable camera — the registry gates it.
    assert client.get('/api/cameras/van-nvr/snapshot').status_code == 404


def test_snapshot_camera_refused_502(client, monkeypatch):
    monkeypatch.setattr(cameras.requests, 'get', lambda *a, **k: FakeResponse(ok=False))
    assert client.get('/api/cameras/van-cam-rear/snapshot').status_code == 502


def test_snapshot_non_image_response_502(client, monkeypatch):
    monkeypatch.setattr(
        cameras.requests, 'get', lambda *a, **k: FakeResponse(content=b'not a jpeg')
    )
    assert client.get('/api/cameras/van-cam-rear/snapshot').status_code == 502


def test_snapshot_unreachable_503(client, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError('no route to host')

    monkeypatch.setattr(cameras.requests, 'get', boom)
    assert client.get('/api/cameras/van-cam-rear/snapshot').status_code == 503
