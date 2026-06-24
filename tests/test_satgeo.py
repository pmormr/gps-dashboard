"""Tests for the az/el -> 3D satellite reconstruction geometry."""

import math

import pytest

from common.satgeo import (
    WGS84_A,
    observer_ecef,
    orbital_radius_m,
    ray_sphere_far,
    reconstruct,
)


def _norm(v: tuple[float, float, float]) -> float:
    """Euclidean length of a 3-vector."""
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def test_observer_ecef_equator_prime_meridian() -> None:
    """(0,0) sits on the +X axis at the semi-major radius."""
    x, y, z = observer_ecef(0.0, 0.0, 0.0)
    assert math.isclose(x, WGS84_A, rel_tol=1e-12)
    assert abs(y) < 1e-6
    assert abs(z) < 1e-6


def test_observer_ecef_longitude_90_is_y_axis() -> None:
    """(0, 90E) sits on the +Y axis."""
    x, y, z = observer_ecef(0.0, 90.0, 0.0)
    assert abs(x) < 1e-6
    assert math.isclose(y, WGS84_A, rel_tol=1e-12)
    assert abs(z) < 1e-6


def test_observer_ecef_north_pole_uses_semi_minor() -> None:
    """The pole's Z is the semi-minor axis (a*sqrt(1-e^2)), below WGS84_A."""
    x, y, z = observer_ecef(90.0, 0.0, 0.0)
    assert abs(x) < 1e-6 and abs(y) < 1e-6
    assert math.isclose(z, 6_356_752.314, rel_tol=1e-6)
    assert z < WGS84_A


def test_altitude_adds_radially() -> None:
    """1 km of height at (0,0) adds 1 km along +X."""
    base = observer_ecef(0.0, 0.0, 0.0)
    raised = observer_ecef(0.0, 0.0, 1000.0)
    assert math.isclose(raised[0] - base[0], 1000.0, rel_tol=1e-9)


def test_zenith_satellite_is_straight_up() -> None:
    """An el=90 GPS sat from (0,0) lands on +X at the GPS orbital radius."""
    pos = reconstruct(0.0, 0.0, 0.0, az_deg=137.0, el_deg=90.0, gnssid=0)
    assert pos is not None
    radius = orbital_radius_m(0)
    assert radius is not None
    assert math.isclose(pos[0], radius, rel_tol=1e-9)
    assert abs(pos[1]) < 1.0 and abs(pos[2]) < 1.0


def test_horizon_north_satellite() -> None:
    """An el=0, az=0 sat from (0,0) lies in the X-Z plane toward +Z (north)."""
    pos = reconstruct(0.0, 0.0, 0.0, az_deg=0.0, el_deg=0.0, gnssid=0)
    assert pos is not None
    assert math.isclose(pos[0], WGS84_A, rel_tol=1e-6)
    assert abs(pos[1]) < 1.0
    assert pos[2] > 0.0  # toward the north pole


@pytest.mark.parametrize('lat', [-45.0, 0.0, 39.7, 70.0])
@pytest.mark.parametrize('lon', [-105.0, 0.0, 120.0])
@pytest.mark.parametrize('az,el', [(0.0, 5.0), (90.0, 45.0), (215.0, 80.0)])
@pytest.mark.parametrize('gnssid', [0, 2, 3, 6])
def test_reconstructed_radius_matches_orbit(
    lat: float, lon: float, az: float, el: float, gnssid: int
) -> None:
    """However placed, the reconstructed sat sits on the orbital sphere."""
    pos = reconstruct(lat, lon, 0.0, az, el, gnssid)
    assert pos is not None
    radius = orbital_radius_m(gnssid)
    assert radius is not None
    assert math.isclose(_norm(pos), radius, rel_tol=1e-9)


def test_unmodelled_constellation_returns_none() -> None:
    """IMES (gnssid 4) has no orbital radius, so reconstruction is None."""
    assert orbital_radius_m(4) is None
    assert reconstruct(0.0, 0.0, 0.0, 100.0, 30.0, 4) is None


def test_ray_that_misses_sphere_returns_none() -> None:
    """A ray whose line stays outside the sphere has no intersection."""
    # Origin far out on +X, aimed along +Y: closest approach is the origin's
    # |X|, well outside a small sphere.
    assert ray_sphere_far((1.0e8, 0.0, 0.0), (0.0, 1.0, 0.0), 1.0e6) is None
