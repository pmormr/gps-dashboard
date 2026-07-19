"""OBD-II van telemetry analytics over the logged ``obd_readings``.

Read-only and DB-backed. ``fuel_rate_lph`` is stored NULL; fuel rate is derived
here at read time via :mod:`common.obd`, so the speed-density calibration
constant re-scales all history at once. The fuel leg comes from
``obd_readings``, the distance leg from the denoised ``track_points`` tier.
"""

from flask import Blueprint, jsonify, request

from api.db import get_connection
from api.params import parse_time
from common.obd import derive_fuel_rate_lph, summarize_drive
from common.timefmt import epoch_seconds
from processor.simplify import track_length_m

obd_bp = Blueprint('obd', __name__)

_M_PER_MILE = 1609.344
_L_PER_GAL = 3.785411784


@obd_bp.get('/api/obd/economy')
def economy():
    """Per-window fuel economy: derived fuel ÷ GPS-track distance.

    Integrates speed-density fuel over ``obd_readings`` in ``[start, end]`` and
    divides by the ``track_points`` path length over the same window. Pass an
    annotation's bounds for per-trip economy. ``calibrated`` is always false until
    a fill-up calibration lands.
    """
    start = request.args.get('start')
    end = request.args.get('end')
    if not start or not end:
        return jsonify({'error': "'start' and 'end' query params are required"}), 400
    start, err = parse_time(start, 'start')
    if err:
        return err
    end, err = parse_time(end, 'end')
    if err:
        return err

    conn = get_connection()
    obd_rows = conn.execute(
        'SELECT timestamp, rpm, speed_kph, absolute_load_pct, commanded_equiv_ratio '
        'FROM obd_readings WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC',
        (start, end),
    ).fetchall()
    samples = [
        (
            epoch_seconds(r['timestamp']),
            derive_fuel_rate_lph(r['absolute_load_pct'], r['rpm'], r['commanded_equiv_ratio']),
            r['speed_kph'],
        )
        for r in obd_rows
    ]
    summary = summarize_drive(samples)

    track = conn.execute(
        'SELECT lat, lon FROM track_points '
        'WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC',
        (start, end),
    ).fetchall()
    coords = [(r['lat'], r['lon']) for r in track]
    distance_m = track_length_m(coords)

    gallons = summary.fuel_litres / _L_PER_GAL
    miles = distance_m / _M_PER_MILE
    mpg = miles / gallons if gallons > 0 else None
    l_per_100km = summary.fuel_litres / (distance_m / 1000.0) * 100.0 if distance_m > 0 else None
    speeds = [r['speed_kph'] for r in obd_rows if r['speed_kph'] is not None]
    max_speed_kph = max(speeds) if speeds else None

    return jsonify(
        {
            'start': start,
            'end': end,
            'fuel_litres': round(summary.fuel_litres, 4),
            'fuel_gallons': round(gallons, 4),
            'distance_m': round(distance_m, 1),
            'distance_mi': round(miles, 3),
            'mpg': round(mpg, 2) if mpg is not None else None,
            'l_per_100km': round(l_per_100km, 2) if l_per_100km is not None else None,
            'max_speed_kph': round(max_speed_kph, 1) if max_speed_kph is not None else None,
            'engine_on_s': round(summary.engine_on_s, 1),
            'moving_s': round(summary.moving_s, 1),
            'idle_s': round(summary.idle_s, 1),
            'n_obd_rows': len(obd_rows),
            'n_track_points': len(coords),
            'calibrated': False,
        }
    )
