"""Tests for the /api/mediamtx status route (Diagnostics → Media drill-in).

The path-normalize logic now lives in ``common.mediamtx`` (see
``test_mediamtx_common.py``); this covers the route's document shape.
"""

from __future__ import annotations


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
