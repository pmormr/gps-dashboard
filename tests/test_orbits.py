"""Tests for the inertial-frame orbit fit and propagation (common.orbits).

Builds known circular orbits, samples them to ECEF positions (what /globe
reconstruction yields), and checks that fit_orbit recovers an orbit which
reproduces the truth positions and az/el — including across multi-day,
multi-pass windows where the ECEF plane would be smeared by Earth rotation.
"""

import math
from dataclasses import replace

import pytest

from common.orbits import (
    GM_M3_S2,
    Orbit,
    azel_at,
    fit_orbit,
    plane_basis,
    position_ecef,
)
from common.satgeo import ecef_to_azel, observer_ecef, orbital_radius_m

_EPOCH = 1_700_000_000.0  # arbitrary recent Unix second


def _make_orbit(gnssid: int, inc_deg: float, raan_deg: float, phase0_deg: float) -> Orbit:
    """A circular orbit at the constellation's nominal radius and mean motion."""
    radius = orbital_radius_m(gnssid)
    assert radius is not None
    n = math.sqrt(GM_M3_S2 / radius**3)
    i, raan = math.radians(inc_deg), math.radians(raan_deg)
    normal = (math.sin(raan) * math.sin(i), -math.cos(raan) * math.sin(i), math.cos(i))
    u, v = plane_basis(normal)
    return Orbit(_EPOCH, radius, n, math.radians(phase0_deg), u, v, normal)


def _samples(orbit: Orbit, times: list[float]) -> list[tuple[float, tuple[float, float, float]]]:
    """Sample an orbit to ``(unix_s, ecef_m)`` pairs."""
    return [(t, position_ecef(orbit, t)) for t in times]


def _pass_times(center: float, span_s: float = 3600.0, step_s: float = 60.0) -> list[float]:
    """Evenly spaced sample times over a window ending at ``center``."""
    k = int(span_s / step_s)
    return [center - span_s + i * step_s for i in range(k + 1)]


def _close_positions(a: Orbit, b: Orbit, t: float, abs_tol: float = 5.0) -> None:
    """Assert two orbits agree on ECEF position at time ``t`` (metres)."""
    pa, pb = position_ecef(a, t), position_ecef(b, t)
    for k in range(3):
        assert math.isclose(pa[k], pb[k], rel_tol=1e-5, abs_tol=abs_tol)


def test_fit_recovers_orbit_and_propagates() -> None:
    """A fit over one pass reproduces truth positions hours/days later."""
    truth = _make_orbit(0, 55.0, 120.0, 30.0)
    fit = fit_orbit(_samples(truth, _pass_times(_EPOCH)), gnssid=0)
    assert fit is not None
    assert math.isclose(fit.radius_m, truth.radius_m, rel_tol=1e-12)
    assert math.isclose(fit.n_rad_s, truth.n_rad_s, rel_tol=1e-12)
    for dt in (0.0, 600.0, 3600.0, 6 * 3600.0, 24 * 3600.0):
        _close_positions(truth, fit, _EPOCH + dt)


def test_fit_consistent_across_days() -> None:
    """Two short passes ~13h apart (Earth rotated far in ECEF) fit one orbit.

    This is the reason for fitting in ECI: in ECEF the two arcs lie in different
    planes, but in the inertial frame they share one plane and one phase line.
    """
    truth = _make_orbit(6, 64.8, 200.0, 10.0)  # GLONASS
    times = _pass_times(_EPOCH - 13 * 3600.0, span_s=1800.0) + _pass_times(_EPOCH, span_s=1800.0)
    fit = fit_orbit(_samples(truth, times), gnssid=6)
    assert fit is not None
    _close_positions(truth, fit, _EPOCH + 6 * 3600.0, abs_tol=20.0)


def test_azel_matches_truth() -> None:
    """Propagated az/el for an observer matches the truth orbit's az/el."""
    truth = _make_orbit(2, 56.0, 300.0, 80.0)  # Galileo
    lat, lon, alt = 39.32, -77.84, 200.0
    obs = observer_ecef(lat, lon, alt)
    fit = fit_orbit(_samples(truth, _pass_times(_EPOCH)), gnssid=2)
    assert fit is not None
    for dt in (0.0, 1800.0, 7200.0):
        t = _EPOCH + dt
        az, el, _ = azel_at(fit, t, lat, lon, obs)
        taz, tel, _ = ecef_to_azel(lat, lon, obs, position_ecef(truth, t))
        assert abs((az - taz + 180.0) % 360.0 - 180.0) < 1e-3
        assert math.isclose(el, tel, abs_tol=1e-3)


def test_rejects_wrong_orbit_class() -> None:
    """An SV moving at the wrong rate for its gnssid's nominal radius is rejected.

    Mimics a BeiDou GEO/IGSO carried under the MEO nominal radius: the traced arc
    is long enough to fix a plane, but the observed angular rate is far off
    nominal, so the nominal-radius assumption is untrustworthy.
    """
    truth = _make_orbit(0, 55.0, 120.0, 30.0)
    too_fast = replace(truth, n_rad_s=truth.n_rad_s * 2.0)
    fit = fit_orbit(_samples(too_fast, _pass_times(_EPOCH, span_s=7200.0)), gnssid=0)
    assert fit is None


def test_too_few_samples_returns_none() -> None:
    """Fewer than three samples cannot fix a plane."""
    truth = _make_orbit(0, 55.0, 120.0, 30.0)
    assert fit_orbit(_samples(truth, [_EPOCH, _EPOCH + 60.0]), gnssid=0) is None


def test_unmodelled_constellation_returns_none() -> None:
    """A gnssid with no nominal radius (IMES) cannot be fit."""
    truth = _make_orbit(0, 55.0, 120.0, 30.0)
    assert fit_orbit(_samples(truth, _pass_times(_EPOCH)), gnssid=4) is None


@pytest.mark.parametrize('gnssid', [0, 2, 3, 6])
def test_fit_round_trips_each_constellation(gnssid: int) -> None:
    """Each modelled MEO constellation fits and propagates back to truth."""
    truth = _make_orbit(gnssid, 55.0, 45.0, 0.0)
    fit = fit_orbit(_samples(truth, _pass_times(_EPOCH, span_s=5400.0)), gnssid=gnssid)
    assert fit is not None
    _close_positions(truth, fit, _EPOCH + 3 * 3600.0)
