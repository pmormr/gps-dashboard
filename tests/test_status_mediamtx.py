"""Tests for the MediaMTX path normalizer behind /api/mediamtx."""

from __future__ import annotations

from api.routes.status_mediamtx import _normalize_paths

# Two representative control-API items: an idle on-demand camera (ready false, no
# tracks/readers) and the live radio path (ready, an Opus track, a viewer).
_ITEMS = [
    {
        'name': 'cam-front',
        'ready': False,
        'source': {'type': 'rtspSource', 'id': ''},
        'tracks': [],
        'readers': [],
        'bytesReceived': 0,
        'bytesSent': 0,
    },
    {
        'name': 'radio',
        'ready': True,
        'source': {'type': 'rtspSession', 'id': 'abc'},
        'tracks': ['Opus'],
        'readers': [{'type': 'webRTCSession', 'id': 'x'}],
        'bytesReceived': 4096,
        'bytesSent': 2048,
    },
]


def test_normalize_paths_maps_ready_tracks_and_reader_count():
    idle, live = _normalize_paths(_ITEMS)
    assert idle == {
        'name': 'cam-front',
        'ready': False,
        'source': 'rtspSource',
        'tracks': [],
        'readers': 0,
        'bytes_received': 0,
        'bytes_sent': 0,
    }
    assert live['ready'] is True
    assert live['source'] == 'rtspSession'
    assert live['tracks'] == ['Opus']
    assert live['readers'] == 1
    assert live['bytes_received'] == 4096


def test_normalize_paths_tolerates_missing_source_and_fields():
    (path,) = _normalize_paths([{'name': 'radio'}])
    assert path['source'] is None
    assert path['ready'] is False
    assert path['tracks'] == []
    assert path['readers'] == 0


def test_mediamtx_endpoint_shape(client):
    """The route is wired and returns the check-page shape even with no hub.

    The control API is unreachable under test, so api_ok is false and paths is
    empty, but the document shape must be stable for the frontend.
    """
    resp = client.get('/api/mediamtx')
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body) >= {'overall_ok', 'checks', 'service_state', 'listening', 'summary', 'paths'}
    assert len(body['checks']) == 4
    assert set(body['summary']) == {'total', 'ready', 'readers'}
