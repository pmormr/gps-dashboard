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
