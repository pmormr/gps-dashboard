"""Web-Mercator + slippy-tile geometry for the weather-raster pipeline.

Pure, clockless, table-testable. The fetcher snaps its CONUS export grid to the
slippy-tile lattice at the layer's native zoom so the master mosaic slices into
standard ``{z}/{x}/{y}`` tiles with no resampling misalignment — the same tile
scheme MapLibre and ``tools/precache.py`` use, expressed here in EPSG:3857
metres (what the ImageServer's ``exportImage`` wants) rather than tile indices.
"""

from __future__ import annotations

import math

#: Earth radius used by the Web-Mercator (EPSG:3857) sphere.
R = 6378137.0

#: Half the Web-Mercator world span in metres (``pi * R`` ≈ 20037508.34); the
#: projected plane runs ``[-ORIGIN, ORIGIN]`` on both axes.
ORIGIN = math.pi * R


def lonlat_to_merc(lon: float, lat: float) -> tuple[float, float]:
    """Project WGS84 lon/lat degrees to EPSG:3857 metres.

    Args:
        lon: Longitude in degrees.
        lat: Latitude in degrees (clamped implicitly by the caller's bbox;
            not valid at the poles).

    Returns:
        ``(x, y)`` in Web-Mercator metres.
    """
    x = math.radians(lon) * R
    y = math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
    return x, y


def merc_to_lon(x: float) -> float:
    """Inverse-project an EPSG:3857 x metre to a longitude in degrees."""
    return math.degrees(x / R)


def merc_to_lat(y: float) -> float:
    """Inverse-project an EPSG:3857 y metre to a latitude in degrees."""
    return math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)


def tile_range(
    z: int, lon_bounds: tuple[float, float], lat_bounds: tuple[float, float]
) -> tuple[int, int, int, int]:
    """Inclusive slippy-tile range covering a lon/lat box at zoom ``z``.

    Args:
        z: Zoom level.
        lon_bounds: ``(west, east)`` longitudes in degrees.
        lat_bounds: ``(south, north)`` latitudes in degrees.

    Returns:
        ``(x0, y0, x1, y1)`` inclusive tile indices — ``x`` increasing east,
        ``y`` increasing south (standard XYZ).
    """
    n = 2**z
    west, east = lon_bounds
    south, north = lat_bounds
    xw, yn = lonlat_to_merc(west, north)
    xe, ys = lonlat_to_merc(east, south)
    x0 = int((xw + ORIGIN) / (2 * ORIGIN) * n)
    x1 = int((xe + ORIGIN) / (2 * ORIGIN) * n)
    y0 = int((ORIGIN - yn) / (2 * ORIGIN) * n)
    y1 = int((ORIGIN - ys) / (2 * ORIGIN) * n)
    return x0, y0, x1, y1


def tile_merc_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """EPSG:3857 bounds of tile ``(z, x, y)``.

    Args:
        z: Zoom level.
        x: Tile column (0 at the antimeridian, increasing east).
        y: Tile row (0 at the top, increasing south).

    Returns:
        ``(xmin, ymin, xmax, ymax)`` in Web-Mercator metres.
    """
    n = 2**z
    ts = 2 * ORIGIN / n
    xmin = -ORIGIN + x * ts
    xmax = -ORIGIN + (x + 1) * ts
    ymax = ORIGIN - y * ts
    ymin = ORIGIN - (y + 1) * ts
    return xmin, ymin, xmax, ymax
