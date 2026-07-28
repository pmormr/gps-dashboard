"""Master-grid derivation, export-strip split, and tile-pyramid rendering.

Deterministic given its inputs (the layer spec and the fetched strip images), so
the geometry is table-testable with synthetic images. The design (P0-verified):

* The master canvas is snapped to the layer's native-zoom tile lattice, so
  slicing it into ``{z}/{x}/{y}`` tiles is exact — no fractional resampling drift.
* The master exceeds the service's per-export height cap, so it is fetched as
  ``ceil(height / export_max_h)`` abutting vertical strips (no overlap margin —
  the strips share the boundary merc-y exactly) and pasted back together.
* Each pyramid tile is rendered by intersecting the tile with the master
  (a tile can be *larger* than the master at low zoom), resampling that overlap,
  and pasting it into a transparent 256² tile. Fully-transparent tiles are
  skipped, so the archive is sparse.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

from PIL import Image

from weather import geo
from weather.registry import RasterLayer

#: Slippy-tile edge in pixels.
TILE_PX = 256

Bbox = tuple[float, float, float, float]


@dataclass(frozen=True)
class MasterGrid:
    """The native-zoom-aligned master canvas covering one layer's bbox.

    Attributes:
        zoom: The native zoom the master is aligned to (``layer.max_zoom``).
        tile_x0, tile_y0, tile_x1, tile_y1: Inclusive native-zoom tile range.
        width, height: Master canvas size in pixels (tiles × 256).
        merc_bounds: ``(xmin, ymin, xmax, ymax)`` EPSG:3857 bounds of the canvas.
    """

    zoom: int
    tile_x0: int
    tile_y0: int
    tile_x1: int
    tile_y1: int
    width: int
    height: int
    merc_bounds: Bbox


@dataclass(frozen=True)
class StripSpec:
    """One vertical export request making up the master.

    Attributes:
        bbox: The strip's EPSG:3857 bounds.
        size: ``(width, height)`` in pixels.
        paste_y: The strip's top edge in master-pixel coordinates.
    """

    bbox: Bbox
    size: tuple[int, int]
    paste_y: int


def master_grid(layer: RasterLayer) -> MasterGrid:
    """Derive the native-zoom-aligned master grid covering a layer's bbox."""
    z = layer.max_zoom
    x0, y0, x1, y1 = geo.tile_range(z, layer.lon_bounds, layer.lat_bounds)
    width = (x1 - x0 + 1) * TILE_PX
    height = (y1 - y0 + 1) * TILE_PX
    xmin, _, _, ymax = geo.tile_merc_bounds(z, x0, y0)
    _, ymin, xmax, _ = geo.tile_merc_bounds(z, x1, y1)
    return MasterGrid(z, x0, y0, x1, y1, width, height, (xmin, ymin, xmax, ymax))


def strip_specs(grid: MasterGrid, export_max_h: int) -> list[StripSpec]:
    """Split the master into abutting vertical strips under the export cap.

    Args:
        grid: The master grid.
        export_max_h: The service's per-export height cap.

    Returns:
        Top-to-bottom strip specs whose pixel heights sum to the master height
        and whose bboxes tile the master with no gap or overlap.
    """
    width, height = grid.width, grid.height
    xmin, ymin, xmax, ymax = grid.merc_bounds
    n = max(1, math.ceil(height / export_max_h))
    rows = [round(i * height / n) for i in range(n + 1)]
    specs: list[StripSpec] = []
    for i in range(n):
        py0, py1 = rows[i], rows[i + 1]
        s_ymax = ymax - (ymax - ymin) * py0 / height
        s_ymin = ymax - (ymax - ymin) * py1 / height
        specs.append(StripSpec((xmin, s_ymin, xmax, s_ymax), (width, py1 - py0), py0))
    return specs


def assemble_master(
    grid: MasterGrid, specs: list[StripSpec], strips: list[Image.Image]
) -> Image.Image:
    """Paste fetched strips into one master canvas.

    Args:
        grid: The master grid.
        specs: The strip specs (from :func:`strip_specs`), in order.
        strips: The fetched strip images, aligned with ``specs``.

    Returns:
        The assembled RGBA master.
    """
    master = Image.new('RGBA', (grid.width, grid.height), (0, 0, 0, 0))
    for spec, strip in zip(specs, strips, strict=True):
        if strip.size != spec.size:
            strip = strip.resize(spec.size, Image.Resampling.LANCZOS)
        master.paste(strip, (0, spec.paste_y))
    return master


def _merc_to_px(grid: MasterGrid, mx: float, my: float) -> tuple[float, float]:
    """Map EPSG:3857 metres to master-pixel coordinates."""
    xmin, ymin, xmax, ymax = grid.merc_bounds
    px = (mx - xmin) / (xmax - xmin) * grid.width
    py = (ymax - my) / (ymax - ymin) * grid.height
    return px, py


def render_tile(
    master: Image.Image, grid: MasterGrid, z: int, x: int, y: int
) -> Image.Image | None:
    """Render one 256² pyramid tile from the master, or None if it has no data.

    Args:
        master: The assembled master canvas.
        grid: The master grid describing ``master``'s georeferencing.
        z, x, y: The target slippy tile.

    Returns:
        The RGBA tile, or None when the tile lies outside the master or the
        overlap is fully transparent (a tile to skip).
    """
    txmin, tymin, txmax, tymax = geo.tile_merc_bounds(z, x, y)
    mxmin, mymin, mxmax, mymax = grid.merc_bounds
    ixmin, ixmax = max(txmin, mxmin), min(txmax, mxmax)
    iymin, iymax = max(tymin, mymin), min(tymax, mymax)
    if ixmax <= ixmin or iymax <= iymin:
        return None
    sx0, sy0 = _merc_to_px(grid, ixmin, iymax)
    sx1, sy1 = _merc_to_px(grid, ixmax, iymin)
    sbox = (math.floor(sx0), math.floor(sy0), math.ceil(sx1), math.ceil(sy1))
    if sbox[2] <= sbox[0] or sbox[3] <= sbox[1]:
        return None
    src = master.crop(sbox)
    if src.getchannel('A').getbbox() is None:
        return None
    dx0 = (ixmin - txmin) / (txmax - txmin) * TILE_PX
    dx1 = (ixmax - txmin) / (txmax - txmin) * TILE_PX
    dy0 = (tymax - iymax) / (tymax - tymin) * TILE_PX
    dy1 = (tymax - iymin) / (tymax - tymin) * TILE_PX
    dw, dh = max(1, round(dx1 - dx0)), max(1, round(dy1 - dy0))
    src = src.resize((dw, dh), Image.Resampling.LANCZOS)
    if (dw, dh) == (TILE_PX, TILE_PX):
        return src
    tile = Image.new('RGBA', (TILE_PX, TILE_PX), (0, 0, 0, 0))
    tile.paste(src, (round(dx0), round(dy0)))
    return tile


def pyramid_tiles(
    master: Image.Image, grid: MasterGrid, layer: RasterLayer
) -> Iterator[tuple[int, int, int, Image.Image]]:
    """Yield every non-empty tile of the sparse ``min_zoom..max_zoom`` pyramid.

    Args:
        master: The assembled master canvas.
        grid: The master grid.
        layer: The layer spec (bbox + zoom range).

    Yields:
        ``(z, x, y, tile)`` for each tile carrying data.
    """
    for z in range(layer.min_zoom, layer.max_zoom + 1):
        x0, y0, x1, y1 = geo.tile_range(z, layer.lon_bounds, layer.lat_bounds)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                tile = render_tile(master, grid, z, x, y)
                if tile is not None:
                    yield z, x, y, tile
