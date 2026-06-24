"""3D constellation globe: the page plus the reconstructed-position API.

``/globe`` renders the satellites we've logged in true 3D scale around the Earth
(three.js, PC browsers). ``/api/constellation`` reconstructs each
``sat_observations`` row in a time window to an ECEF position via
:mod:`common.satgeo`, anchored to a representative observer fix, and groups them
by satellite so the client draws one arc per SV. See
.claude/modules/observatory.md.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

from api.db import canonical_timestamp, get_connection, now_canonical
from api.observatory import anchor_observer, reconstruct_tracks
from api.params import parse_time
from common.satgeo import EARTH_RADIUS_M, fit_orbit_normal, observer_ecef

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
    obs = anchor_observer(conn, end)
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
    tracks = reconstruct_tracks(rows, lat, lon, origin)

    sat_list = []
    for (gid, svid), samples in tracks.items():
        pts_km = [
            (s.ecef_m[0] / 1000.0, s.ecef_m[1] / 1000.0, s.ecef_m[2] / 1000.0) for s in samples
        ]
        normal = fit_orbit_normal(pts_km)
        orbit = None
        if normal is not None:
            orbit = {
                'nx': normal[0],
                'ny': normal[1],
                'nz': normal[2],
                'radius_km': math.sqrt(sum(c * c for c in pts_km[0])),
            }
        samples_json = [
            {'t': s.t, 'x': p[0], 'y': p[1], 'z': p[2], 'snr': s.snr, 'used': s.used}
            for s, p in zip(samples, pts_km, strict=True)
        ]
        sat_list.append({'gnssid': gid, 'svid': svid, 'samples': samples_json, 'orbit': orbit})

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
            'sats': sat_list,
        }
    )
