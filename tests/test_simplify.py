"""Tests for the shared track geometry + Reumann–Witkam simplification.

The geometry helpers get concrete numeric checks (the math is clean at the
equator); ``reumann_witkam`` is pinned by the invariants its docstring promises
— endpoints always kept, monotonic indices, every interior vertex's importance
above epsilon, and monotonicity in epsilon — rather than a hand-traced output,
which the emit-the-pending rule makes brittle.
"""

import math

import pytest

from processor.simplify import EARTH_RADIUS_M, local_meters, perp_distance_m, reumann_witkam


def test_local_meters_reference_is_origin():
    assert local_meters(10.0, 20.0, 10.0, 20.0) == (0.0, 0.0)


def test_local_meters_north_offset_at_equator():
    _, north = local_meters(0.001, 0.0, 0.0, 0.0)
    assert north == pytest.approx(math.radians(0.001) * EARTH_RADIUS_M)


def test_local_meters_east_scaled_by_cos_latitude():
    east, _ = local_meters(60.0, 0.001, 60.0, 0.0)
    expected = math.radians(0.001) * EARTH_RADIUS_M * math.cos(math.radians(60.0))
    assert east == pytest.approx(expected)


def test_perp_distance_point_on_line_is_zero():
    # All three points share latitude 0, so p lies on the infinite a-b line.
    assert perp_distance_m((0.0, 0.002), (0.0, 0.0), (0.0, 0.001)) == pytest.approx(0.0)


def test_perp_distance_equals_east_offset():
    # Line runs due north (lon fixed); p is offset purely east by 0.001 deg.
    a, b, p = (0.0, 0.0), (0.001, 0.0), (0.0005, 0.001)
    expected = math.radians(0.001) * EARTH_RADIUS_M
    assert perp_distance_m(p, a, b) == pytest.approx(expected)


def test_perp_distance_degenerate_segment_falls_back_to_anchor():
    # a == b: distance collapses to the straight-line distance to the anchor.
    a = b = (0.0, 0.0)
    p = (0.001, 0.0)
    assert perp_distance_m(p, a, b) == pytest.approx(math.radians(0.001) * EARTH_RADIUS_M)


def test_reumann_witkam_passthrough_for_short_tracks():
    assert reumann_witkam([], 10.0) == []
    assert reumann_witkam([(0.0, 0.0)], 10.0) == [(0, 0.0)]
    assert reumann_witkam([(0.0, 0.0), (0.0, 0.001)], 10.0) == [(0, 0.0), (1, 0.0)]


def test_reumann_witkam_drops_collinear_interior():
    coords = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002), (0.0, 0.003)]
    assert reumann_witkam(coords, 10.0) == [(0, 0.0), (3, 0.0)]


def test_reumann_witkam_keeps_endpoints_with_zero_importance():
    coords = [(0.0, 0.0), (0.002, 0.001), (0.0, 0.002), (0.002, 0.003), (0.0, 0.004)]
    kept = reumann_witkam(coords, 50.0)
    assert kept[0] == (0, 0.0)
    assert kept[-1] == (len(coords) - 1, 0.0)


def test_reumann_witkam_indices_strictly_increasing():
    coords = [(0.0, 0.0), (0.002, 0.001), (0.0, 0.002), (0.002, 0.003), (0.0, 0.004)]
    indices = [i for i, _ in reumann_witkam(coords, 50.0)]
    assert indices == sorted(indices)
    assert len(set(indices)) == len(indices)


def test_reumann_witkam_interior_importance_exceeds_epsilon():
    coords = [(0.0, 0.0), (0.002, 0.001), (0.0, 0.002), (0.002, 0.003), (0.0, 0.004)]
    epsilon = 50.0
    kept = reumann_witkam(coords, epsilon)
    interior = kept[1:-1]
    assert interior  # the zig-zag must retain at least one interior vertex
    assert all(importance > epsilon for _, importance in interior)


def test_reumann_witkam_larger_epsilon_keeps_no_more_points():
    coords = [(0.0, 0.0), (0.002, 0.001), (0.0, 0.002), (0.002, 0.003), (0.0, 0.004)]
    assert len(reumann_witkam(coords, 500.0)) <= len(reumann_witkam(coords, 50.0))


def test_reumann_witkam_retains_a_sharp_corner():
    # Three points due east, then a 90° turn due north: the corner must survive.
    coords = [(0.0, 0.0), (0.0, 0.001), (0.0, 0.002), (0.001, 0.002), (0.002, 0.002)]
    indices = [i for i, _ in reumann_witkam(coords, 10.0)]
    assert 2 in indices
    assert len(indices) > 2
