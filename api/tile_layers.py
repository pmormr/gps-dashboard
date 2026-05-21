# Registry of supported tile layers. Imported by both the Flask tile route
# (api/routes/tiles.py) and the precache CLI (tools/precache.py) so layer
# definitions stay in one place.
#
# `url` is a Python format string with {z}, {x}, {y} placeholders. USGS uses
# ArcGIS REST's {z}/{y}/{x} order — both placeholders are present so the same
# .format(z=, x=, y=) call works regardless of layer.
#
# `max_zoom` is the highest zoom the upstream actually serves; requests beyond
# it are rejected with 400. The frontend uses this as Leaflet's maxNativeZoom
# so it upsamples the deepest tile rather than asking for ones that don't
# exist.

LAYERS = {
    'osm': {
        'url': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        'attribution': '© OpenStreetMap contributors',
        'max_zoom': 19,
        # Cache files keep the .png extension regardless of upstream format; the
        # HTTP response's Content-Type is what browsers actually use.
        'media_type': 'image/png',
    },
    'usgs': {
        'url': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
        'attribution': 'USGS The National Map',
        'max_zoom': 16,
        'media_type': 'image/jpeg',
    },
}
