"""Drone telemetry tier API — ingest + map-overlay reads.

The fourth data stream's HTTP surface (``.claude/modules/drone.md``):

* ``POST /api/drone/flights`` — the remote ingest path. ``import_drone.py --api``
  (the laptop manual path over the van LAN) extracts + thins a clip and POSTs the
  thinned flight here; the server reconstructs it and writes it through the *same*
  idempotent :func:`tools.import_drone.load_flight` the Pi-side sync uses, so the
  ``(model_code, first_fix_utc)`` natural key dedups both paths identically.
* ``GET /api/drone/flights`` — the map-overlay read, bbox/time filtered like
  ``/api/points``, with each flight's thinned track embedded by default.

The client sends only identity + the thinned points; the server *derives* the
time bounds, bbox, and point count from those points (Reumann–Witkam always keeps
both endpoints, so ``points[0]``/``points[-1]`` are the first/last fix). Deriving
server-side keeps the natural key consistent with the Pi-side loader and avoids
trusting client-computed bounds.
"""

from flask import Blueprint, jsonify, request

from api.db import canonical_timestamp, get_connection
from api.params import parse_bbox, parse_time
from tools.import_drone import Clip, Flight, TrackPoint, load_flight

drone_bp = Blueprint('drone', __name__)

# Flight metadata columns returned to the map layer (every column but none of the
# track points, which are joined in separately when requested).
_FLIGHT_COLUMNS = (
    'id, model, model_code, first_fix_utc, last_fix_utc, media_path, '
    'source_name, n_points, min_lat, min_lon, max_lat, max_lon, imported_at'
)


def _flight_from_json(body: dict) -> tuple[Flight | None, tuple | None]:
    """Reconstruct a :class:`Flight` from a POST body, validating as we go.

    The body carries identity (``model``/``model_code`` and optional
    ``media_path``/``source_name``) plus the thinned ``points``; the flight's time
    bounds, bbox, and point count are derived here from the points rather than
    trusted from the client. Points are sorted by timestamp so ``first``/``last``
    and the stored order are well-defined regardless of send order.

    Args:
        body: The decoded JSON request body.

    Returns:
        A ``(flight, None)`` pair on success, or ``(None, error_response)`` where
        ``error_response`` is a Flask ``(json, status)`` tuple.
    """
    for field in ('model', 'model_code', 'points'):
        if not body.get(field):
            return None, (jsonify({'error': f"'{field}' is required"}), 400)
    raw_points = body['points']
    if not isinstance(raw_points, list):
        return None, (jsonify({'error': "'points' must be a non-empty list"}), 400)

    points: list[TrackPoint] = []
    for i, p in enumerate(raw_points):
        try:
            timestamp = canonical_timestamp(p['timestamp'])
            lat = float(p['lat'])
            lon = float(p['lon'])
            abs_alt = None if p.get('abs_alt') is None else float(p['abs_alt'])
            importance = float(p.get('importance', 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            return None, (jsonify({'error': f'point[{i}] is invalid: {exc}'}), 400)
        points.append(TrackPoint(timestamp, lat, lon, abs_alt, importance))

    points.sort(key=lambda p: p.timestamp)
    lats = [p.lat for p in points]
    lons = [p.lon for p in points]
    clip = Clip(
        ref='',
        name=body.get('source_name') or '',
        media_path=body.get('media_path'),
        model=str(body['model']),
        model_code=str(body['model_code']),
    )
    flight = Flight(
        clip=clip,
        first_fix_utc=points[0].timestamp,
        last_fix_utc=points[-1].timestamp,
        points=points,
        bbox=(min(lats), min(lons), max(lats), max(lons)),
    )
    return flight, None


@drone_bp.post('/api/drone/flights')
def ingest_flight():
    """Idempotent remote ingest of one extracted + thinned drone flight.

    Writes through :func:`tools.import_drone.load_flight`, so a re-POST of the same
    clip is a no-op (``skipped``) and an SD-card flight later seen on the NAS
    backfills its canonical ``media_path``. Returns 201 for a fresh insert, 200 for
    a skip/backfill of an already-known flight.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'JSON object body required'}), 400
    flight, err = _flight_from_json(body)
    if err:
        return err

    conn = get_connection()
    outcome = load_flight(conn, flight)
    row = conn.execute(
        'SELECT id FROM drone_flights WHERE model_code = ? AND first_fix_utc = ?',
        (flight.clip.model_code, flight.first_fix_utc),
    ).fetchone()
    status = 201 if outcome == 'imported' else 200
    return jsonify({'outcome': outcome, 'flight_id': row['id'] if row else None}), status


@drone_bp.get('/api/drone/flights')
def list_flights():
    """List drone flights for the map overlay, bbox/time filtered.

    A flight matches the time window when its ``[first_fix_utc, last_fix_utc]``
    interval overlaps ``[start, end]``, and the bbox when its stored bounds overlap
    ``bbox=W,S,E,N`` — both interval/box *overlap*, not containment, so a flight
    partially in view still returns. All filters are optional (no filter ⇒ every
    flight). Each flight embeds its thinned track in time order unless
    ``points=0``.
    """
    conn = get_connection()

    where: list[str] = []
    params: list = []

    for value, name, column, op in (
        (request.args.get('start'), 'start', 'last_fix_utc', '>='),
        (request.args.get('end'), 'end', 'first_fix_utc', '<='),
    ):
        if value is None:
            continue
        canonical, err = parse_time(value, name)
        if err:
            return err
        where.append(f'{column} {op} ?')
        params.append(canonical)

    bbox, err = parse_bbox(request.args)
    if err:
        return err
    if bbox is not None:
        w, s, e, n = bbox
        where += ['max_lon >= ?', 'min_lon <= ?', 'max_lat >= ?', 'min_lat <= ?']
        params += [w, e, s, n]

    sql = f'SELECT {_FLIGHT_COLUMNS} FROM drone_flights'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY first_fix_utc ASC'
    flights = [dict(r) for r in conn.execute(sql, params).fetchall()]

    if request.args.get('points') != '0' and flights:
        ids = [f['id'] for f in flights]
        placeholders = ','.join('?' * len(ids))
        rows = conn.execute(
            'SELECT flight_id, timestamp, lat, lon, abs_alt, importance '
            f'FROM drone_track_points WHERE flight_id IN ({placeholders}) '
            'ORDER BY flight_id, timestamp',
            ids,
        ).fetchall()
        by_flight: dict[int, list[dict]] = {}
        for row in rows:
            point = dict(row)
            by_flight.setdefault(point.pop('flight_id'), []).append(point)
        for flight in flights:
            flight['points'] = by_flight.get(flight['id'], [])

    return jsonify({'flights': flights, 'count': len(flights)})
