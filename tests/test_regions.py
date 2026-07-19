"""Tests for the shared region/bbox helpers used by the tile tools."""

from __future__ import annotations

import pytest

from tools.regions import REGIONS, parse_bbox


def test_parse_bbox_four_floats():
    assert parse_bbox('-109.06,36.99,-102.04,41.00') == (-109.06, 36.99, -102.04, 41.00)


def test_parse_bbox_wrong_count_raises():
    with pytest.raises(ValueError):
        parse_bbox('1,2,3')


def test_parse_bbox_non_float_raises():
    with pytest.raises(ValueError):
        parse_bbox('a,b,c,d')


def test_region_bbox_property_matches_parse():
    # A REGIONS entry's bbox tuple round-trips through the string parser.
    r = REGIONS['colorado']
    assert parse_bbox(','.join(str(v) for v in r.bbox)) == r.bbox
