"""Tests for GET /api/constellation: reconstruction, grouping, window, anchor.

Seeds a gps_points anchor fix plus sat_observations rows, then drives the real
route through the Flask test client to pin the contract: rows reconstruct onto
their constellation's orbital sphere, group by SV, honour the time window, skip
unmodelled constellations, and 404 without any fix to anchor the observer.
"""

import math
from datetime import UTC, datetime, timedelta

import api.db as db
from api.observatory import unix_seconds
from common.orbits import GM_M3_S2, Orbit, plane_basis, position_ecef
from common.satgeo import (
    ecef_to_azel,
    ecef_to_eci,
    gmst_rad,
    observer_ecef,
    orbital_radius_m,
)

WINDOW = {'start': '2026-06-20T00:00:00Z', 'end': '2026-06-20T01:00:00Z'}

_LAT, _LON, _ALT = 39.32, -77.84, 200.0


def _unit(v):
    m = math.sqrt(sum(c * c for c in v))
    return tuple(c / m for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b, strict=True))


def _zenith_orbit(now_unix, gnssid=0):
    """A circular orbit whose satellite sits at the observer's zenith at now."""
    radius = orbital_radius_m(gnssid)
    n = math.sqrt(GM_M3_S2 / radius**3)
    zen = _unit(ecef_to_eci(observer_ecef(_LAT, _LON, _ALT), gmst_rad(now_unix)))
    ref = (0.0, 0.0, 1.0) if abs(zen[2]) < 0.9 else (1.0, 0.0, 0.0)
    normal = _unit(_cross(zen, ref))
    u, v = plane_basis(normal)
    phase0 = math.atan2(_dot(zen, v), _dot(zen, u))
    return Orbit(now_unix, radius, n, phase0, u, v, normal)


def _seed_fittable_track(now, gnssid=0, svid=7):
    """Seed an anchor at ``now`` plus a 2h zenith az/el track; return the truth orbit."""
    now_unix = now.timestamp()
    orbit = _zenith_orbit(now_unix, gnssid)
    obs_ecef = observer_ecef(_LAT, _LON, _ALT)
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO gps_points (timestamp, lat, lon, altitude) VALUES (?, ?, ?, ?)',
        (db.canonical_timestamp(now.isoformat()), _LAT, _LON, _ALT),
    )
    for k in range(120):
        t_unix = now_unix - 7200.0 + k * 60.0
        az, el, _ = ecef_to_azel(_LAT, _LON, obs_ecef, position_ecef(orbit, t_unix))
        if el < 0.0:
            continue
        ts = db.canonical_timestamp(datetime.fromtimestamp(t_unix, UTC).isoformat())
        conn.execute(
            'INSERT INTO sat_observations (timestamp, gnssid, svid, az, el, snr, used, health) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (ts, gnssid, svid, az, el, 44.0, 1, 1),
        )
    conn.commit()
    conn.close()
    return orbit


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


def test_fittable_sv_carries_predicted_position_and_trail(client):
    """A fittable track yields a propagated current position plus a trailing path."""
    now = datetime.now(UTC)
    truth = _seed_fittable_track(now, gnssid=0, svid=7)
    end = db.canonical_timestamp(now.isoformat())
    start = db.canonical_timestamp((now - timedelta(hours=24)).isoformat())

    resp = client.get('/api/constellation', query_string={'start': start, 'end': end})
    assert resp.status_code == 200
    sat = next(s for s in resp.get_json()['sats'] if (s['gnssid'], s['svid']) == (0, 7))

    assert sat['predicted'] is not None
    assert len(sat['trail']) >= 2

    # Propagated dot matches the truth orbit at the window end (km), within the
    # reconstruction round-trip error.
    expected = position_ecef(truth, unix_seconds(end))
    for axis, e in zip(('x', 'y', 'z'), expected, strict=True):
        assert math.isclose(sat['predicted'][axis], e / 1000.0, abs_tol=5.0)

    # Trail points lie on the constellation's orbital sphere.
    r0 = math.sqrt(sum(sat['trail'][0][a] ** 2 for a in ('x', 'y', 'z')))
    assert math.isclose(r0, orbital_radius_m(0) / 1000.0, rel_tol=1e-3)


def test_unfittable_sv_has_null_prediction(client):
    """Too short a track to fit an orbit leaves predicted/trail empty, not failed."""
    _seed_fix()
    _seed_obs('2026-06-20T00:10:00.000Z', 0, 1, 100.0, 40.0)
    _seed_obs('2026-06-20T00:20:00.000Z', 0, 1, 110.0, 45.0)
    resp = client.get('/api/constellation', query_string=WINDOW)
    assert resp.status_code == 200
    sat = resp.get_json()['sats'][0]
    assert sat['predicted'] is None
    assert sat['trail'] == []
