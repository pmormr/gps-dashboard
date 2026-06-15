from datetime import datetime

from flask import Blueprint, jsonify, request

from api.db import get_connection

points_bp = Blueprint('points', __name__)



@points_bp.get('/api/points/latest')
def latest_point():
    conn = get_connection()
    row = conn.execute(
        "SELECT id, timestamp, lat, lon, speed, altitude, track "
        "FROM gps_points ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return jsonify({'error': 'No GPS data available yet'}), 404
    return jsonify(dict(row))


@points_bp.get('/api/points')
def get_points():
    start = request.args.get('start')
    end = request.args.get('end')

    if not start or not end:
        return jsonify({'error': "'start' and 'end' query params are required"}), 400

    for value, name in ((start, 'start'), (end, 'end')):
        try:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return jsonify({'error': f"Invalid timestamp for '{name}': {value}"}), 400

    try:
        limit = min(int(request.args.get('limit', 5000)), 20000)
    except ValueError:
        return jsonify({'error': "'limit' must be an integer"}), 400

    bucket = request.args.get('bucket')
    if bucket is not None:
        try:
            bucket = int(bucket)
        except ValueError:
            return jsonify({'error': "'bucket' must be an integer (seconds)"}), 400
        if bucket <= 0:
            return jsonify({'error': "'bucket' must be > 0"}), 400

    bbox_str = request.args.get('bbox')
    bbox = None
    if bbox_str is not None:
        parts = bbox_str.split(',')
        if len(parts) != 4:
            return jsonify({'error': "'bbox' must be 'W,S,E,N' (4 comma-separated floats)"}), 400
        try:
            w, s, e, n = (float(p) for p in parts)
        except ValueError:
            return jsonify({'error': "'bbox' must be 4 floats"}), 400
        if w > e or s > n:
            return jsonify({'error': "'bbox' must have W<=E and S<=N"}), 400
        bbox = (w, s, e, n)

    where = ["timestamp >= ?", "timestamp <= ?"]
    params: list = [start, end]
    if bbox is not None:
        where += ["lat BETWEEN ? AND ?", "lon BETWEEN ? AND ?"]
        params += [bbox[1], bbox[3], bbox[0], bbox[2]]

    if bucket is not None:
        # Pick one row per N-second bucket. SQLite resolves bare columns under
        # GROUP BY indeterminately, which is exactly the "representative point"
        # behavior we want — within a small bucket the position barely moves.
        sql = (
            "SELECT id, timestamp, lat, lon, speed, altitude, track "
            "FROM gps_points WHERE " + " AND ".join(where) + " "
            "GROUP BY CAST(strftime('%s', timestamp) AS INTEGER) / ? "
            "ORDER BY timestamp ASC LIMIT ?"
        )
        params += [bucket, limit]
    else:
        sql = (
            "SELECT id, timestamp, lat, lon, speed, altitude, track "
            "FROM gps_points WHERE " + " AND ".join(where) + " "
            "ORDER BY timestamp ASC LIMIT ?"
        )
        params.append(limit)

    conn = get_connection()
    rows = conn.execute(sql, params).fetchall()

    points = [dict(r) for r in rows]
    return jsonify({'points': points, 'count': len(points), 'truncated': len(points) == limit})
