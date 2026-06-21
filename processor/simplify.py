"""Shared track geometry + Reumann–Witkam line simplification.

The pure-geometry core the GPS processor's online denoise filter and the drone
importer both build on. Splitting it out keeps one implementation of the
perpendicular-deviation metric: the processor consumes it in a causal,
fix-at-a-time state machine; the importer runs the batch :func:`reumann_witkam`
over a whole flight's track so drone tracks decimate like van tracks.

No DB, no I/O, no wall-clock — just planar geometry on (lat, lon) degrees.
"""

import math

EARTH_RADIUS_M = 6_371_000.0


def local_meters(lat: float, lon: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """Project a point to local east/north metres about a reference point.

    Equirectangular approximation — accurate to well under a metre at the
    sub-kilometre scales these comparisons use (deviations, dwell radii).

    Args:
        lat: Point latitude (deg).
        lon: Point longitude (deg).
        ref_lat: Reference latitude (deg).
        ref_lon: Reference longitude (deg).

    Returns:
        ``(east, north)`` offset in metres from the reference.
    """
    east = math.radians(lon - ref_lon) * EARTH_RADIUS_M * math.cos(math.radians(ref_lat))
    north = math.radians(lat - ref_lat) * EARTH_RADIUS_M
    return east, north


def perp_distance_m(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Perpendicular distance (m) from point ``p`` to the infinite line ``a``–``b``.

    The Reumann–Witkam deviation metric. When ``a`` and ``b`` coincide, falls
    back to the distance to ``a``.

    Args:
        p: The test point ``(lat, lon)``.
        a: A point on the line ``(lat, lon)`` — the anchor.
        b: A second point on the line ``(lat, lon)`` — the direction reference.

    Returns:
        Perpendicular distance in metres.
    """
    pe, pn = local_meters(p[0], p[1], a[0], a[1])
    be, bn = local_meters(b[0], b[1], a[0], a[1])
    seg = math.hypot(be, bn)
    if seg == 0.0:
        return math.hypot(pe, pn)
    return abs(pe * bn - pn * be) / seg


def reumann_witkam(coords: list[tuple[float, float]], epsilon: float) -> list[tuple[int, float]]:
    """Batch Reumann–Witkam simplification of a (lat, lon) track.

    Walks the track keeping an anchor and a direction reference; points within
    ``epsilon`` of the anchor→reference line are dropped. When a point deviates
    past ``epsilon`` the last in-tolerance point is emitted as a vertex and a new
    segment opens at it (anchor = that vertex, reference = the breaking point) —
    the same break-and-emit rule the processor's online filter applies per fix,
    minus the live keep-alive gap. The first and last points are always kept.

    Args:
        coords: The track as ``(lat, lon)`` pairs, in track order.
        epsilon: Perpendicular-deviation tolerance in metres.

    Returns:
        ``(index, importance)`` for each kept vertex, in track order. ``index``
        is into ``coords``; ``importance`` is the deviation (m) that forced the
        vertex out — the decimation priority. Endpoints carry ``0.0``.
    """
    n = len(coords)
    if n <= 2:
        return [(i, 0.0) for i in range(n)]

    kept: list[tuple[int, float]] = [(0, 0.0)]
    anchor = coords[0]
    ref = coords[1]
    pending = 1
    for i in range(2, n):
        dev = perp_distance_m(coords[i], anchor, ref)
        if dev > epsilon:
            kept.append((pending, dev))
            anchor = coords[pending]
            ref = coords[i]
            pending = i
        else:
            pending = i
    kept.append((n - 1, 0.0))
    return kept
