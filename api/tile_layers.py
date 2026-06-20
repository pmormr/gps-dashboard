# Registry of supported tile layers. Imported by both the Flask tile route
# (api/routes/tiles.py) and the precache CLI (tools/precache.py) so layer
# definitions stay in one place.
#
# `url` is a Python format string with {z}, {x}, {y} placeholders. USGS uses
# ArcGIS REST's {z}/{y}/{x} order — both placeholders are present so the same
# .format(z=, x=, y=) call works regardless of layer.
#
# `max_zoom` is the highest zoom the upstream actually serves; requests beyond
# it are rejected with 400. The frontend caps the raster source's `maxzoom`
# here so MapLibre overzooms the deepest tile rather than asking for ones that
# don't exist.

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
