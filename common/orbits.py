"""Fit and propagate a GNSS satellite's orbit from reconstructed positions.

The /globe reconstruction (:mod:`common.satgeo`) turns each logged az/el into an
ECEF position on the constellation's nominal orbital sphere. Those positions are
Earth-fixed, but orbits are fixed in the *inertial* (ECI) frame — Earth rotates
underneath them. So the orbit is fit and propagated in ECI: convert each sample
to ECI (rotate by GMST), fit the plane there (it is genuinely constant across
passes and days, unlike the Earth-rotation-smeared ECEF plane the display rings
use), then model the in-plane angle as a linear function of time.

The priors are strong, so the fit is cheap and well-determined:

* **Size** — the semi-major axis is the constellation's nominal radius (fixed;
  not independently observable, since reconstruction *assumed* it), which fixes
  the mean motion ``n = sqrt(GM / a^3)``.
* **Plane** — the orbital-plane normal from :func:`common.satgeo.fit_orbit_normal`,
  now applied to the ECI track.
* **Phase** — the only free parameter: one epoch angle ``phase0``, fit as the
  circular mean of the in-plane angle minus ``n * dt``.

Propagating in ECI and rotating back to ECEF reproduces the sidereal-day repeat
of each ground track for free, and generalises to constellations whose tracks do
*not* repeat daily. First cut is two-body only (no J2 nodal precession) — good to
minutes over a 1-2 day horizon, which is the prediction window we care about. See
plans/gnss-observatory-plan.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from common.satgeo import (
    Vec3,
    ecef_to_azel,
    ecef_to_eci,
    eci_to_ecef,
    fit_orbit_normal,
    gmst_rad,
    orbital_radius_m,
)

GM_M3_S2 = 3.986004418e14  # Earth's gravitational parameter, m^3/s^2

# A between-pass gap (the SV below the horizon) spans hours; the logger samples
# every ~60s. So a consecutive pair separated by more than this is a gap, not an
# in-pass step, and is excluded from the observed-rate estimate. A pure angle
# threshold is not enough: a multi-hour gap can wrap to a small apparent arc.
_MAX_GAP_S = 900.0
# Backstop angle guard for the in-pass steps that survive the time gate.
_MAX_STEP_RAD = math.radians(60.0)

# Reject a fit whose observed mean motion departs this fraction from the
# constellation nominal — catches SVs in a different orbit class than the
# gnssid's nominal radius (BeiDou GEO/IGSO among MEO, eccentric QZSS).
_RATE_TOLERANCE = 0.15


@dataclass(frozen=True)
class Orbit:
    """A circular two-body orbit, expressed in the inertial (ECI) frame.

    The ECI position at time ``t`` is
    ``radius_m * (cos θ · u + sin θ · v)`` with
    ``θ = phase0_rad + n_rad_s * (t - epoch_unix)``; rotate by GMST(t) to get
    ECEF. ``(u, v, normal)`` is right-handed, so ``θ`` increases prograde.
    """

    epoch_unix: float
    radius_m: float
    n_rad_s: float
    phase0_rad: float
    u: Vec3
    v: Vec3
    normal: Vec3


def _dot(a: Vec3, b: Vec3) -> float:
    """Dot product of two 3-vectors."""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    """Cross product of two 3-vectors."""
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v: Vec3) -> Vec3:
    """Unit vector in the direction of ``v`` (``v`` assumed non-zero)."""
    m = math.sqrt(_dot(v, v))
    return (v[0] / m, v[1] / m, v[2] / m)


def plane_basis(normal: Vec3) -> tuple[Vec3, Vec3]:
    """Right-handed in-plane basis ``(u, v)`` for an orbital-plane normal.

    Picks a reference axis not parallel to the normal, so ``(u, v, normal)`` is
    right-handed and the in-plane angle measured from ``u`` increases prograde
    (counter-clockwise about the normal).

    Args:
        normal: Orbital-plane normal (any non-zero length).

    Returns:
        Unit vectors ``(u, v)`` spanning the plane.
    """
    n = _unit(normal)
    ref: Vec3 = (0.0, 0.0, 1.0) if abs(n[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = _unit(_cross(ref, n))
    v = _cross(n, u)
    return u, v


def fit_orbit(samples: list[tuple[float, Vec3]], gnssid: int) -> Orbit | None:
    """Fit a circular ECI orbit to time-tagged ECEF positions.

    Args:
        samples: ``(unix_s, ecef_m)`` reconstructed positions, time-ordered.
        gnssid: Constellation id; sets the nominal radius and hence mean motion.

    Returns:
        The fitted :class:`Orbit`, or None when the constellation is unmodelled,
        too little arc is traced to fix the plane, or the observed angular rate
        is inconsistent with the nominal radius (wrong orbit class for the
        gnssid).
    """
    radius = orbital_radius_m(gnssid)
    if radius is None or len(samples) < 3:
        return None
    n = math.sqrt(GM_M3_S2 / radius**3)

    eci = [(t, ecef_to_eci(p, gmst_rad(t))) for t, p in samples]
    normal = fit_orbit_normal([p for _, p in eci])
    if normal is None:
        return None
    u, v = plane_basis(normal)

    thetas = [(t, math.atan2(_dot(p, v), _dot(p, u))) for t, p in eci]

    # Observed mean motion from in-pass consecutive steps (gap-skipping), to
    # validate the nominal-radius assumption before trusting the propagation.
    dtheta_sum = 0.0
    dt_sum = 0.0
    for (t0, th0), (t1, th1) in zip(thetas, thetas[1:], strict=False):
        dt = t1 - t0
        if dt <= 0.0 or dt > _MAX_GAP_S:
            continue
        d = (th1 - th0 + math.pi) % (2.0 * math.pi) - math.pi
        if abs(d) > _MAX_STEP_RAD:
            continue
        dtheta_sum += d
        dt_sum += dt
    if dt_sum <= 0.0:
        return None
    observed_n = dtheta_sum / dt_sum
    if observed_n <= 0.0 or abs(observed_n - n) / n > _RATE_TOLERANCE:
        return None

    # Epoch at the latest sample; phase0 is the circular mean of θ - n·dt, so the
    # propagation extrapolates from the freshest data.
    epoch = thetas[-1][0]
    sin_sum = cos_sum = 0.0
    for t, th in thetas:
        a = th - n * (t - epoch)
        sin_sum += math.sin(a)
        cos_sum += math.cos(a)
    phase0 = math.atan2(sin_sum, cos_sum)

    return Orbit(
        epoch_unix=epoch,
        radius_m=radius,
        n_rad_s=n,
        phase0_rad=phase0,
        u=u,
        v=v,
        normal=_unit(normal),
    )


def position_eci(orbit: Orbit, unix_s: float) -> Vec3:
    """ECI position of the satellite at ``unix_s`` (metres)."""
    theta = orbit.phase0_rad + orbit.n_rad_s * (unix_s - orbit.epoch_unix)
    c, s = math.cos(theta), math.sin(theta)
    r = orbit.radius_m
    return (
        r * (c * orbit.u[0] + s * orbit.v[0]),
        r * (c * orbit.u[1] + s * orbit.v[1]),
        r * (c * orbit.u[2] + s * orbit.v[2]),
    )


def position_ecef(orbit: Orbit, unix_s: float) -> Vec3:
    """ECEF position of the satellite at ``unix_s`` (metres)."""
    return eci_to_ecef(position_eci(orbit, unix_s), gmst_rad(unix_s))


def azel_at(
    orbit: Orbit, unix_s: float, lat_deg: float, lon_deg: float, observer_m: Vec3
) -> tuple[float, float, float]:
    """Topocentric az/el/range of the propagated satellite for an observer.

    Args:
        orbit: The fitted orbit.
        unix_s: Evaluation time, Unix seconds.
        lat_deg: Observer latitude, degrees.
        lon_deg: Observer longitude, degrees east.
        observer_m: Observer ECEF position (metres); pass the cached value to
            avoid recomputing it per call across a pass scan.

    Returns:
        ``(az_deg, el_deg, range_m)`` as in :func:`common.satgeo.ecef_to_azel`.
    """
    return ecef_to_azel(lat_deg, lon_deg, observer_m, position_ecef(orbit, unix_s))
