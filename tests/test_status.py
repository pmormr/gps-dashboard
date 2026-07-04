"""Tests for the /api/status Home aggregate (api/routes/status.py)."""

from __future__ import annotations

import pytest

from api.db import get_connection, now_canonical


def _insert_status_rows(ts: str) -> None:
    """Insert one current row into each domain table at timestamp ``ts``."""
    conn = get_connection()
    conn.execute(
        'INSERT INTO gps_points (timestamp, lat, lon, speed, mode) VALUES (?, ?, ?, ?, ?)',
        (ts, 39.5, -105.1, 0.0, 3),
    )
    conn.execute(
        'INSERT INTO receiver_metadata (timestamp, nsat_used, nsat_seen, hdop, pdop) '
        'VALUES (?, ?, ?, ?, ?)',
        (ts, 11, 18, 0.8, 1.5),
    )
    conn.execute(
        'INSERT INTO victron_readings '
        '(sensor_id, timestamp, battery_soc, battery_power, pv_power, dc_system_power) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, ts, 87.0, -45.0, 320.0, 65.0),
    )
    conn.execute(
        'INSERT INTO bme680_readings (sensor_id, timestamp, temp_c, humidity_pct, iaq) '
        'VALUES (?, ?, ?, ?, ?)',
        (1, ts, 22.4, 41.0, 58.0),
    )
    conn.execute(
        'INSERT INTO obd_readings (sensor_id, timestamp, rpm, coolant_c, speed_kph) '
        'VALUES (?, ?, ?, ?, ?)',
        (1, ts, 1850.0, 89.0, 72.0),
    )
    conn.execute(
        'INSERT INTO system_readings '
        '(sensor_id, timestamp, cpu_temp_c, load_1m, mem_used_pct, disk_nvme_free_gb, throttled) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (1, ts, 52.1, 0.4, 31.0, 812.0, 0),
    )
    conn.execute(
        'INSERT INTO openwrt_readings '
        '(sensor_id, timestamp, wan_up, wan_ping_ms, halow_rssi_dbm, halow_stations) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (1, ts, 1, 38.0, -62.0, 1),
    )
    conn.execute(
        'INSERT INTO nvr_readings '
        '(sensor_id, timestamp, hdd_ok, hdd_temp_c, channels_video_loss) VALUES (?, ?, ?, ?, ?)',
        (1, ts, 1, 41.0, 0),
    )
    for sensor_id, online in ((2, 1), (3, 1), (4, 0)):
        conn.execute(
            'INSERT INTO camera_readings (sensor_id, timestamp, online) VALUES (?, ?, ?)',
            (sensor_id, ts, online),
        )
    conn.commit()


@pytest.fixture(autouse=True)
def _stub_host(monkeypatch):
    """Stub the systemd/chrony probes so the test is host-independent and fast."""
    import api.routes.status as status_mod

    monkeypatch.setattr(status_mod.proc, 'service_state', lambda name: 'active')
    monkeypatch.setattr(status_mod, '_ntp_synced', lambda: True)


def test_status_empty_db(client):
    """With no readings, every domain is null but the envelope is well-formed."""
    resp = client.get('/api/status')
    assert resp.status_code == 200
    data = resp.get_json()

    for domain in ('location', 'gnss', 'house', 'cabin', 'van', 'pi', 'router', 'nvr', 'cameras'):
        assert data[domain] is None
    assert data['obd_link'] is None  # obd stream never registered
    assert 'now' in data
    assert data['ntp'] == {'synced': True}

    names = [s['name'] for s in data['services']]
    assert 'gps-logger' in names and 'chrony' in names
    assert all(isinstance(s['state'], str) for s in data['services'])


def test_status_latest_values(client):
    """Each domain block carries the latest reading and its source timestamp."""
    ts = now_canonical()
    _insert_status_rows(ts)
    data = client.get('/api/status').get_json()

    assert data['location']['lat'] == 39.5
    assert data['location']['mode'] == 3
    assert data['location']['timestamp'] == ts
    assert data['gnss']['nsat_used'] == 11
    assert data['house']['battery_soc'] == 87.0
    assert data['house']['pv_power'] == 320.0
    assert data['cabin']['temp_c'] == 22.4
    assert data['van']['rpm'] == 1850.0
    assert data['pi']['cpu_temp_c'] == 52.1
    assert data['pi']['throttled'] == 0
    assert data['router']['wan_up'] == 1
    assert data['router']['halow_rssi_dbm'] == -62.0
    assert data['nvr']['hdd_ok'] == 1
    assert data['cameras'] == {'timestamp': ts, 'online': 2, 'total': 3}
    # Columns the inserts left unset ride along as NULL rather than being dropped.
    assert data['house']['battery_voltage'] is None
    assert data['van']['fuel_level_pct'] is None
    assert data['router']['wan_rx_kbps'] is None


def test_status_obd_link(client):
    """The van OBD stream's registry status rides along as ``obd_link``."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO sensors (node, type, first_seen, status) VALUES ('van', 'obd', ?, ?)",
        (now_canonical(), 'no_adapter'),
    )
    conn.commit()
    data = client.get('/api/status').get_json()

    assert data['obd_link'] == 'no_adapter'


def test_status_cameras_uses_latest_row_per_camera(client):
    """The fleet aggregate counts each camera's newest row, not stale history."""
    conn = get_connection()
    old, new = '2026-06-28T11:00:00.000Z', '2026-06-28T12:00:00.000Z'
    # Camera 2 recovered (0 → 1); camera 3 went dark (1 → 0).
    for sensor_id, ts, online in ((2, old, 0), (2, new, 1), (3, old, 1), (3, new, 0)):
        conn.execute(
            'INSERT INTO camera_readings (sensor_id, timestamp, online) VALUES (?, ?, ?)',
            (sensor_id, ts, online),
        )
    conn.commit()
    data = client.get('/api/status').get_json()

    assert data['cameras'] == {'timestamp': new, 'online': 1, 'total': 2}


def test_status_returns_most_recent(client):
    """A domain reports its newest row, not an arbitrary one."""
    conn = get_connection()
    conn.execute(
        'INSERT INTO bme680_readings (sensor_id, timestamp, temp_c) VALUES (1, ?, 10.0)',
        ('2020-01-01T00:00:00.000Z',),
    )
    conn.execute(
        'INSERT INTO bme680_readings (sensor_id, timestamp, temp_c) VALUES (1, ?, 25.0)',
        ('2026-06-28T12:00:00.000Z',),
    )
    conn.commit()
    data = client.get('/api/status').get_json()

    assert data['cabin']['timestamp'] == '2026-06-28T12:00:00.000Z'
    assert data['cabin']['temp_c'] == 25.0
