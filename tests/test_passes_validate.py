"""Tests for the pure helpers in the pass-prediction backtest tool."""

import math

from tools.passes_validate import _percentile, angular_separation_deg


def test_angular_separation_identical_is_zero() -> None:
    """A direction is zero degrees from itself."""
    assert math.isclose(angular_separation_deg(123.0, 45.0, 123.0, 45.0), 0.0, abs_tol=1e-9)


def test_angular_separation_elevation_gap() -> None:
    """Same azimuth, elevations 20 vs 50, separated by 30 degrees."""
    assert math.isclose(angular_separation_deg(90.0, 20.0, 90.0, 50.0), 30.0, abs_tol=1e-6)


def test_angular_separation_opposite_horizon() -> None:
    """North horizon vs south horizon are 180 degrees apart."""
    assert math.isclose(angular_separation_deg(0.0, 0.0, 180.0, 0.0), 180.0, abs_tol=1e-6)


def test_angular_separation_azimuth_at_zenith_is_small() -> None:
    """Near zenith a large azimuth gap is a small on-sky angle (no blow-up)."""
    sep = angular_separation_deg(10.0, 89.0, 200.0, 89.0)
    assert sep < 2.5


def test_percentile_bounds_and_median() -> None:
    """Percentile picks sane order statistics."""
    values = [5.0, 1.0, 4.0, 2.0, 3.0]
    assert _percentile(values, 0.0) == 1.0
    assert _percentile(values, 1.0) == 5.0
    assert _percentile(values, 0.5) == 3.0
