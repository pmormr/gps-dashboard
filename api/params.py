"""Shared request-parameter parsing and validation for the JSON API.

Collapses the bbox/timestamp/limit validation that was duplicated inline across
the points, drone, annotations, and sensors routes. Each helper returns a
``(value, error)`` pair: on success ``error`` is ``None``; on failure ``value``
is ``None`` and ``error`` is a Flask ``(response, status)`` tuple a handler can
return directly. This mirrors the convention the routes already used by hand
(e.g. annotations' old ``_canonicalize``), so call sites stay shape-for-shape.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import Response, jsonify
from werkzeug.datastructures import MultiDict

from api.db import canonical_timestamp, now_canonical

Bbox = tuple[float, float, float, float]
ErrorResponse = tuple[Response, int]
TimeWindow = tuple[str | None, str | None, ErrorResponse | None]


def _error(message: str, status: int = 400) -> ErrorResponse:
    """Build a Flask ``(json, status)`` error response.

    Args:
        message: Human-readable error, surfaced as ``{'error': message}``.
        status: HTTP status code.

    Returns:
        A Flask ``(response, status)`` tuple.
    """
    return jsonify({'error': message}), status


def parse_time(value: str, name: str) -> tuple[str | None, ErrorResponse | None]:
    """Validate an ISO-8601 timestamp and normalize it to canonical storage form.

    Every range-compared timestamp column stores fixed-width millisecond UTC
    (see :func:`api.db.canonical_timestamp`), so the canonical form is what the
    SQL comparisons need — not the raw client string.

    Args:
        value: The incoming timestamp string.
        name: Field name, used in the error message.

    Returns:
        ``(canonical, None)`` on success, or ``(None, error_response)``.
    """
    try:
        return canonical_timestamp(value), None
    except (ValueError, TypeError, AttributeError):
        return None, _error(f"Invalid timestamp for '{name}': {value}")


def parse_required_window(args: MultiDict[str, str]) -> TimeWindow:
    """Parse mandatory ``start`` and ``end`` timestamps into canonical form.

    Both params are required; a missing or malformed value yields an error the
    handler returns directly. Used by the history/economy reads that have no
    sensible default window.

    Args:
        args: The request args mapping (``request.args``).

    Returns:
        ``(start, end, None)`` on success (both canonical ms-UTC), or
        ``(None, None, error_response)``.
    """
    raw_start = args.get('start')
    raw_end = args.get('end')
    if not raw_start or not raw_end:
        return None, None, _error("'start' and 'end' query params are required")
    start, err = parse_time(raw_start, 'start')
    if err:
        return None, None, err
    end, err = parse_time(raw_end, 'end')
    if err:
        return None, None, err
    return start, end, None


def parse_time_window(args: MultiDict[str, str], default_hours: float) -> TimeWindow:
    """Parse an optional ``start``/``end`` window, defaulting to a trailing span.

    ``end`` defaults to now; ``start`` defaults to now minus ``default_hours``
    (a fixed trailing window, independent of an explicit ``end``). Each provided
    value is validated and normalized to canonical ms-UTC.

    Args:
        args: The request args mapping (``request.args``).
        default_hours: Width of the default trailing window when ``start`` is
            absent.

    Returns:
        ``(start, end, None)`` on success (both canonical ms-UTC), or
        ``(None, None, error_response)``.
    """
    raw_end = args.get('end')
    if raw_end:
        end, err = parse_time(raw_end, 'end')
        if err:
            return None, None, err
    else:
        end = now_canonical()
    raw_start = args.get('start')
    if raw_start:
        start, err = parse_time(raw_start, 'start')
        if err:
            return None, None, err
    else:
        window_start = datetime.now(UTC) - timedelta(hours=default_hours)
        start = canonical_timestamp(window_start.isoformat())
    return start, end, None


def parse_bbox(args: MultiDict[str, str]) -> tuple[Bbox | None, ErrorResponse | None]:
    """Parse an optional ``bbox=W,S,E,N`` query param.

    A missing ``bbox`` is not an error — it yields ``(None, None)`` so callers can
    treat "no spatial filter" uniformly.

    Args:
        args: The request args mapping (``request.args``).

    Returns:
        ``(bbox, None)`` where ``bbox`` is a ``(w, s, e, n)`` tuple or ``None``
        when unset, or ``(None, error_response)`` when malformed.
    """
    bbox_str = args.get('bbox')
    if bbox_str is None:
        return None, None
    parts = bbox_str.split(',')
    if len(parts) != 4:
        return None, _error("'bbox' must be 'W,S,E,N' (4 comma-separated floats)")
    try:
        w, s, e, n = (float(p) for p in parts)
    except ValueError:
        return None, _error("'bbox' must be 4 floats")
    if w > e or s > n:
        return None, _error("'bbox' must have W<=E and S<=N")
    return (w, s, e, n), None


def bbox_point_where(bbox: Bbox, prefix: str = '') -> tuple[list[str], list[float]]:
    """Build a point-in-box SQL filter for a lat/lon pair.

    The parameter order (``[s, n, w, e]``) is the transposition footgun this helper
    exists to remove — ``parse_bbox`` yields ``(w, s, e, n)`` but the ``BETWEEN``
    clauses need latitude first.

    Args:
        bbox: A ``(w, s, e, n)`` tuple (as returned by :func:`parse_bbox`).
        prefix: Optional column prefix / table alias, e.g. ``'p.'`` for ``p.lat``.

    Returns:
        ``(clauses, params)`` — WHERE fragments for the caller to AND-combine, with
        their bound parameters.
    """
    w, s, e, n = bbox
    clauses = [f'{prefix}lat BETWEEN ? AND ?', f'{prefix}lon BETWEEN ? AND ?']
    return clauses, [s, n, w, e]


def bbox_overlap_where(bbox: Bbox) -> tuple[list[str], list[float]]:
    """Build a box-overlap SQL filter for a row carrying min/max bounds.

    Matches rows whose ``[min_lon,max_lon]×[min_lat,max_lat]`` extent overlaps the
    query box — used by reads over rows with a precomputed bounding box (drone
    flights, phone paths).

    Args:
        bbox: A ``(w, s, e, n)`` tuple (as returned by :func:`parse_bbox`).

    Returns:
        ``(clauses, params)`` for the caller to AND-combine.
    """
    w, s, e, n = bbox
    clauses = ['max_lon >= ?', 'min_lon <= ?', 'max_lat >= ?', 'min_lat <= ?']
    return clauses, [w, e, s, n]


def time_overlap_where(
    args: MultiDict[str, str], start_col: str, end_col: str
) -> tuple[list[str], list[str], ErrorResponse | None]:
    """Build an interval-overlap SQL clause for a ``[start_col, end_col]`` row.

    A row's interval overlaps the query window ``[start, end]`` when
    ``end_col >= start`` and ``start_col <= end`` — each bound optional. Used by
    reads over rows that span a time range (phone paths/visits/activities, drone
    flights).

    Args:
        args: The request args mapping (``request.args``).
        start_col: The row column holding the interval start.
        end_col: The row column holding the interval end.

    Returns:
        ``(where, params, None)`` on success, or ``([], [], error_response)`` when
        a provided timestamp is malformed.
    """
    where: list[str] = []
    params: list[str] = []
    for value, name, column, op in (
        (args.get('start'), 'start', end_col, '>='),
        (args.get('end'), 'end', start_col, '<='),
    ):
        if value is None:
            continue
        canonical, err = parse_time(value, name)
        if err:
            return [], [], err
        assert canonical is not None  # err is None ⇒ canonical parsed
        where.append(f'{column} {op} ?')
        params.append(canonical)
    return where, params, None


def parse_limit(
    args: MultiDict[str, str], default: int, maximum: int, key: str = 'limit'
) -> tuple[int | None, ErrorResponse | None]:
    """Parse an optional positive integer count, clamped to ``maximum``.

    Args:
        args: The request args mapping (``request.args``).
        default: Value used when the param is absent.
        maximum: Upper bound the parsed value is clamped to.
        key: The query-param name (e.g. ``'limit'`` for row caps, ``'buckets'``
            for the trend-series resolution).

    Returns:
        ``(value, None)`` on success, or ``(None, error_response)``.
    """
    try:
        value = min(int(args.get(key, default)), maximum)
    except (ValueError, TypeError):
        return None, _error(f"'{key}' must be an integer")
    if value <= 0:
        return None, _error(f"'{key}' must be > 0")
    return value, None
