from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from api.db import get_connection

trips_bp = Blueprint('trips', __name__)

ALLOWED_TRIP_FIELDS = {'name', 'start_time', 'end_time', 'notes'}


def _validate_timestamp(value: str, name: str):
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return jsonify({'error': f"Invalid timestamp for '{name}': {value}"}), 400
    return None


@trips_bp.get('/api/trips')
def list_trips():
    conn = get_connection()
    rows = conn.execute("""
        SELECT t.id, t.name, t.start_time, t.end_time, t.notes,
               (SELECT COUNT(*) FROM gps_points p
                WHERE p.timestamp >= t.start_time AND p.timestamp <= t.end_time
               ) AS point_count
        FROM trips t
        ORDER BY t.start_time DESC
    """).fetchall()
    return jsonify({'trips': [dict(r) for r in rows]})


@trips_bp.post('/api/trips')
def create_trip():
    body = request.get_json(silent=True) or {}
    name = body.get('name', '').strip()
    start_time = body.get('start_time', '')
    end_time = body.get('end_time', '')
    notes = body.get('notes', '')

    if not name:
        return jsonify({'error': "'name' is required"}), 400

    for value, field in ((start_time, 'start_time'), (end_time, 'end_time')):
        if not value:
            return jsonify({'error': f"'{field}' is required"}), 400
        err = _validate_timestamp(value, field)
        if err:
            return err

    if start_time >= end_time:
        return jsonify({'error': "'start_time' must be before 'end_time'"}), 400

    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO trips (name, start_time, end_time, notes) VALUES (?, ?, ?, ?)",
        (name, start_time, end_time, notes),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM trips WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@trips_bp.patch('/api/trips/<int:trip_id>')
def update_trip(trip_id):
    conn = get_connection()
    if not conn.execute("SELECT 1 FROM trips WHERE id = ?", (trip_id,)).fetchone():
        return jsonify({'error': 'Trip not found'}), 404

    body = request.get_json(silent=True) or {}
    updates = {k: v for k, v in body.items() if k in ALLOWED_TRIP_FIELDS}
    if not updates:
        return jsonify({'error': 'No valid fields to update'}), 400

    for field in ('start_time', 'end_time'):
        if field in updates:
            err = _validate_timestamp(updates[field], field)
            if err:
                return err

    if 'start_time' in updates or 'end_time' in updates:
        existing = conn.execute(
            "SELECT start_time, end_time FROM trips WHERE id = ?", (trip_id,)
        ).fetchone()
        effective_start = updates.get('start_time', existing['start_time'])
        effective_end = updates.get('end_time', existing['end_time'])
        if effective_start >= effective_end:
            return jsonify({'error': "'start_time' must be before 'end_time'"}), 400

    set_clause = ', '.join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE trips SET {set_clause} WHERE id = ?",
        (*updates.values(), trip_id),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    return jsonify(dict(row))


@trips_bp.delete('/api/trips/<int:trip_id>')
def delete_trip(trip_id):
    conn = get_connection()
    if not conn.execute("SELECT 1 FROM trips WHERE id = ?", (trip_id,)).fetchone():
        return jsonify({'error': 'Trip not found'}), 404
    conn.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
    conn.commit()
    return '', 204


@trips_bp.get('/api/trips/mark')
def get_marks():
    conn = get_connection()
    rows = conn.execute("SELECT key, timestamp FROM marks").fetchall()
    return jsonify({r['key']: r['timestamp'] for r in rows})


@trips_bp.post('/api/trips/mark')
def mark_timestamp():
    body = request.get_json(silent=True) or {}
    marker = body.get('marker', '')
    if marker not in ('start', 'end'):
        return jsonify({'error': "'marker' must be 'start' or 'end'"}), 400

    timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    conn = get_connection()
    conn.execute(
        "INSERT INTO marks (key, timestamp) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET timestamp = excluded.timestamp",
        (marker, timestamp),
    )
    conn.commit()

    rows = conn.execute("SELECT key, timestamp FROM marks").fetchall()
    result = {r['key']: r['timestamp'] for r in rows}
    return jsonify(result)
