"""3D constellation globe: the page plus the reconstructed-position API.

``/globe`` renders the satellites we've logged in true 3D scale around the Earth
(three.js, PC browsers). ``/api/constellation`` reconstructs each
``sat_observations`` row in a time window to an ECEF position via
:mod:`common.satgeo`, anchored to a representative observer fix, fits each
satellite's inertial orbit, and groups both by satellite — the client propagates
the fit to draw the live position, trail, and orbit. See
.claude/modules/observatory.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request

from api.db import canonical_timestamp, get_connection, now_canonical
from api.observatory import anchor_observer, reconstruct_tracks
from api.params import parse_time
from common.orbits import fit_orbit
from common.satgeo import EARTH_RADIUS_M, observer_ecef

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
    inverted to an ECEF position against its constellation's orbital sphere
    (``samples``, kilometres); unmodelled constellations (e.g. IMES) are skipped.
    Window defaults to the trailing 24h.

    Where the orbit fits (:func:`common.orbits.fit_orbit`), each satellite also
    carries its inertial-frame ``orbit`` parameters (epoch, radius, mean motion,
    epoch phase, and the in-plane ``u``/``v`` + ``normal`` unit basis). The client
    propagates these with the same two-body model to place the live dot, its
    trailing path, the instantaneous orbit ring, and the focused full-period
    orbit — one fit, one propagator, so all four agree. ``orbit`` is null when the
    fit fails (the client then falls back to the last observed sample).
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
        samples_json = [
            {
                't': s.t,
                'x': s.ecef_m[0] / 1000.0,
                'y': s.ecef_m[1] / 1000.0,
                'z': s.ecef_m[2] / 1000.0,
                'snr': s.snr,
                'used': s.used,
            }
            for s in samples
        ]
        fit = fit_orbit([(s.unix, s.ecef_m) for s in samples], gid)
        orbit = None
        if fit is not None:
            orbit = {
                'epoch': fit.epoch_unix,
                'radius_km': fit.radius_m / 1000.0,
                'n': fit.n_rad_s,
                'phase0': fit.phase0_rad,
                'u': list(fit.u),
                'v': list(fit.v),
                'normal': list(fit.normal),
            }
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
