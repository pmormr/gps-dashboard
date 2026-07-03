"""Shared named-region registry for tooling.

Used by `tools/precache.py` (raster pre-cache) and `tools/fetch_terrain_tiles.py`
(Mapzen Terrarium fetch). Add new regions here, not in the consumers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Region:
    """A named geographic bounding box in WGS84 degrees.

    Attributes:
        name: Slug used as the dict key in `REGIONS` and on `--region` CLI flags.
        min_lon: Western bound (degrees, negative west of the prime meridian).
        min_lat: Southern bound (degrees).
        max_lon: Eastern bound (degrees).
        max_lat: Northern bound (degrees).
    """

    name: str
    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Return the bbox as a `(min_lon, min_lat, max_lon, max_lat)` tuple.

        Convenience for call sites that want to splat into positional helpers, e.g.
        `tiles_for_bbox(*region.bbox, z)`.
        """
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


REGIONS: dict[str, Region] = {
    # Whole lower-48. Practical only to ~z12 for raster (see CLAUDE.md / docs);
    # deeper zooms run to hundreds of GB and days of downloading.
    'conus': Region('conus', -125.00, 24.50, -66.50, 49.50),
    # Full continent — matches the OSM PMTiles archive bbox so vector basemap
    # and terrain archive cover the same operational area.
    'north_america': Region('north_america', -168.00, 7.00, -52.00, 72.00),
    'arizona': Region('arizona', -114.82, 31.33, -109.04, 37.00),
    'california': Region('california', -124.41, 32.53, -114.13, 42.01),
    'colorado': Region('colorado', -109.06, 36.99, -102.04, 41.00),
    'idaho': Region('idaho', -117.24, 41.99, -111.04, 49.00),
    'maryland': Region('maryland', -79.49, 37.89, -74.99, 39.72),
    'montana': Region('montana', -116.05, 44.36, -104.04, 49.00),
    'nevada': Region('nevada', -120.00, 35.00, -114.03, 42.00),
    'new_mexico': Region('new_mexico', -109.05, 31.33, -103.00, 37.00),
    'oregon': Region('oregon', -124.57, 41.99, -116.46, 46.26),
    'pennsylvania': Region('pennsylvania', -80.52, 39.72, -74.69, 42.27),
    'utah': Region('utah', -114.05, 36.99, -109.04, 42.00),
    'virginia': Region('virginia', -83.68, 36.54, -75.24, 39.47),
    'washington': Region('washington', -124.73, 45.54, -116.92, 49.00),
    'west_virginia': Region('west_virginia', -82.65, 37.20, -77.72, 40.64),
    'wyoming': Region('wyoming', -111.06, 40.99, -104.05, 45.01),
}
