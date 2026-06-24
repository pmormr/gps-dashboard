"""Tests for GET /api/passes: end-to-end fit + propagation through the route.

Seeds an anchor fix plus a recent az/el track sampled from a synthetic orbit
placed so the satellite is at the observer's zenith *now*. The route reconstructs
those observations, fits the orbit, and must report the in-progress pass — so the
whole capture→reconstruct→fit→predict chain is exercised against the real Flask
client and a temp DB.
"""

import math
from datetime import UTC, datetime

import api.db as db
from common.orbits import GM_M3_S2, Orbit, plane_basis, position_ecef
from common.satgeo import ecef_to_azel, ecef_to_eci, gmst_rad, observer_ecef, orbital_radius_m

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
    normal = _unit(_cross(zen, ref))  # perpendicular to zenith => zenith lies in-plane
    u, v = plane_basis(normal)
    phase0 = math.atan2(_dot(zen, v), _dot(zen, u))  # epoch = now, so theta(now) hits zenith
    return Orbit(now_unix, radius, n, phase0, u, v, normal)


def _seed_track(gnssid=0, svid=7):
    """Seed an anchor fix and a 2h az/el track ending now; return the orbit."""
    now = datetime.now(UTC)
    now_unix = now.timestamp()
    orbit = _zenith_orbit(now_unix, gnssid)
    obs_ecef = observer_ecef(_LAT, _LON, _ALT)

    conn = db.get_connection()
    conn.execute(
        'INSERT INTO gps_points (timestamp, lat, lon, altitude) VALUES (?, ?, ?, ?)',
        (db.now_canonical(), _LAT, _LON, _ALT),
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


def test_predicts_in_progress_pass(client):
    """A satellite at zenith now yields a fittable orbit and an in-progress pass."""
    _seed_track(gnssid=0, svid=7)
    resp = client.get('/api/passes', query_string={'hours': 24, 'mask': 5})
    assert resp.status_code == 200
    body = resp.get_json()

    assert body['counts']['fit'] >= 1
    assert body['observer']['lat'] == _LAT
    assert body['mask_deg'] == 5.0 and body['horizon_hours'] == 24.0
    assert body['passes'], 'expected at least the in-progress pass'

    p = body['passes'][0]
    assert p['name'] == 'G07' and p['system'] == 'GPS'
    assert p['in_progress'] is True
    assert p['rise_unix'] <= p['peak_unix'] <= p['set_unix']
    assert p['peak_el'] > 60.0  # the sat is overhead now
    assert p['max_snr'] == 44.0 and p['used'] is True


def test_404_without_any_fix(client):
    """No gps_points means no observer to anchor on."""
    resp = client.get('/api/passes')
    assert resp.status_code == 404


def test_rejects_bad_params(client):
    """Non-numeric horizon is a 400."""
    resp = client.get('/api/passes', query_string={'hours': 'soon'})
    assert resp.status_code == 400


def test_unfittable_track_is_counted_not_failed(client):
    """A single stationary observation can't fit, but the route still 200s."""
    conn = db.get_connection()
    conn.execute(
        'INSERT INTO gps_points (timestamp, lat, lon, altitude) VALUES (?, ?, ?, ?)',
        (db.now_canonical(), _LAT, _LON, _ALT),
    )
    conn.execute(
        'INSERT INTO sat_observations (timestamp, gnssid, svid, az, el, snr, used, health) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (db.now_canonical(), 0, 12, 100.0, 40.0, 30.0, 1, 1),
    )
    conn.commit()
    conn.close()
    resp = client.get('/api/passes')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['counts']['observed'] == 1 and body['counts']['fit'] == 0
    assert body['passes'] == []
