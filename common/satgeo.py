"""Reconstruct a satellite's 3D position from a single ground station's az/el.

The logger records only *direction* to each satellite (azimuth/elevation from the
van), never *range*. A direction alone can't place a point in space. But GNSS
satellites orbit at known nominal altitudes, so the position is recoverable
geometrically: from the observer's ECEF position, shoot the az/el look-ray
outward and intersect it with the sphere of that constellation's orbital radius.
That intersection is the satellite's ECEF position.

The only approximation is the *nominal circular* radius — real orbits are mildly
eccentric and BeiDou/QZSS mix orbit classes — so reconstructed positions are
display-grade (sub-pixel error at whole-Earth zoom), not metrology. This is the
geometry behind the /globe constellation view; see plans/gnss-observatory-plan.md.

All lengths are metres in an Earth-centred, Earth-fixed (ECEF) frame: +X through
(lat 0, lon 0), +Y through (lat 0, lon 90°E), +Z through the north pole.
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]

# WGS84 ellipsoid (observer geodetic -> ECEF).
WGS84_A = 6378137.0  # semi-major axis, m
WGS84_F = 1.0 / 298.257223563  # flattening
_E2 = WGS84_F * (2.0 - WGS84_F)  # first eccentricity squared

# Mean Earth radius for the rendered sphere (the textured globe is a sphere, not
# the WGS84 ellipsoid; the ~21 km polar difference is invisible at scale).
EARTH_RADIUS_M = 6_371_000.0

# Nominal orbital radius (distance from Earth's centre), metres, keyed by gpsd
# gnssid. MEO constellations use the mean semi-major axis; SBAS is geostationary;
# QZSS is an IGSO/eccentric approximation (its true radius swings, so its dots are
# coarse). IMES (gnssid 4) is terrestrial and intentionally absent.
ORBITAL_RADIUS_M: dict[int, float] = {
    0: 26_560_000.0,  # GPS — altitude ~20,180 km
    1: 42_164_000.0,  # SBAS — geostationary
    2: 29_600_000.0,  # Galileo — altitude ~23,222 km
    3: 27_910_000.0,  # BeiDou MEO — ~21,530 km (IGSO/GEO BeiDou are misplaced)
    5: 42_164_000.0,  # QZSS — IGSO, geosync semi-major (eccentric; approximate)
    6: 25_510_000.0,  # GLONASS — altitude ~19,130 km
}


def orbital_radius_m(gnssid: int) -> float | None:
    """Nominal orbital radius for a constellation, or None if unmodelled.

    Args:
        gnssid: gpsd constellation id (0 GPS, 1 SBAS, 2 Galileo, 3 BeiDou,
            5 QZSS, 6 GLONASS).

    Returns:
        Earth-centre distance in metres, or None for an unmodelled gnssid
        (e.g. 4 IMES).
    """
    return ORBITAL_RADIUS_M.get(gnssid)


def observer_ecef(lat_deg: float, lon_deg: float, alt_m: float = 0.0) -> Vec3:
    """Convert a WGS84 geodetic position to ECEF metres.

    Args:
        lat_deg: Geodetic latitude, degrees.
        lon_deg: Longitude, degrees east.
        alt_m: Height above the ellipsoid, metres.

    Returns:
        The ``(x, y, z)`` ECEF position in metres.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    n = WGS84_A / math.sqrt(1.0 - _E2 * sin_lat * sin_lat)
    x = (n + alt_m) * math.cos(lat) * math.cos(lon)
    y = (n + alt_m) * math.cos(lat) * math.sin(lon)
    z = (n * (1.0 - _E2) + alt_m) * sin_lat
    return (x, y, z)


