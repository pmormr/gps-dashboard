"""The weather-layer registry — radar is entry one.

Every layer the subsystem captures is one entry here (the ``api/sensor_schema``
and ``updater/chunks`` pattern): a declarative spec naming its upstream service,
capture geometry, cadence, and retention, so the fetcher and delivery routes
read config rather than hard-coding radar. Adding a raster layer is adding a
:class:`RasterLayer`; the vector/forecast pipelines get their own spec types
when those layers land (see plans/weather-plan.md).

Path resolution lives here too so the fetcher (writer) and the Flask route
(reader) resolve the same archive location from one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: NWS MRMS quality-controlled base reflectivity ImageServer (public, no key).
#: The documented machine backend behind radar.weather.gov; Web-Mercator native.
RADAR_SERVICE_URL = (
    'https://mapservices.weather.noaa.gov/eventdriven/rest/services/'
    'radar/radar_base_reflectivity_time/ImageServer'
)


@dataclass(frozen=True)
class RasterLayer:
    """A capture-while-online raster weather layer (radar and its kin).

    Attributes:
        id: Stable identifier — archive subdirectory, API path segment, chunk
            id. Digits-and-lowercase; no path separators.
        label: Human name for the UI.
        service_url: ArcGIS ImageServer base URL (``exportImage``/``query``).
        subset: The ``idp_subset`` catalog filter isolating one region per
            cycle (``CONUS`` excludes AK/HI/Caribbean — one raster per frame).
        lon_bounds: ``(west, east)`` capture longitudes in degrees.
        lat_bounds: ``(south, north)`` capture latitudes in degrees.
        min_zoom: Shallowest pyramid zoom to slice.
        max_zoom: Native/deepest pyramid zoom (radar ≈ z8 at 565 m/px).
        export_max_h: The service's ``maxImageHeight`` export cap; the master
            splits into ``ceil(height / export_max_h)`` vertical strips.
        retention_days: Rolling-archive window; older frames are pruned.
        attribution: Credit string baked into each PMTiles archive's metadata.
    """

    id: str
    label: str
    service_url: str
    subset: str
    lon_bounds: tuple[float, float]
    lat_bounds: tuple[float, float]
    min_zoom: int
    max_zoom: int
    export_max_h: int
    retention_days: int
    attribution: str


#: Layer one. The CONUS bbox is the plan estimate; the fetcher snaps it outward
#: to the z8 tile lattice (P0: → master 11008×5888, 2 vertical exports).
RADAR = RasterLayer(
    id='radar',
    label='Radar (base reflectivity)',
    service_url=RADAR_SERVICE_URL,
    subset='CONUS',
    lon_bounds=(-126.3, -66.9),
    lat_bounds=(24.9, 49.4),
    min_zoom=2,
    max_zoom=8,
    export_max_h=4100,
    retention_days=14,
    attribution='NOAA/NWS MRMS base reflectivity',
)

#: Registry of raster layers, keyed by id. Grows per plans/weather-plan.md.
RASTER_LAYERS: dict[str, RasterLayer] = {RADAR.id: RADAR}


def archive_root() -> Path:
    """Resolve the weather archive root.

    ``GPS_WEATHER_ARCHIVE_DIR`` on the Pi points at the NVMe cache; the local
    default sits beside the other gps-dashboard caches. Read at call time so a
    test or env override follows. The archive is ephemeral and
    rebuild-worthless — deliberately outside the DB backup path.

    Returns:
        The directory holding one subdirectory per layer.
    """
    return Path(
        os.environ.get(
            'GPS_WEATHER_ARCHIVE_DIR', str(Path.home() / '.cache' / 'gps-dashboard' / 'weather')
        )
    )


def layer_dir(layer_id: str) -> Path:
    """Directory holding one layer's per-frame PMTiles archives."""
    return archive_root() / layer_id


def frame_path(layer_id: str, frame_ms: int) -> Path:
    """Path to one frame's PMTiles archive (named by its epoch-ms frame key)."""
    return layer_dir(layer_id) / f'{frame_ms}.pmtiles'
