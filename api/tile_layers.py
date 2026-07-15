# Registry of supported tile layers + the on-disk tile/archive paths. Imported
# by the Flask tile route (api/routes/tiles.py), the precache CLI
# (tools/precache.py), and the offline-data freshness probes (updater/probes.py)
# so layer definitions and asset locations stay in one Flask-free place.
#
# `url` is a Python format string with {z}, {x}, {y} placeholders. USGS uses
# ArcGIS REST's {z}/{y}/{x} order — both placeholders are present so the same
# .format(z=, x=, y=) call works regardless of layer.
#
# `max_zoom` is the highest zoom the upstream actually serves; requests beyond
# it are rejected with 400. The frontend caps the raster source's `maxzoom`
# here so MapLibre overzooms the deepest tile rather than asking for ones that
# don't exist.

import os
from pathlib import Path

TILE_CACHE_DIR = Path(
    os.environ.get('GPS_TILE_CACHE_DIR', Path.home() / '.cache' / 'gps-dashboard' / 'tiles')
)

# Vector OSM basemap: a single immutable PMTiles archive served with byte-range
# support, read client-side by pmtiles.js. Persistent asset (not a cache), so it
# has its own env var rather than deriving from GPS_TILE_CACHE_DIR.
PMTILES_PATH = Path(
    os.environ.get(
        'GPS_PMTILES_PATH', Path.home() / '.cache' / 'gps-dashboard' / 'northamerica.pmtiles'
    )
)

# Terrarium-encoded elevation tiles, also a single immutable PMTiles archive.
# Read client-side by MapLibre via the pmtiles protocol + a raster-dem source
# with `encoding: 'terrarium'`. Same operational model as the OSM archive.
TERRAIN_PMTILES_PATH = Path(
    os.environ.get(
        'GPS_TERRAIN_PMTILES_PATH',
        Path.home() / '.cache' / 'gps-dashboard' / 'northamerica-terrain.pmtiles',
    )
)

# Raster layers only. OSM is now a vector PMTiles basemap served separately
# (see api/routes/tiles.py:osm_pmtiles and static/vendor/basemap), so USGS is
# the lone raster layer left.
LAYERS = {
    'usgs': {
        'url': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
        'attribution': 'USGS The National Map',
        'max_zoom': 16,
        # Cache files keep the .png extension regardless of upstream format (USGS
        # serves JPEG); the HTTP response's Content-Type is what browsers use.
        'media_type': 'image/jpeg',
    },
}
