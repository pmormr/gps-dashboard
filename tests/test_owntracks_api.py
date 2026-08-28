"""Flask-client tests for the OwnTracks live-tier reads (``/api/phone/owntracks*``).

Seeds ``owntracks_points`` in the isolated temp DB (the ``client`` fixture)
directly — the sync tool's write side is a plain ``INSERT OR IGNORE`` exercised
by its own pure-helper tests — then covers the window/bbox/device filters, the
limit cap, and the latest-per-device read.
"""

from __future__ import annotations

from api.db import get_connection

_INSERT_SQL = (
    'INSERT INTO owntracks_points '
    '(user, device, timestamp, lat, lon, accuracy, altitude, velocity, battery, synced_at) '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
)


def _row(device: str, timestamp: str, lat: float, lon: float) -> tuple:
    return ('paul', device, timestamp, lat, lon, 5.0, 100.0, 0.0, 80.0, '2026-08-28T16:00:00.000Z')


# Three fixes for 'phone' over two hours near (40, -77); one 'tablet' fix far away.
_ROWS = [
    _row('phone', '2026-08-28T10:00:00.000Z', 40.0, -77.0),
    _row('phone', '2026-08-28T11:00:00.000Z', 40.5, -77.5),
    _row('phone', '2026-08-28T12:00:00.000Z', 41.0, -78.0),
    _row('tablet', '2026-08-28T11:30:00.000Z', 30.0, -90.0),
]


def _seed(client) -> None:
    conn = get_connection()
    conn.executemany(_INSERT_SQL, _ROWS)
    conn.commit()
    conn.close()


def test_owntracks_all_points_in_time_order(client):
    _seed(client)
    data = client.get('/api/phone/owntracks').get_json()
    assert data['count'] == 4
    assert not data['truncated']
    stamps = [p['timestamp'] for p in data['points']]
    assert stamps == sorted(stamps)


def test_owntracks_window_filter(client):
    _seed(client)
    url = '/api/phone/owntracks?start=2026-08-28T10:30:00Z&end=2026-08-28T11:45:00Z'
    data = client.get(url).get_json()
    assert {p['timestamp'] for p in data['points']} == {
        '2026-08-28T11:00:00.000Z',
        '2026-08-28T11:30:00.000Z',
    }


def test_owntracks_device_filter(client):
    _seed(client)
    data = client.get('/api/phone/owntracks?device=phone').get_json()
    assert data['count'] == 3
    assert {p['device'] for p in data['points']} == {'phone'}


def test_owntracks_bbox_filter(client):
    _seed(client)
    data = client.get('/api/phone/owntracks?bbox=-91,29,-89,31').get_json()
    assert data['count'] == 1
    assert data['points'][0]['device'] == 'tablet'


def test_owntracks_limit_keeps_oldest_and_reports_truncation(client):
    _seed(client)
    data = client.get('/api/phone/owntracks?limit=2').get_json()
    assert data['count'] == 2
    assert data['truncated']
    assert data['points'][0]['timestamp'] == '2026-08-28T10:00:00.000Z'


def test_owntracks_invalid_start_rejected(client):
    assert client.get('/api/phone/owntracks?start=nope').status_code == 400


def test_owntracks_latest_per_device(client):
    _seed(client)
    data = client.get('/api/phone/owntracks/latest').get_json()
    assert data['count'] == 2
    by_device = {d['device']: d for d in data['devices']}
    assert by_device['phone']['timestamp'] == '2026-08-28T12:00:00.000Z'
    assert by_device['phone']['lat'] == 41.0
    assert by_device['tablet']['timestamp'] == '2026-08-28T11:30:00.000Z'


def test_owntracks_latest_empty(client):
    data = client.get('/api/phone/owntracks/latest').get_json()
    assert data == {'devices': [], 'count': 0}
