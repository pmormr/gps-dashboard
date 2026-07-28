"""Tests for the master-grid derivation, strip split, and tile rendering.

Uses a synthetic master (a solid-color patch on a transparent canvas) so the
image geometry — abutting strips, intersection-and-paste, empty-tile skip — is
exercised without hitting the network.
"""

from PIL import Image

from weather import mosaic
from weather.registry import RADAR


def test_master_grid_dims():
    grid = mosaic.master_grid(RADAR)
    assert (grid.width, grid.height) == (11008, 5888)
    assert (grid.tile_x0, grid.tile_y0, grid.tile_x1, grid.tile_y1) == (38, 87, 80, 109)


def test_strip_specs_abut_and_cover():
    grid = mosaic.master_grid(RADAR)
    specs = mosaic.strip_specs(grid, RADAR.export_max_h)
    # 5888 / 4100 -> 2 strips
    assert len(specs) == 2
    # Pixel heights sum to the master height, no gap/overlap.
    assert sum(s.size[1] for s in specs) == grid.height
    assert specs[0].paste_y == 0
    assert specs[1].paste_y == specs[0].size[1]
    # Strip 0's south edge == strip 1's north edge (shared boundary merc-y).
    assert specs[0].bbox[1] == specs[1].bbox[3]
    # Full width, master x-bounds preserved on every strip.
    for s in specs:
        assert s.size[0] == grid.width
        assert (s.bbox[0], s.bbox[2]) == (grid.merc_bounds[0], grid.merc_bounds[2])


def test_single_strip_when_under_cap():
    grid = mosaic.master_grid(RADAR)
    specs = mosaic.strip_specs(grid, 100000)
    assert len(specs) == 1
    assert specs[0].size[1] == grid.height


def _synthetic_master(grid: mosaic.MasterGrid) -> Image.Image:
    """A master with an opaque red block in its center quarter, else transparent."""
    m = Image.new('RGBA', (grid.width, grid.height), (0, 0, 0, 0))
    w, h = grid.width, grid.height
    block = Image.new('RGBA', (w // 2, h // 2), (255, 0, 0, 255))
    m.paste(block, (w // 4, h // 4))
    return m


def test_render_tile_skips_transparent_corner():
    grid = mosaic.master_grid(RADAR)
    master = _synthetic_master(grid)
    # Top-left native tile of the master is in the transparent margin.
    assert mosaic.render_tile(master, grid, grid.zoom, grid.tile_x0, grid.tile_y0) is None


def test_render_tile_returns_data_in_block():
    grid = mosaic.master_grid(RADAR)
    master = _synthetic_master(grid)
    cx = (grid.tile_x0 + grid.tile_x1) // 2
    cy = (grid.tile_y0 + grid.tile_y1) // 2
    tile = mosaic.render_tile(master, grid, grid.zoom, cx, cy)
    assert tile is not None
    assert tile.size == (256, 256)
    assert tile.getchannel('A').getbbox() is not None


def test_render_tile_outside_master_is_none():
    grid = mosaic.master_grid(RADAR)
    master = _synthetic_master(grid)
    # A tile far west of the CONUS master.
    assert mosaic.render_tile(master, grid, grid.zoom, 0, grid.tile_y0) is None


def test_low_zoom_tile_larger_than_master_pastes_into_256():
    grid = mosaic.master_grid(RADAR)
    master = _synthetic_master(grid)
    # A z2 tile is far larger than the CONUS master; render must still produce a
    # 256² tile (the intersection pasted into a transparent canvas) or None.
    tiles = [t for t in mosaic.pyramid_tiles(master, grid, RADAR) if t[0] == 2]
    for _z, _x, _y, tile in tiles:
        assert tile.size == (256, 256)


def test_pyramid_yields_only_nonempty_tiles():
    grid = mosaic.master_grid(RADAR)
    master = _synthetic_master(grid)
    tiles = list(mosaic.pyramid_tiles(master, grid, RADAR))
    assert tiles  # the central block guarantees some coverage at every zoom
    for _z, _x, _y, tile in tiles:
        assert tile.getchannel('A').getbbox() is not None
