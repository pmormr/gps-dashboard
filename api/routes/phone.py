"""Phone location-history tier API — the map-overlay reads.

The phone tier's HTTP surface (``plans/phone-history-plan.md``): two read-only
overlays over the Google Timeline history imported by
``tools/import_phone_timeline.py``. There is no ingest route — the import is a
batch tool run on the Pi against the live DB, not a LAN POST (unlike drone).

* ``GET /api/phone/tracks`` — the breadcrumb. ``phone_paths`` whose interval
  overlaps the window, each with its thinned points embedded. Size-guarded like
  ``/api/points``: every path's endpoints (``importance = 0``, the polyline's
  skeleton) are always kept, then the remaining ``limit`` budget is filled with
  the highest-``importance`` interior vertices. ``truncated`` ⇒ interior vertices
  were dropped (endpoints never are).
* ``GET /api/phone/places`` — the semantic layer: ``phone_visits`` +
  ``phone_activities`` overlapping the window.

All filters (``start``/``end``/``bbox``) are optional; overlap, not containment,
so a segment partially in view still returns.
"""

from flask import Blueprint, jsonify, request

from api.db import get_connection
from api.params import parse_bbox, parse_limit, parse_time

phone_bp = Blueprint('phone', __name__)

_PATH_COLUMNS = (
    'id, start_time, end_time, n_points, min_lat, min_lon, max_lat, max_lon, imported_at'
)
_VISIT_COLUMNS = 'id, start_time, end_time, lat, lon, place_id, semantic_type, probability'
_ACTIVITY_COLUMNS = (
    'id, start_time, end_time, start_lat, start_lon, end_lat, end_lon, '
    'distance_m, activity_type, probability'
)


def _time_overlap_where(args) -> tuple[list[str], list, tuple | None]:
    """Build the start/end interval-overlap SQL clause shared by both reads.

    A segment ``[start_time, end_time]`` overlaps the window ``[start, end]`` when
    ``end_time >= start`` and ``start_time <= end`` — each bound optional.

    Args:
        args: The request args mapping (``request.args``).

    Returns:
        ``(where, params, None)`` on success, or ``([], [], error_response)`` when
        a timestamp is malformed.
    """
    where: list[str] = []
    params: list = []
    for value, name, column, op in (
        (args.get('start'), 'start', 'end_time', '>='),
        (args.get('end'), 'end', 'start_time', '<='),
    ):
        if value is None:
            continue
        canonical, err = parse_time(value, name)
        if err:
            return [], [], err
        where.append(f'{column} {op} ?')
        params.append(canonical)
    return where, params, None


@phone_bp.get('/api/phone/tracks')
def list_tracks():
    """Breadcrumb paths overlapping the window, thinned points embedded.

    Endpoints (``importance = 0``) are kept for every overlapping path so each
    polyline always has its skeleton; the rest of the ``limit`` budget goes to the
    highest-``importance`` interior vertices, ranked globally across the returned
    paths, then regrouped per path in time order. ``truncated`` reports interior
    loss (the endpoint skeleton is never dropped).
    """
    conn = get_connection()

    where, params, err = _time_overlap_where(request.args)
    if err:
        return err

    bbox, err = parse_bbox(request.args)
    if err:
        return err
    if bbox is not None:
        w, s, e, n = bbox
        where += ['max_lon >= ?', 'min_lon <= ?', 'max_lat >= ?', 'min_lat <= ?']
        params += [w, e, s, n]

    limit, err = parse_limit(request.args, default=10000, maximum=50000)
    if err:
        return err

    sql = f'SELECT {_PATH_COLUMNS} FROM phone_paths'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY start_time ASC'
    paths = [dict(r) for r in conn.execute(sql, params).fetchall()]

    truncated = False
    if paths:
        ids = [p['id'] for p in paths]
        placeholders = ','.join('?' * len(ids))
        endpoints = conn.execute(
            'SELECT path_id, timestamp, lat, lon, importance FROM phone_track_points '
            f'WHERE path_id IN ({placeholders}) AND importance = 0.0 '
            'ORDER BY path_id, timestamp',
            ids,
        ).fetchall()
        interior_budget = max(0, limit - len(endpoints))
        interior = conn.execute(
            'SELECT path_id, timestamp, lat, lon, importance FROM phone_track_points '
            f'WHERE path_id IN ({placeholders}) AND importance > 0.0 '
            'ORDER BY importance DESC LIMIT ?',
            [*ids, interior_budget],
        ).fetchall()
        truncated = len(interior) == interior_budget

        by_path: dict[int, list[dict]] = {}
        for row in (*endpoints, *interior):
            point = dict(row)
            by_path.setdefault(point.pop('path_id'), []).append(point)
        for points in by_path.values():
            points.sort(key=lambda p: p['timestamp'])
        for path in paths:
            path['points'] = by_path.get(path['id'], [])

    return jsonify({'paths': paths, 'count': len(paths), 'truncated': truncated})


@phone_bp.get('/api/phone/places')
def list_places():
    """Place visits + trip activities overlapping the window.

    Visits filter their point location against the bbox; activities match when
    either their start or end falls in it. Each list is capped at ``limit`` in
    time order; ``truncated`` is set if either hit the cap.
    """
    conn = get_connection()

    time_where, time_params, err = _time_overlap_where(request.args)
    if err:
        return err

    bbox, err = parse_bbox(request.args)
    if err:
        return err

    limit, err = parse_limit(request.args, default=5000, maximum=20000)
    if err:
        return err

    visit_where = list(time_where)
    visit_params = list(time_params)
    activity_where = list(time_where)
    activity_params = list(time_params)
    if bbox is not None:
        w, s, e, n = bbox
        visit_where += ['lat BETWEEN ? AND ?', 'lon BETWEEN ? AND ?']
        visit_params += [s, n, w, e]
        activity_where.append(
            '((start_lat BETWEEN ? AND ? AND start_lon BETWEEN ? AND ?) '
            'OR (end_lat BETWEEN ? AND ? AND end_lon BETWEEN ? AND ?))'
        )
        activity_params += [s, n, w, e, s, n, w, e]

    visits = _select_places(conn, 'phone_visits', _VISIT_COLUMNS, visit_where, visit_params, limit)
    activities = _select_places(
        conn, 'phone_activities', _ACTIVITY_COLUMNS, activity_where, activity_params, limit
    )

    truncated = len(visits) == limit or len(activities) == limit
    return jsonify(
        {
            'visits': visits,
            'activities': activities,
            'count': len(visits) + len(activities),
            'truncated': truncated,
        }
    )


def _select_places(conn, table: str, columns: str, where: list[str], params: list, limit: int):
    """Run one capped, time-ordered ``phone_visits``/``phone_activities`` read.

    Args:
        conn: Open SQLite connection.
        table: The table to read.
        columns: The column list to select.
        where: Assembled WHERE clauses (may be empty).
        params: Bound parameters for ``where``.
        limit: Row cap.

    Returns:
        The selected rows as dicts, in ``start_time`` order.
    """
    sql = f'SELECT {columns} FROM {table}'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY start_time ASC LIMIT ?'
    return [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]
