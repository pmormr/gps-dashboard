"""Weather-archive delivery: per-frame PMTiles + the frame index.

Serves the rolling radar archive the fetcher (``tools/fetch_weather.py``) writes:
each frame's PMTiles by byte-range (the ``osm.pmtiles`` serving model — the
browser range-reads only the tiles in view) and a JSON index of available frame
instants the frontend builds its scrubber/animation and tile URLs from.
Read-only; the archive is owned by the ``weather-fetch`` timer. Archive health
lives in the ``/data`` chunk registry, not a bespoke status endpoint here.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from flask import Blueprint, Response, abort, jsonify, request, send_file

from common.timefmt import format_canonical, now_canonical
from weather import archive, registry
from weather.registry import RASTER_LAYERS, VECTOR_LAYERS

weather_bp = Blueprint('weather', __name__)


@weather_bp.get('/tiles/weather/<layer>/<int:frame_ts>.pmtiles')
def weather_pmtiles(layer: str, frame_ts: int) -> Response:
    """Range-serve one frame's PMTiles archive.

    Mirrors ``osm_pmtiles``: ``conditional=True`` makes Werkzeug honor Range
    (206 Partial Content) with an mtime/size ETag, so pmtiles.js byte-range
    reads the header, directory, and individual tiles it needs. ``frame_ts`` is
    an ``int`` converter (digits only — no path traversal) and ``layer`` is
    gated against the registry.

    Args:
        layer: The raster layer id (e.g. ``radar``).
        frame_ts: The frame instant / archive key (epoch-ms).

    Returns:
        The PMTiles archive as a range-capable octet-stream.
    """
    if layer not in RASTER_LAYERS:
        abort(404)
    path = registry.frame_path(layer, frame_ts)
    if not path.exists():
        abort(404)
    return send_file(path, mimetype='application/octet-stream', conditional=True)


@weather_bp.get('/api/weather/<layer>/frames')
def weather_frames(layer: str) -> Response:
    """Available frame instants for a layer, newest first.

    The animation window is the head of the list (``frames[:N]`` = the recent
    loop); the full list is the 14-day scrub range. ``?window=<hours>`` trims to
    the trailing window (a float) for a cheap recent-only fetch.

    Args:
        layer: The raster layer id.

    Returns:
        ``{layer, generated_at, count, newest, oldest, frames}`` — ``frames``
        descending epoch-ms; ``newest``/``oldest`` null when the archive is empty.
    """
    if layer not in RASTER_LAYERS:
        abort(404)
    frames = sorted(archive.existing_frames(layer), reverse=True)
    window = request.args.get('window', type=float)
    if window is not None:
        cutoff = int(datetime.now(UTC).timestamp() * 1000) - int(window * 3_600_000)
        frames = [f for f in frames if f >= cutoff]
    return jsonify(
        {
            'layer': layer,
            'generated_at': now_canonical(),
            'count': len(frames),
            'newest': frames[0] if frames else None,
            'oldest': frames[-1] if frames else None,
            'frames': frames,
        }
    )


@weather_bp.get('/api/weather/<layer>/geojson')
def weather_geojson(layer: str) -> Response:
    """Serve a vector layer's stored GeoJSON snapshot, MapLibre-loadable.

    Returns the stored FeatureCollection with ``fetched_at`` (the snapshot's
    file mtime, canonical ms-UTC) injected as an extra member — valid GeoJSON,
    ignored by MapLibre, read by the view for the age label. An empty
    FeatureCollection (never fetched) keeps a MapLibre source and the frontend
    happy. The frontend drops features past their own ``expires``.

    Args:
        layer: The vector layer id (e.g. ``warnings``).

    Returns:
        The FeatureCollection plus ``fetched_at`` (null when never fetched).
    """
    if layer not in VECTOR_LAYERS:
        abort(404)
    path = registry.vector_path(layer)
    if not path.exists():
        return jsonify({'type': 'FeatureCollection', 'features': [], 'fetched_at': None})
    fc = json.loads(path.read_text())
    fc['fetched_at'] = format_canonical(datetime.fromtimestamp(path.stat().st_mtime, tz=UTC))
    return jsonify(fc)
