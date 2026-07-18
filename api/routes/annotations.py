from flask import Blueprint, jsonify, request

from api.db import get_connection
from api.params import parse_time

annotations_bp = Blueprint('annotations', __name__)

ALLOWED_ANNOTATION_FIELDS = {'name', 'start_time', 'end_time', 'notes'}


@annotations_bp.get('/api/annotations')
def list_annotations():
    """Return every annotation.

    Annotations span all history (not the loaded window), so the frontend can
    show them as pins/bands regardless of the current time picker selection.
    ``point_count`` is NULL for point annotations (NULL ``end_time``); for
    ranges it is the number of ``gps_points`` falling inside.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT a.id, a.name, a.start_time, a.end_time, a.notes,
               CASE
                 WHEN a.end_time IS NULL THEN NULL
                 ELSE (SELECT COUNT(*) FROM gps_points p
                       WHERE p.timestamp >= a.start_time AND p.timestamp <= a.end_time)
               END AS point_count
        FROM annotations a
        ORDER BY a.start_time DESC
    """).fetchall()
    return jsonify({'annotations': [dict(r) for r in rows]})


@annotations_bp.post('/api/annotations')
def create_annotation():
    """Create an annotation. Omit ``end_time`` (or send null) for a point bookmark."""
    body = request.get_json(silent=True) or {}
    name = body.get('name', '').strip()
    start_time = body.get('start_time', '')
    end_time = body.get('end_time')
    notes = body.get('notes', '')

    if not name:
        return jsonify({'error': "'name' is required"}), 400
    if not start_time:
        return jsonify({'error': "'start_time' is required"}), 400

    start_time, err = parse_time(start_time, 'start_time')
    if err:
        return err

    if end_time:
        end_time, err = parse_time(end_time, 'end_time')
        if err:
            return err
        if start_time >= end_time:
            return jsonify({'error': "'start_time' must be before 'end_time'"}), 400
    else:
        end_time = None

    conn = get_connection()
    cursor = conn.execute(
        'INSERT INTO annotations (name, start_time, end_time, notes) VALUES (?, ?, ?, ?)',
        (name, start_time, end_time, notes),
    )
    conn.commit()

    row = conn.execute('SELECT * FROM annotations WHERE id = ?', (cursor.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@annotations_bp.patch('/api/annotations/<int:annotation_id>')
def update_annotation(annotation_id):
    conn = get_connection()
    if not conn.execute('SELECT 1 FROM annotations WHERE id = ?', (annotation_id,)).fetchone():
        return jsonify({'error': 'Annotation not found'}), 404

    body = request.get_json(silent=True) or {}
    updates = {k: v for k, v in body.items() if k in ALLOWED_ANNOTATION_FIELDS}
    if not updates:
        return jsonify({'error': 'No valid fields to update'}), 400

    for field in ('start_time', 'end_time'):
        if field in updates and updates[field]:
            updates[field], err = parse_time(updates[field], field)
            if err:
                return err
        elif field == 'end_time' and field in updates and not updates[field]:
            updates[field] = None

    if 'start_time' in updates or 'end_time' in updates:
        existing = conn.execute(
            'SELECT start_time, end_time FROM annotations WHERE id = ?',
            (annotation_id,),
        ).fetchone()
        effective_start = updates.get('start_time', existing['start_time'])
        effective_end = updates.get('end_time', existing['end_time'])
        if effective_end is not None and effective_start >= effective_end:
            return jsonify({'error': "'start_time' must be before 'end_time'"}), 400

    set_clause = ', '.join(f'{k} = ?' for k in updates)
    conn.execute(
        f'UPDATE annotations SET {set_clause} WHERE id = ?',
        (*updates.values(), annotation_id),
    )
    conn.commit()

    row = conn.execute('SELECT * FROM annotations WHERE id = ?', (annotation_id,)).fetchone()
    return jsonify(dict(row))


@annotations_bp.delete('/api/annotations/<int:annotation_id>')
def delete_annotation(annotation_id):
    conn = get_connection()
    if not conn.execute('SELECT 1 FROM annotations WHERE id = ?', (annotation_id,)).fetchone():
        return jsonify({'error': 'Annotation not found'}), 404
    conn.execute('DELETE FROM annotations WHERE id = ?', (annotation_id,))
    conn.commit()
    return '', 204
