"""Satellite pass prediction: the schedule API plus its page.

``/api/passes`` fits each recently observed satellite's orbit from the
accumulated az/el (see :mod:`common.orbits`) and propagates it forward to list
the upcoming above-horizon passes — rise, peak, and set for the parked observer.
``/passes`` renders that schedule. See .claude/modules/observatory.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, request

from api.db import canonical_timestamp, get_connection, now_canonical
from api.observatory import anchor_observer, reconstruct_tracks, sat_name
from api.params import ErrorResponse
from common.gpsd import GNSS_NAMES
from common.orbits import Orbit, Pass, azel_at, find_passes, fit_orbit
from common.satgeo import Vec3, observer_ecef

passes_bp = Blueprint('passes', __name__)

# Trailing span of observations the orbit fit draws on. A few days captures
# multiple passes per SV, which pins the orbital plane well; older data adds
# little and risks spanning a relocation of the (parked) van.
_FIT_WINDOW_HOURS = 72
_DEFAULT_HORIZON_HOURS = 12.0
_MAX_HORIZON_HOURS = 48.0
_DEFAULT_MASK_DEG = 5.0
_MAX_MASK_DEG = 30.0
# Evenly spaced az/el samples per pass when track=1 (the skyplot arc overlay).
_TRACK_POINTS = 32


def _parse_float(
    name: str, default: float, maximum: float
) -> tuple[float | None, ErrorResponse | None]:
    """Parse an optional non-negative float query param, clamped to ``maximum``.

    Zero is allowed (a 0° mask means draw arcs down to the horizon); only
    negatives and non-numbers are rejected.
    """
    raw = request.args.get(name)
    if raw is None:
        return default, None
    try:
        value = float(raw)
    except ValueError:
        return None, (jsonify({'error': f"'{name}' must be a number"}), 400)
    if value < 0.0:
        return None, (jsonify({'error': f"'{name}' must be >= 0"}), 400)
    return min(value, maximum), None


def _iso(unix_s: float) -> str:
    """Format Unix seconds as a whole-second UTC ISO string."""
    return datetime.fromtimestamp(unix_s, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _sample_track(
    orbit: Orbit, p: Pass, lat: float, lon: float, observer_m: Vec3
) -> list[list[float]]:
    """Evenly spaced ``[az, el]`` samples along a pass, rise to set.

    Used by the skyplot overlay to draw the predicted arc across the dome.
    """
    span = p.set_unix - p.rise_unix
    out: list[list[float]] = []
    for i in range(_TRACK_POINTS):
        t = p.rise_unix + span * i / (_TRACK_POINTS - 1)
        az, el, _ = azel_at(orbit, t, lat, lon, observer_m)
        out.append([round(az, 1), round(el, 1)])
    return out


def _pass_json(
    gnssid: int,
    svid: int,
    p: Pass,
    now_unix: float,
    snr: float | None,
    used: bool,
    track: list[list[float]] | None,
) -> dict:
    """Serialise a Pass with display-rounded angles and ISO times."""
    return {
        'gnssid': gnssid,
        'svid': svid,
        'name': sat_name(gnssid, svid),
        'system': GNSS_NAMES.get(gnssid, 'Other'),
        'rise_unix': round(p.rise_unix),
        'rise_iso': _iso(p.rise_unix),
        'rise_az': round(p.rise_az_deg, 1),
        'peak_unix': round(p.peak_unix),
        'peak_iso': _iso(p.peak_unix),
        'peak_el': round(p.peak_el_deg, 1),
        'peak_az': round(p.peak_az_deg, 1),
        'set_unix': round(p.set_unix),
        'set_iso': _iso(p.set_unix),
        'set_az': round(p.set_az_deg, 1),
        'duration_s': round(p.duration_s),
        'in_progress': p.rise_unix <= now_unix + 1.0,
        'max_snr': round(snr, 1) if snr is not None else None,
        'used': used,
        'track': track,
    }


@passes_bp.get('/api/passes')
def passes():
    """Predicted upcoming satellite passes for the parked observer.

    Fits each satellite seen in the trailing fit window from its reconstructed
    az/el track, then propagates forward over the horizon to find above-mask
    passes. Query params: ``hours`` (horizon, default 12, max 48) and ``mask``
    (horizon elevation in degrees, default 5, max 30). Satellites that can't be
    fit (too little arc, or an orbit class inconsistent with the gnssid's nominal
    radius) are skipped and counted, not failed.
    """
    horizon_hours, err = _parse_float('hours', _DEFAULT_HORIZON_HOURS, _MAX_HORIZON_HOURS)
    if err:
        return err
    mask_deg, err = _parse_float('mask', _DEFAULT_MASK_DEG, _MAX_MASK_DEG)
    if err:
        return err
    assert horizon_hours is not None and mask_deg is not None

    want_track = request.args.get('track') in ('1', 'true', 'yes')

    now = datetime.now(UTC)
    end_ts = now_canonical()
    fit_start_ts = canonical_timestamp((now - timedelta(hours=_FIT_WINDOW_HOURS)).isoformat())

    conn = get_connection()
    obs = anchor_observer(conn, end_ts)
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
        [fit_start_ts, end_ts],
    ).fetchall()
    tracks = reconstruct_tracks(rows, lat, lon, origin)

    now_unix = now.timestamp()
    horizon_end = now_unix + horizon_hours * 3600.0
    out: list[dict] = []
    n_fit = 0
    for (gnssid, svid), samples in tracks.items():
        orbit = fit_orbit([(s.unix, s.ecef_m) for s in samples], gnssid)
        if orbit is None:
            continue
        n_fit += 1
        snrs = [s.snr for s in samples if s.snr is not None]
        max_snr = max(snrs) if snrs else None
        used = any(s.used for s in samples)
        for p in find_passes(orbit, lat, lon, origin, now_unix, horizon_end, mask_deg):
            track = _sample_track(orbit, p, lat, lon, origin) if want_track else None
            out.append(_pass_json(gnssid, svid, p, now_unix, max_snr, used, track))

    out.sort(key=lambda d: d['rise_unix'])
    return jsonify(
        {
            'observer': {'lat': lat, 'lon': lon, 'alt': alt, 'timestamp': obs['timestamp']},
            'generated': end_ts,
            'horizon_hours': horizon_hours,
            'mask_deg': mask_deg,
            'fit_window_hours': _FIT_WINDOW_HOURS,
            'counts': {'observed': len(tracks), 'fit': n_fit},
            'passes': out,
        }
    )
