from datetime import datetime

from flask import Blueprint, jsonify, request

from api.db import get_connection

points_bp = Blueprint('points', __name__)


def _parse_timestamp(value: str, param: str):
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value
    except ValueError:
        return None, jsonify({'error': f"Invalid timestamp for '{param}': {value}"}), 400


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

    conn = get_connection()
    rows = conn.execute(
        "SELECT id, timestamp, lat, lon, speed, altitude, track "
        "FROM gps_points WHERE timestamp >= ? AND timestamp <= ? "
        "ORDER BY timestamp ASC LIMIT ?",
        (start, end, limit),
    ).fetchall()

    return jsonify({'points': [dict(r) for r in rows], 'count': len(rows)})
