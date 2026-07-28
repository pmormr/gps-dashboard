"""Weather-map subsystem: capture-while-online, play-offline radar archive.

Radar is layer one of a registry designed to grow other NWS/NOAA layers
(see ``weather/registry.py``). The pipeline is pure helpers here plus a thin
``tools/fetch_weather.py`` CLI, mirroring the ``radio/`` split — the geometry,
mosaic, and retention logic are clockless and table-tested; only the source
client and archive writer touch the network and filesystem.
"""