def look_direction_ecef(lat_deg: float, lon_deg: float, az_deg: float, el_deg: float) -> Vec3:
    """ECEF unit vector for a topocentric azimuth/elevation look direction.

    Builds the local East-North-Up basis at ``(lat, lon)`` and rotates the
    (az, el) direction into ECEF. Azimuth is clockwise from north.

    Args:
        lat_deg: Observer latitude, degrees.
        lon_deg: Observer longitude, degrees east.
        az_deg: Azimuth, degrees clockwise from north.
        el_deg: Elevation, degrees above the horizon.

    Returns:
        A unit ``(x, y, z)`` ECEF direction vector.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    az = math.radians(az_deg)
    el = math.radians(el_deg)
    e = math.cos(el) * math.sin(az)
    n = math.cos(el) * math.cos(az)
    u = math.sin(el)
    sl, cl = math.sin(lat), math.cos(lat)
    so, co = math.sin(lon), math.cos(lon)
    # ENU basis expressed in ECEF, then dir = e*East + n*North + u*Up.
    dx = -e * so - n * sl * co + u * cl * co
    dy = e * co - n * sl * so + u * cl * so
    dz = n * cl + u * sl
    return (dx, dy, dz)


def ray_sphere_far(origin: Vec3, direction: Vec3, radius_m: float) -> Vec3 | None:
    """Far intersection of a ray with an origin-centred sphere.

    Solves ``|origin + t*direction|^2 = radius^2`` for the larger positive root
    (the satellite is the far hit, above the observer). ``direction`` is assumed
    unit length.

    Args:
        origin: Ray origin (observer ECEF), metres.
        direction: Unit ray direction in ECEF.
        radius_m: Sphere radius from Earth's centre, metres.

    Returns:
        The ``(x, y, z)`` intersection in metres, or None when the ray misses
        the sphere or only hits behind the observer.
    """
    px, py, pz = origin
    dx, dy, dz = direction
    pdotd = px * dx + py * dy + pz * dz
    p2 = px * px + py * py + pz * pz
    disc = pdotd * pdotd - (p2 - radius_m * radius_m)
    if disc < 0.0:
        return None
    t = -pdotd + math.sqrt(disc)
    if t <= 0.0:
        return None
    return (px + t * dx, py + t * dy, pz + t * dz)


def reconstruct(
    lat_deg: float, lon_deg: float, alt_m: float, az_deg: float, el_deg: float, gnssid: int
) -> Vec3 | None:
    """Reconstruct a satellite's ECEF position from an observer fix and az/el.

    Args:
        lat_deg: Observer latitude, degrees.
        lon_deg: Observer longitude, degrees east.
        alt_m: Observer height above the ellipsoid, metres.
        az_deg: Satellite azimuth, degrees clockwise from north.
        el_deg: Satellite elevation, degrees above the horizon.
        gnssid: Constellation id, used to look up the orbital radius.

    Returns:
        The satellite's ``(x, y, z)`` ECEF position in metres, or None when the
        constellation is unmodelled or the geometry has no valid intersection.
    """
    radius = orbital_radius_m(gnssid)
    if radius is None:
        return None
    origin = observer_ecef(lat_deg, lon_deg, alt_m)
    direction = look_direction_ecef(lat_deg, lon_deg, az_deg, el_deg)
    return ray_sphere_far(origin, direction, radius)


def fit_orbit_normal(
    points: list[Vec3], min_arc_deg: float = 12.0, max_step_deg: float = 60.0
) -> Vec3 | None:
    """Estimate the orbital-plane normal from time-ordered ECEF positions.

    A Keplerian orbit lies in a plane through Earth's centre, so its normal is the
    angular-momentum direction. Summing the cross products of consecutive in-pass
    points accumulates that direction, weighted by angular spacing. Steps larger
    than ``max_step_deg`` are treated as between-pass gaps (the satellite was
    below the horizon for most of an orbit) and skipped, so a multi-pass window
    stays sign-consistent. Returns None when too few points or too short a traced
    arc leave the plane underdetermined.

    Args:
        points: Time-ordered ECEF positions on the orbit (any consistent unit;
            the result is a unit vector regardless of scale).
        min_arc_deg: Minimum total in-pass arc that must be traced to trust the
            fit, in degrees.
        max_step_deg: Consecutive steps wider than this are skipped as gaps.

    Returns:
        A unit normal ``(x, y, z)`` to the orbital plane, or None when the fit is
        underdetermined.
    """
    if len(points) < 3:
        return None
    sx = sy = sz = 0.0
    arc = 0.0
    max_step = math.radians(max_step_deg)
    for a, b in zip(points, points[1:], strict=False):
        cx = a[1] * b[2] - a[2] * b[1]
        cy = a[2] * b[0] - a[0] * b[2]
        cz = a[0] * b[1] - a[1] * b[0]
        cmag = math.sqrt(cx * cx + cy * cy + cz * cz)
        dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
        if math.atan2(cmag, dot) > max_step:
            continue
        arc += math.atan2(cmag, dot)
        sx += cx
        sy += cy
        sz += cz
    if arc < math.radians(min_arc_deg):
        return None
    mag = math.sqrt(sx * sx + sy * sy + sz * sz)
    if mag < 1e-9:
        return None
    return (sx / mag, sy / mag, sz / mag)
