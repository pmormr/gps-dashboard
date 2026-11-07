"""Great-circle geodesy + the population tier shared by the places reads.

Spherical formulas on a mean Earth radius — metre-level error over the
tens-of-kilometre distances the Drive compass works at, which is all the
tier needs (nothing here feeds navigation). The TypeScript twin for the
frontend is ``web/src/lib/geo.ts``; keep the two in step.
"""

from __future__ import annotations

import math

EARTH_RADIUS_M = 6_371_000.0

#: Population tier bounds: ``floor(log10(population)) - 1`` clamped to 1–5, so
#: tier 1 is under 1k, 2 is 1k–10k, 3 is 10k–100k, 4 is 100k–1M, 5 is 1M+.
#: One number drives the compass's visibility radius, slot quota, label
#: weight and pip glyph (plans/drive-hud-plan.md D5/D7).
POPULATION_TIER_MIN = 1
POPULATION_TIER_MAX = 5


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS-84 points.

    Args:
        lat1: First point's latitude, degrees.
        lon1: First point's longitude, degrees.
        lat2: Second point's latitude, degrees.
        lon2: Second point's longitude, degrees.

    Returns:
        The distance in metres.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from the first point to the second.

    Args:
        lat1: Origin latitude, degrees.
        lon1: Origin longitude, degrees.
        lat2: Target latitude, degrees.
        lon2: Target longitude, degrees.

    Returns:
        The bearing in degrees true, normalised to ``[0, 360)``.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    return math.degrees(math.atan2(y, x)) % 360.0


def population_tier(population: int) -> int:
    """The ×10 population band a settlement falls in, clamped to 1–5.

    Args:
        population: Resident count (non-positive counts read as tier 1).

    Returns:
        ``floor(log10(population)) - 1`` clamped to
        ``[POPULATION_TIER_MIN, POPULATION_TIER_MAX]``.
    """
    if population < 10:
        return POPULATION_TIER_MIN
    tier = int(math.floor(math.log10(population))) - 1
    return max(POPULATION_TIER_MIN, min(POPULATION_TIER_MAX, tier))
