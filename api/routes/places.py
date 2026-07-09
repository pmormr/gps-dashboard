"""Places tier API — nearby-places and event-schedule reads.

The read surface over the tier synced by ``tools/import_places.py`` (see
``.claude/modules/places.md``). List reads return the queryable columns plus the
``summary`` teaser; the full ``details`` JSON (tour stops, operating hours,
amenities, fees) is fetched per-row by the detail endpoints, so browsing a
region never drags megabytes of display structure.

Every payload carries ``synced_at`` — schedule data is only as fresh as the last
sync, and the UI is expected to wear that age rather than present it as live.

* ``GET /api/places`` — bbox/kind/park/name-filtered POI list. Rows without
  a coordinate never match a bbox (their lat/lon is NULL) but appear in
  unfiltered or park/name-filtered reads.
* ``GET /api/places/<id>`` — one POI with parsed ``details``.
* ``GET /api/places/events`` — occurrences (event × date × time window) in
  a calendar-date window, grouped per event. Dates are park-local
  ``YYYY-MM-DD`` strings as published — not the ms-UTC axis.
* ``GET /api/places/events/<id>`` — one event with parsed ``details`` and
  its full occurrence list.
"""

from __future__ import annotations

import json
import re

from flask import Blueprint, Response, jsonify, request

from api.db import get_connection
from api.params import parse_bbox, parse_limit

places_bp = Blueprint('places', __name__)

_PLACE_COLUMNS = 'id, source, source_kind, source_id, park_code, name, lat, lon, summary, synced_at'
_EVENT_COLUMNS = (
    'id, source, source_id, park_code, name, lat, lon, location_text, '
    'is_free, needs_reservation, synced_at'
)
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _parse_date(value: str | None, name: str) -> tuple[str | None, tuple[Response, int] | None]:
    """Validate an optional ``YYYY-MM-DD`` calendar-date param.

    Event dates are park-local calendar dates (not ms-UTC timestamps), so this
    is a distinct axis from :func:`api.params.parse_time`.

    Args:
        value: The raw param value, or None when absent.
        name: Field name for the error message.

    Returns:
        ``(value, None)`` on success (None passes through), or
        ``(None, error_response)``.
    """
    if value is None or _DATE_RE.match(value):
        return value, None
    return None, (jsonify({'error': f"Invalid date for '{name}': {value} (want YYYY-MM-DD)"}), 400)


@places_bp.get('/api/places')
def list_places():
    """POI list: optional ``bbox``, ``kind`` (comma-separated), ``park``, ``q``.

    ``q`` is a case-insensitive name substring. Ordered by name; ``truncated``
    when the ``limit`` cap was hit.
    """
    bbox, err = parse_bbox(request.args)
    if err:
        return err
    limit, err = parse_limit(request.args, default=2000, maximum=10000)
    if err:
        return err

    where: list[str] = []
    params: list = []
    if bbox is not None:
        w, s, e, n = bbox
        where += ['lat BETWEEN ? AND ?', 'lon BETWEEN ? AND ?']
        params += [s, n, w, e]
    kind = request.args.get('kind')
    if kind:
        kinds = [k.strip() for k in kind.split(',') if k.strip()]
        where.append(f'source_kind IN ({",".join("?" * len(kinds))})')
        params += kinds
    park = request.args.get('park')
    if park:
        where.append('park_code = ?')
        params.append(park)
    q = request.args.get('q')
    if q:
        where.append('name LIKE ?')
        params.append(f'%{q}%')

    sql = f'SELECT {_PLACE_COLUMNS} FROM places'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY name ASC LIMIT ?'
    conn = get_connection()
    rows = [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]
    return jsonify({'places': rows, 'count': len(rows), 'truncated': len(rows) == limit})


@places_bp.get('/api/places/<int:place_id>')
def get_place(place_id: int):
    """One POI with its full parsed ``details`` (tour stops, hours, amenities…)."""
    conn = get_connection()
    row = conn.execute(
        f'SELECT {_PLACE_COLUMNS}, details FROM places WHERE id = ?',
        (place_id,),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'place not found'}), 404
    record = dict(row)
    record['details'] = json.loads(record['details'])
    return jsonify(record)


@places_bp.get('/api/places/events')
def list_events():
    """Occurrences in a calendar window, grouped per event.

    ``start``/``end`` bound the occurrence date (``YYYY-MM-DD``, both optional);
    ``bbox``/``park`` filter the event. ``limit`` caps *occurrences* (the join
    rows); an event's ``dates`` list holds only the occurrences that matched.
    Ordered by each event's first matching occurrence.
    """
    start, err = _parse_date(request.args.get('start'), 'start')
    if err:
        return err
    end, err = _parse_date(request.args.get('end'), 'end')
    if err:
        return err
    bbox, err = parse_bbox(request.args)
    if err:
        return err
    limit, err = parse_limit(request.args, default=500, maximum=5000)
    if err:
        return err

    where: list[str] = []
    params: list = []
    if start is not None:
        where.append('d.date >= ?')
        params.append(start)
    if end is not None:
        where.append('d.date <= ?')
        params.append(end)
    if bbox is not None:
        w, s, e, n = bbox
        where += ['e.lat BETWEEN ? AND ?', 'e.lon BETWEEN ? AND ?']
        params += [s, n, w, e]
    park = request.args.get('park')
    if park:
        where.append('e.park_code = ?')
        params.append(park)

    columns = ', '.join(f'e.{c.strip()}' for c in _EVENT_COLUMNS.split(','))
    sql = (
        f'SELECT {columns}, d.date, d.time_start, d.time_end '
        'FROM place_event_dates d JOIN place_events e ON e.id = d.event_id'
    )
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY d.date ASC, d.time_start ASC LIMIT ?'
    conn = get_connection()
    rows = conn.execute(sql, [*params, limit]).fetchall()

    events: dict[int, dict] = {}
    for row in rows:
        record = dict(row)
        occurrence = {
            'date': record.pop('date'),
            'time_start': record.pop('time_start'),
            'time_end': record.pop('time_end'),
        }
        events.setdefault(record['id'], {**record, 'dates': []})['dates'].append(occurrence)
    return jsonify(
        {
            'events': list(events.values()),
            'count': len(events),
            'truncated': len(rows) == limit,
        }
    )


@places_bp.get('/api/places/events/<int:event_id>')
def get_event(event_id: int):
    """One event with parsed ``details`` and its full occurrence list."""
    conn = get_connection()
    row = conn.execute(
        f'SELECT {_EVENT_COLUMNS}, details FROM place_events WHERE id = ?',
        (event_id,),
    ).fetchone()
    if row is None:
        return jsonify({'error': 'event not found'}), 404
    record = dict(row)
    record['details'] = json.loads(record['details'])
    record['dates'] = [
        dict(r)
        for r in conn.execute(
            'SELECT date, time_start, time_end FROM place_event_dates '
            'WHERE event_id = ? ORDER BY date ASC, time_start ASC',
            (event_id,),
        ).fetchall()
    ]
    return jsonify(record)
