"""Unit tests for ``common.geo`` — the places reads' great-circle helpers + tier."""

from __future__ import annotations

import pytest

from common.geo import haversine_m, initial_bearing_deg, population_tier


def test_haversine_known_distance() -> None:
    # Denver → Leadville ≈ 124 km great-circle.
    d = haversine_m(39.7392, -104.9903, 39.2508, -106.2925)
    assert d == pytest.approx(124200, rel=0.01)
    assert haversine_m(39.0, -106.0, 39.0, -106.0) == 0.0


def test_initial_bearing_cardinals() -> None:
    assert initial_bearing_deg(39.0, -106.0, 40.0, -106.0) == pytest.approx(0.0)
    assert initial_bearing_deg(39.0, -106.0, 39.0, -105.0) == pytest.approx(90.0, abs=0.5)
    assert initial_bearing_deg(39.0, -106.0, 38.0, -106.0) == pytest.approx(180.0)
    assert initial_bearing_deg(39.0, -106.0, 39.0, -107.0) == pytest.approx(270.0, abs=0.5)


@pytest.mark.parametrize(
    ('population', 'tier'),
    [
        (0, 1),
        (500, 1),
        (999, 1),
        (1_000, 2),
        (9_999, 2),
        (10_000, 3),
        (99_999, 3),
        (100_000, 4),
        (715_522, 4),
        (1_000_000, 5),
        (8_800_000, 5),
    ],
)
def test_population_tier_bands(population: int, tier: int) -> None:
    assert population_tier(population) == tier
