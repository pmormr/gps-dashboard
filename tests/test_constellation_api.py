"""Tests for GET /api/constellation: reconstruction, grouping, window, anchor.

Seeds a gps_points anchor fix plus sat_observations rows, then drives the real
route through the Flask test client to pin the contract: rows reconstruct onto
their constellation's orbital sphere, group by SV, honour the time window, skip
unmodelled constellations, and 404 without any fix to anchor the observer.
"""

import math

import api.db as db
from common.satgeo import orbital_radius_m

WINDOW = {'start': '2026-06-20T00:00:00Z', 'end': '2026-06-20T01:00:00Z'}


def _seed_fix(lat=39.7, lon=-105.0, alt=1600.0, ts='2026-06-20T00:30:00.000Z'):
    """Insert one gps_points anchor fix."""
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO gps_points (timestamp, lat, lon, altitude) VALUES (?, ?, ?, ?)',
        (ts, lat, lon, alt),
    )
    conn.commit()
    conn.close()


def _seed_obs(ts, gnssid, svid, az, el, snr=30.0, used=1):
    """Insert one sat_observations row."""
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO sat_observations (timestamp, gnssid, svid, az, el, snr, used, health) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (ts, gnssid, svid, az, el, snr, used, 1),
    )
    conn.commit()
    conn.close()


def test_404_without_any_fix(client):
    """No gps_points means no observer to anchor the reconstruction."""
    _seed_obs('2026-06-20T00:30:00.000Z', 0, 1, 100.0, 40.0)
    resp = client.get('/api/constellation', query_string=WINDOW)
    assert resp.status_code == 404


def test_reconstructs_and_groups_by_sv(client):
    """Samples group per (gnssid, svid) and land on the orbital sphere."""
    _seed_fix()
    _seed_obs('2026-06-20T00:10:00.000Z', 0, 1, 100.0, 40.0)
    _seed_obs('2026-06-20T00:20:00.000Z', 0, 1, 110.0, 45.0)
    _seed_obs('2026-06-20T00:15:00.000Z', 2, 5, 200.0, 30.0)

    resp = client.get('/api/constellation', query_string=WINDOW)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['observer']['lat'] == 39.7
    sats = {(s['gnssid'], s['svid']): s for s in body['sats']}
    assert set(sats) == {(0, 1), (2, 5)}
    assert len(sats[(0, 1)]['samples']) == 2
    assert all('orbit' in s for s in body['sats'])  # null until enough arc is traced

    s0 = sats[(0, 1)]['samples'][0]
    r_km = math.sqrt(s0['x'] ** 2 + s0['y'] ** 2 + s0['z'] ** 2)
    assert math.isclose(r_km, orbital_radius_m(0) / 1000.0, rel_tol=1e-6)
    assert s0['used'] is True


def test_skips_unmodelled_constellation(client):
    """IMES (gnssid 4) has no orbital radius, so it is dropped."""
    _seed_fix()
    _seed_obs('2026-06-20T00:10:00.000Z', 4, 1, 100.0, 40.0)
    resp = client.get('/api/constellation', query_string=WINDOW)
    assert resp.status_code == 200
    assert resp.get_json()['sats'] == []


def test_window_excludes_out_of_range_samples(client):
    """Observations outside [start, end] are not returned."""
    _seed_fix()
    _seed_obs('2026-06-19T23:00:00.000Z', 0, 1, 100.0, 40.0)
    resp = client.get('/api/constellation', query_string=WINDOW)
    assert resp.status_code == 200
    assert resp.get_json()['sats'] == []
