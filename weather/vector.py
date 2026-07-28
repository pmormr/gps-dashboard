"""Vector weather layers — keep-latest GeoJSON snapshots (warnings, P4).

The lightweight pipeline the registry's raster half doesn't cover: fetch a
FeatureCollection, store the latest snapshot atomically (overwrite — no rolling
archive), serve it to a MapLibre fill/line. This is the proof the registry
generalizes beyond the tiled-raster radar path — a new vector layer is a
:class:`~weather.registry.VectorLayer` entry, not new plumbing.
"""

from __future__ import annotations

import json
import os

import httpx

from weather import registry
from weather.registry import VectorLayer
from weather.source import USER_AGENT


def fetch_and_store(
    layer: VectorLayer, *, timeout: float = 30.0, client: httpx.Client | None = None
) -> int:
    """Fetch a vector layer's GeoJSON and overwrite its stored snapshot.

    Written tmp-then-renamed so a reader never sees a partial file. The whole
    upstream FeatureCollection is stored verbatim; the frontend filters expired
    features and the delivery route stamps the fetch time.

    Args:
        layer: The vector layer to capture.
        timeout: Per-request timeout in seconds (owned-client path only).
        client: An optional httpx client to reuse (tests inject a MockTransport);
            the identifying headers ride on the request either way.

    Returns:
        The number of features stored.

    Raises:
        httpx.HTTPError: On a network/HTTP failure (the caller treats this as an
            off-grid no-op).
    """
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/geo+json'}
    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        resp = http.get(layer.source_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if owns_client:
            http.close()

    features = data.get('features', []) if isinstance(data, dict) else []
    dest = registry.vector_path(layer.id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f'{dest.name}.{os.getpid()}.tmp')
    try:
        tmp.write_text(json.dumps(data))
        tmp.replace(dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return len(features)
