"""Phone-tracking API — the map-overlay reads for both phone tiers.

The phone theme's HTTP surface, read-only over two separate tiers (history:
``.claude/modules/phone.md``; live: ``plans/phone-tracking-plan.md``). Neither
has an ingest route — the history tier is loaded by a batch tool run on the Pi
(``tools/import_phone_timeline.py``), the live tier by a timer-driven pull
(``tools/sync_owntracks.py``).

Timeline history (``phone_*``, Google Timeline import):

* ``GET /api/phone/tracks`` — the breadcrumb. ``phone_paths`` whose interval
  overlaps the window, each with its thinned points embedded. Size-guarded like
  ``/api/points``: every path's endpoints (``importance = 0``, the polyline's
  skeleton) are always kept, then the remaining ``limit`` budget is filled with
  the highest-``importance`` interior vertices. ``truncated`` ⇒ interior vertices
  were dropped (endpoints never are).
* ``GET /api/phone/places`` — the semantic layer: ``phone_visits`` +
  ``phone_activities`` overlapping the window.

OwnTracks live (``owntracks_points``, Recorder pull):

* ``GET /api/phone/owntracks`` — points in the window, oldest first.
* ``GET /api/phone/owntracks/latest`` — the most recent fix per device (the
  live-marker read).

All filters (``start``/``end``/``bbox``) are optional; interval rows match on
overlap, not containment, so a segment partially in view still returns.
"""

from flask import Blueprint, jsonify, request

from api.db import get_connection
from api.params import (
    bbox_overlap_where,
    bbox_point_where,
    parse_bbox,
    parse_limit,
    time_overlap_where,
)

phone_bp = Blueprint('phone', __name__)

_PATH_COLUMNS = (
    'id, start_time, end_time, n_points, min_lat, min_lon, max_lat, max_lon, imported_at'
)
_POINT_COLUMNS = 'path_id, timestamp, lat, lon, importance, activity_type'
_VISIT_COLUMNS = 'id, start_time, end_time, lat, lon, place_id, semantic_type, probability'
_ACTIVITY_COLUMNS = (
    'id, start_time, end_time, start_lat, start_lon, end_lat, end_lon, '
    'distance_m, activity_type, probability'
)
_OWNTRACKS_COLUMNS = 'user, device, timestamp, lat, lon, accuracy, altitude, velocity, battery'


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

    where, params, err = time_overlap_where(request.args, 'start_time', 'end_time')
    if err:
        return err

    bbox, err = parse_bbox(request.args)
    if err:
        return err
    if bbox is not None:
        bbox_where, bbox_params = bbox_overlap_where(bbox)
        where += bbox_where
        params += bbox_params

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
            f'SELECT {_POINT_COLUMNS} FROM phone_track_points '
            f'WHERE path_id IN ({placeholders}) AND importance = 0.0 '
            'ORDER BY path_id, timestamp',
            ids,
        ).fetchall()
        interior_budget = max(0, limit - len(endpoints))
        interior = conn.execute(
            f'SELECT {_POINT_COLUMNS} FROM phone_track_points '
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

    time_where, time_params, err = time_overlap_where(request.args, 'start_time', 'end_time')
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
        visit_clauses, visit_bbox_params = bbox_point_where(bbox)
        visit_where += visit_clauses
        visit_params += visit_bbox_params
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


@phone_bp.get('/api/phone/owntracks')
def list_owntracks():
    """Live-tier (OwnTracks) points in the window, oldest first.

    Points filter on their single ``timestamp`` (``start``/``end``), plus
    optional ``bbox`` and ``device``; capped at ``limit`` in time order, so a
    hit cap (``truncated``) drops the newest points, never reorders.
    """
    conn = get_connection()

    where, params, err = time_overlap_where(request.args, 'timestamp', 'timestamp')
    if err:
        return err

    bbox, err = parse_bbox(request.args)
    if err:
        return err
    if bbox is not None:
        clauses, bbox_params = bbox_point_where(bbox)
        where += clauses
        params += bbox_params

    device = request.args.get('device')
    if device:
        where.append('device = ?')
        params.append(device)

    limit, err = parse_limit(request.args, default=10000, maximum=50000)
    if err:
        return err

    sql = f'SELECT {_OWNTRACKS_COLUMNS} FROM owntracks_points'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY timestamp ASC LIMIT ?'
    points = [dict(r) for r in conn.execute(sql, [*params, limit]).fetchall()]
    return jsonify({'points': points, 'count': len(points), 'truncated': len(points) == limit})


@phone_bp.get('/api/phone/owntracks/latest')
def latest_owntracks():
    """The most recent fix per device — the live-marker read.

    One row per (user, device): with a lone ``MAX(timestamp)`` aggregate,
    SQLite guarantees the other selected columns come from the max row.
    """
    conn = get_connection()
    rows = conn.execute(
        'SELECT user, device, MAX(timestamp) AS timestamp, lat, lon, accuracy, '
        'altitude, velocity, battery, synced_at '
        'FROM owntracks_points GROUP BY user, device ORDER BY user, device'
    ).fetchall()
    devices = [dict(r) for r in rows]
    return jsonify({'devices': devices, 'count': len(devices)})


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
