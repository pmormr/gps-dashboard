"""3D constellation globe: the page plus the reconstructed-position API.

``/globe`` renders the satellites we've logged in true 3D scale around the Earth
(three.js, PC browsers). ``/api/constellation`` reconstructs each
``sat_observations`` row in a time window to an ECEF position via
:mod:`common.satgeo`, anchored to a representative observer fix, and groups them
by satellite so the client draws one arc per SV. See
plans/gnss-observatory-plan.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

from api.db import canonical_timestamp, get_connection, now_canonical
from api.params import parse_time
from common.satgeo import (
    EARTH_RADIUS_M,
    look_direction_ecef,
    observer_ecef,
    orbital_radius_m,
    ray_sphere_far,
)

globe_bp = Blueprint('globe', __name__)

# Default trailing window when the client omits start/end.
_DEFAULT_WINDOW_HOURS = 24


@globe_bp.get('/globe')
def globe_page():
    """The 3D constellation globe page (three.js; PC browsers)."""
    return render_template('globe.html')


@globe_bp.get('/api/constellation')
def constellation():
    """Reconstructed 3D satellite positions over a time window, grouped by SV.

    Anchors to a single representative observer fix (the latest at/just before
    the window end) — over a parked session the van barely moves relative to the
    ~26,000 km orbital baseline, so one origin is ample. Each logged az/el is
    inverted to an ECEF position against its constellation's orbital sphere;
    unmodelled constellations (e.g. IMES) are skipped. Positions are returned in
    kilometres, grouped by ``(gnssid, svid)`` so the client renders one arc per
    satellite. Window defaults to the trailing 24h.
    """
    end = request.args.get('end')
    if end:
        end, err = parse_time(end, 'end')
        if err:
            return err
    else:
        end = now_canonical()
    start = request.args.get('start')
    if start:
        start, err = parse_time(start, 'start')
        if err:
            return err
    else:
        start = canonical_timestamp(
            (datetime.now(UTC) - timedelta(hours=_DEFAULT_WINDOW_HOURS)).isoformat()
        )

    conn = get_connection()
    obs = conn.execute(
        'SELECT lat, lon, altitude, timestamp FROM gps_points '
        'WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT 1',
        [end],
    ).fetchone()
    if obs is None:
        obs = conn.execute(
            'SELECT lat, lon, altitude, timestamp FROM gps_points ORDER BY timestamp DESC LIMIT 1'
        ).fetchone()
    if obs is None:
        return jsonify({'error': 'No GPS fix available to anchor the observer'}), 404

    lat = obs['lat']
    lon = obs['lon']
    alt = obs['altitude'] or 0.0
    origin = observer_ecef(lat, lon, alt)

    rows = conn.execute(
        'SELECT timestamp, gnssid, svid, az, el, snr, used FROM sat_observations '
        'WHERE timestamp BETWEEN ? AND ? AND az IS NOT NULL AND el IS NOT NULL '
        'ORDER BY gnssid, svid, timestamp',
        [start, end],
    ).fetchall()

    radii: dict[int, float | None] = {}
    sats: dict[tuple[int, int], list[dict]] = {}
    for r in rows:
        gid = r['gnssid']
        if gid not in radii:
            radii[gid] = orbital_radius_m(gid)
        radius = radii[gid]
        if radius is None:
            continue
        d = look_direction_ecef(lat, lon, r['az'], r['el'])
        pos = ray_sphere_far(origin, d, radius)
        if pos is None:
            continue
        sats.setdefault((gid, r['svid']), []).append(
            {
                't': r['timestamp'],
                'x': pos[0] / 1000.0,
                'y': pos[1] / 1000.0,
                'z': pos[2] / 1000.0,
                'snr': r['snr'],
                'used': bool(r['used']),
            }
        )

    return jsonify(
        {
            'observer': {
                'lat': lat,
                'lon': lon,
                'alt': alt,
                'x': origin[0] / 1000.0,
                'y': origin[1] / 1000.0,
                'z': origin[2] / 1000.0,
                'timestamp': obs['timestamp'],
            },
            'earth_radius_km': EARTH_RADIUS_M / 1000.0,
            'window': {'start': start, 'end': end},
            'sats': [
                {'gnssid': gid, 'svid': svid, 'samples': samples}
                for (gid, svid), samples in sats.items()
            ],
        }
    )
