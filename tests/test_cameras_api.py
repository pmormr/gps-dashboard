"""Tests for the camera routes (``api/routes/cameras.py``).

Exercised through the real Flask app with ``requests.get`` replaced by a fake —
no cameras on the LAN. Covers the registry list and the snapshot proxy's success
plus its 404/502/503 error mapping (including that the NVR, present in the fleet
but not a viewable camera, is rejected).
"""

from __future__ import annotations

import requests

import api.routes.cameras as cameras


class FakeResponse:
    """Minimal stand-in for a ``requests`` response (only ``.ok``/``.content``)."""

    def __init__(self, *, ok: bool = True, content: bytes = b'\xff\xd8\xff') -> None:
        self.ok = ok
        self.content = content


def test_list_cameras(client):
    resp = client.get('/api/cameras')
    assert resp.status_code == 200
    data = resp.get_json()['cameras']
    assert [c['node'] for c in data] == [c.node for c in cameras.CAMERAS]
    assert data[0] == {'node': 'van-cam-front', 'label': 'Front', 'path': 'cam-front'}


def test_snapshot_ok_pulls_sub_stream_from_fleet_host(client, monkeypatch):
    captured: dict[str, str] = {}

    def fake_get(url, **kwargs):
        captured['url'] = url
        return FakeResponse(content=b'JPEGBYTES')

    monkeypatch.setattr(cameras.requests, 'get', fake_get)
    resp = client.get('/api/cameras/van-cam-front/snapshot')
    assert resp.status_code == 200
    assert resp.mimetype == 'image/jpeg'
    assert resp.data == b'JPEGBYTES'
    # node resolved to its fleet host; the small sub-stream still is requested
    assert captured['url'] == 'http://192.168.42.51/cgi-bin/snapshot.cgi?channel=1&subtype=1'


def test_snapshot_unknown_node_404(client):
    assert client.get('/api/cameras/van-cam-nope/snapshot').status_code == 404


def test_snapshot_nvr_is_not_a_camera_404(client):
    # van-nvr is in FLEET but not a viewable camera — the registry gates it.
    assert client.get('/api/cameras/van-nvr/snapshot').status_code == 404


def test_snapshot_camera_refused_502(client, monkeypatch):
    monkeypatch.setattr(cameras.requests, 'get', lambda *a, **k: FakeResponse(ok=False))
    assert client.get('/api/cameras/van-cam-rear/snapshot').status_code == 502


def test_snapshot_unreachable_503(client, monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError('no route to host')

    monkeypatch.setattr(cameras.requests, 'get', boom)
    assert client.get('/api/cameras/van-cam-rear/snapshot').status_code == 503
