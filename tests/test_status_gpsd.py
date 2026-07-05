"""Tests for the /api/gpsd/* endpoints (api/routes/status_gpsd.py)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

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


def test_gpsd_live_with_fix(client, monkeypatch):
    """A 3D fix: the TPV essentials pass through, fix age computed server-side."""
    import api.routes.status_gpsd as g

    fix_time = (datetime.now(UTC) - timedelta(seconds=1)).isoformat().replace('+00:00', 'Z')
    monkeypatch.setattr(
        g,
        'query_gpsd',
        lambda **kw: {
            'connected': True,
            'tpv': {
                'mode': 3,
                'lat': 39.5,
                'lon': -105.1,
                'speed': 22.4,
                'track': 271.5,
                'altMSL': 1601.2,
                'alt': 1580.0,
                'climb': -0.3,
                'time': fix_time,
            },
            'sky': {},
        },
    )

    data = client.get('/api/gpsd/live').get_json()
    assert data['connected'] is True
    assert data['mode'] == 3 and data['fix_label'] == '3D Fix'
    assert data['lat'] == 39.5 and data['lon'] == -105.1
    assert data['speed'] == 22.4 and data['track'] == 271.5
    assert data['alt'] == 1601.2  # altMSL preferred over ellipsoidal alt
    assert data['climb'] == -0.3
    assert data['time'] == fix_time
    assert 0 <= data['fix_age_s'] < 10


def test_gpsd_live_no_fix(client, monkeypatch):
    """No fix yet: position fields null, mode 1, no fix age."""
    import api.routes.status_gpsd as g

    monkeypatch.setattr(
        g,
        'query_gpsd',
        lambda **kw: {'connected': True, 'tpv': {'mode': 1}, 'sky': {}},
    )

    data = client.get('/api/gpsd/live').get_json()
    assert data['connected'] is True
    assert data['mode'] == 1 and data['fix_label'] == 'No Fix'
    assert data['lat'] is None and data['lon'] is None
    assert data['fix_age_s'] is None


def test_gpsd_live_unreachable(client, monkeypatch):
    """gpsd down: well-formed document with connected=False."""
    import api.routes.status_gpsd as g

    monkeypatch.setattr(g, 'query_gpsd', lambda **kw: {'connected': False, 'tpv': {}, 'sky': {}})

    data = client.get('/api/gpsd/live').get_json()
    assert data['connected'] is False
    assert data['mode'] == 0 and data['lat'] is None
