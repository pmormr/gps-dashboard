"""Tests for the weather-pipeline Web-Mercator + slippy-tile geometry.

The CONUS tile-range assertion is the load-bearing one: it pins the exact grid
the P0 spike verified against the live service (master 11008×5888, 2 exports),
so a regression in the projection math surfaces here rather than as a silently
misaligned mosaic.
"""

import pytest

from weather import geo
from weather.registry import RADAR


def test_merc_roundtrip_lon():
    x, _ = geo.lonlat_to_merc(-105.0, 40.0)
    assert geo.merc_to_lon(x) == pytest.approx(-105.0)


def test_merc_roundtrip_lat():
    _, y = geo.lonlat_to_merc(-105.0, 40.0)
    assert geo.merc_to_lat(y) == pytest.approx(40.0)


def test_origin_is_half_world_span():
    assert geo.ORIGIN == pytest.approx(20037508.342789244)


def test_conus_tile_range_at_z8_matches_spike():
    # P0: CONUS bbox snapped to z8 -> x[38..80] y[87..109] (43×23 tiles).
    x0, y0, x1, y1 = geo.tile_range(8, RADAR.lon_bounds, RADAR.lat_bounds)
    assert (x0, y0, x1, y1) == (38, 87, 80, 109)


def test_master_pixel_dims_from_z8_range():
    x0, y0, x1, y1 = geo.tile_range(8, RADAR.lon_bounds, RADAR.lat_bounds)
    assert ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256) == (11008, 5888)


def test_tile_merc_bounds_are_contiguous():
    # Tile (z,x)'s east edge is tile (z,x+1)'s west edge.
    _, _, xmax, _ = geo.tile_merc_bounds(8, 38, 87)
    xmin_next, _, _, _ = geo.tile_merc_bounds(8, 39, 87)
    assert xmax == pytest.approx(xmin_next)


def test_tile_merc_bounds_span_one_tile():
    xmin, ymin, xmax, ymax = geo.tile_merc_bounds(8, 38, 87)
    expected = 2 * geo.ORIGIN / 2**8
    assert xmax - xmin == pytest.approx(expected)
    assert ymax - ymin == pytest.approx(expected)


def test_world_tile_covers_full_span():
    xmin, ymin, xmax, ymax = geo.tile_merc_bounds(0, 0, 0)
    assert xmin == pytest.approx(-geo.ORIGIN)
    assert xmax == pytest.approx(geo.ORIGIN)
    assert ymax == pytest.approx(geo.ORIGIN)
    assert ymin == pytest.approx(-geo.ORIGIN)


def test_native_pixel_size_near_z8():
    # 564.77 m/px native (P0) sits just under the z8 tile pixel size (611 m/px),
    # so z8 is the natural max slice zoom (downsample, never upsample).
    z8_m_per_px = 2 * geo.ORIGIN / 2**8 / 256
    assert z8_m_per_px == pytest.approx(611.496, abs=0.01)
    assert 564.77 < z8_m_per_px
