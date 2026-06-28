"""Tests for the /api/gpsd/status endpoint (api/routes/status_gpsd.py)."""

from __future__ import annotations

import os

from api.db import get_connection, now_canonical


def test_gpsd_status_healthy(client, monkeypatch):
    """A live receiver with a fresh fix: all checks pass."""
    import api.routes.status_gpsd as g

    monkeypatch.setattr(
        g,
        'query_gpsd',
        lambda: {
            'connected': True,
            'tpv': {'mode': 3, 'speed': 0.0, 'track': 90},
            'sky': {'satellites': [{'used': True}] * 4 + [{'used': False}]},
        },
    )
    monkeypatch.setattr(g, 'configured_gpsd_device', lambda: '/dev/ttyAMA0')
    monkeypatch.setattr(g.proc, 'service_state', lambda name: 'active')
    monkeypatch.setattr(os.path, 'exists', lambda p: True)

    conn = get_connection()
    conn.execute(
        'INSERT INTO gps_points (timestamp, lat, lon, speed, altitude) VALUES (?, ?, ?, ?, ?)',
        (now_canonical(), 39.5, -105.1, 0.0, 1600.0),
    )
    conn.commit()

    data = client.get('/api/gpsd/status').get_json()
    assert data['overall_ok'] is True
    assert data['service_state'] == 'active'
    assert data['device_present'] is True
    assert data['fix_mode'] == 3
    assert data['sats_used'] == 4 and data['sats_visible'] == 5
    assert data['frozen'] is False
    assert data['latest']['lat'] == 39.5
    assert all(c['ok'] for c in data['checks'])


def test_gpsd_status_unreachable(client, monkeypatch):
    """No gpsd / no device / no data: well-formed failing document."""
    import api.routes.status_gpsd as g

    monkeypatch.setattr(g, 'query_gpsd', lambda: {'connected': False, 'tpv': {}, 'sky': {}})
    monkeypatch.setattr(g, 'configured_gpsd_device', lambda: None)
    monkeypatch.setattr(g.proc, 'service_state', lambda name: 'inactive')

    data = client.get('/api/gpsd/status').get_json()
    assert data['overall_ok'] is False
    assert data['latest'] is None
    assert data['fix_mode'] == 0
    assert {'name', 'ok'} <= data['checks'][0].keys()
