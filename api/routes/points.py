from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, request

from api.db import canonical_timestamp, get_connection
from api.params import bbox_point_where, parse_bbox, parse_limit, parse_required_window

points_bp = Blueprint('points', __name__)


# Processed-tier columns the trail renderer reads. kind/n_raw/importance let the
# client size dots and reason about how much raw data each point represents;
# dwell_start/dwell_end/radius let it render a stop as a dwell-interval block on
# the timeline and select it by interval overlap (NULL on kind='track' rows).
_TRACK_COLUMNS = (
    'id, timestamp, lat, lon, speed, altitude, track, kind, n_raw, importance, accuracy, '
    'dwell_start, dwell_end, radius'
)


@points_bp.get('/api/points/latest')
def latest_point():
    # The live "current position" dot reads *raw* gps_points, not the processed
    # tier — so the marker tracks the true latest fix and never waits on a stop
    # row converging over its first minute.
    conn = get_connection()
    row = conn.execute(
        'SELECT id, timestamp, lat, lon, speed, altitude, track '
        'FROM gps_points ORDER BY timestamp DESC LIMIT 1'
    ).fetchone()
    if row is None:
        return jsonify({'error': 'No GPS data available yet'}), 404
    return jsonify(dict(row))


@points_bp.get('/api/points/recent')
def recent_points():
    """Raw-tier trailing window — the Drive view's breadcrumb seed.

    Reads raw ``gps_points``, not the processed tier: the open moving segment
    lags the processor's cursor, so a ``track_points`` trail would visibly stop
    short of the van. The trail is cosmetic — rows are stride-decimated to the
    ``limit`` budget (every Nth by insert order, newest fix always kept so the
    trail reaches the van), not importance-ranked like ``/api/points``.
    """
    minutes, err = parse_limit(request.args, default=30, maximum=24 * 60, key='minutes')
    if err:
        return err
    limit, err = parse_limit(request.args, default=500, maximum=5000)
    if err:
        return err

    cutoff = canonical_timestamp((datetime.now(UTC) - timedelta(minutes=minutes)).isoformat())
    conn = get_connection()
    rows = conn.execute(
        'SELECT timestamp, lat, lon FROM gps_points WHERE timestamp >= ? ORDER BY id',
        (cutoff,),
    ).fetchall()

    stride = max(1, -(-len(rows) // limit))  # ceil(len/limit)
    points = [dict(r) for r in rows[::stride]]
    if rows and (len(rows) - 1) % stride != 0:
        points.append(dict(rows[-1]))
    return jsonify(
        {'points': points, 'count': len(points), 'raw_count': len(rows), 'minutes': minutes}
    )


@points_bp.get('/api/points')
def get_points():
    """Trail/history points from the processed tier, size-aware decimated.

    Reads ``track_points`` (the denoised/simplified tier), not raw ``gps_points``.
    Every ``kind='stop'`` whose dwell overlaps the window always survives; the
    remaining ``limit`` budget is filled with the highest-``importance``
    ``kind='track'`` vertices, then the result is re-sorted by time for rendering.
    ``importance`` is comparable only
    *within* the moving kind (perpendicular deviation in metres; a stop's is dwell
    seconds), so stops are taken unconditionally rather than ranked against them.
    ``truncated`` therefore means moving vertices were dropped — stops never are.
    """
    start, end, err = parse_required_window(request.args)
    if err:
        return err

    limit, err = parse_limit(request.args, default=5000, maximum=20000)
    if err:
        return err

    bbox, err = parse_bbox(request.args)
    if err:
        return err

    bbox_where: list[str] = []
    bbox_params: list = []
    if bbox is not None:
        bbox_where, bbox_params = bbox_point_where(bbox)

    conn = get_connection()

    # Stops first (always kept), then top-importance moving vertices fill what's
    # left of the budget. Two queries because the ranking is per-kind — and the
    # time match differs by kind too:
    #
    #   * A moving vertex is an instant, matched on its `timestamp`.
    #   * A stop spans `[dwell_start, dwell_end]`, so it's matched on *interval
    #     overlap* with the window — otherwise a long open dwell (whose
    #     representative timestamp sits at its start) drops out of any recent
    #     window even while the van is still parked there, e.g. "last 1h" while
    #     parked overnight would render empty.
    stop_where = ["kind = 'stop'", 'dwell_end >= ?', 'dwell_start <= ?', *bbox_where]
    stops = conn.execute(
        f'SELECT {_TRACK_COLUMNS} FROM track_points '
        f'WHERE {" AND ".join(stop_where)} ORDER BY dwell_start ASC',
        [start, end, *bbox_params],
    ).fetchall()

    move_where = ["kind = 'track'", 'timestamp >= ?', 'timestamp <= ?', *bbox_where]
    move_budget = max(0, limit - len(stops))
    moving = conn.execute(
        f'SELECT {_TRACK_COLUMNS} FROM track_points '
        f'WHERE {" AND ".join(move_where)} ORDER BY importance DESC LIMIT ?',
        [start, end, *bbox_params, move_budget],
    ).fetchall()

    # A full moving budget is the proxy for "more vertices existed than fit" —
    # same len==limit heuristic the time-bucketed version used. When stops alone
    # fill the limit (budget 0) every moving vertex was dropped, which this still
    # reports as truncated rather than silently hiding the loss.
    truncated = len(moving) == move_budget

    points = [dict(r) for r in stops] + [dict(r) for r in moving]
    points.sort(key=lambda p: p['timestamp'])
    return jsonify({'points': points, 'count': len(points), 'truncated': truncated})
