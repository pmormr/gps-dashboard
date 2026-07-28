"""NWS ImageServer client for the radar source: enumerate + export.

The pure request builders and the catalog parser are split from the I/O so they
stay table-testable; :class:`ImageServerClient` wraps httpx for the two calls the
fetcher makes — the catalog ``query`` (which frames exist upstream now) and
``exportImage`` (fetch one strip at a frame instant).

P0 findings baked in: enumeration filters ``idp_subset`` so one raster comes
back per cycle (the catalog otherwise returns CONUS + CARIB + … per instant),
the frame key is the raster's ``idp_validtime`` (epoch-ms), and that same value
is the ``exportImage`` ``time`` — one API surface for enumerate and fetch.
"""

from __future__ import annotations

import io
import json
from typing import Any

import httpx
from PIL import Image

#: Identifying UA per the repo convention (api/routes/tiles.py, precache.py).
USER_AGENT = 'gps-dashboard/1.0 (pmormr@gmail.com)'

Bbox = tuple[float, float, float, float]


class SourceError(RuntimeError):
    """An upstream call returned something other than the expected payload."""


def catalog_params(subset: str) -> dict[str, str]:
    """REST catalog ``query`` params enumerating one subset's frames.

    Filtering ``idp_subset`` (rather than a geometry envelope) yields exactly
    one raster per update cycle; ``timeExtent`` and the WMS time dimension can't
    enumerate frames, so this catalog query is the enumeration surface.

    Args:
        subset: The ``idp_subset`` value to isolate (e.g. ``CONUS``).

    Returns:
        Query-string params for ``<service>/query``.
    """
    return {
        'where': f"idp_subset='{subset}'",
        'outFields': 'idp_validtime,idp_subset',
        'returnGeometry': 'false',
        'orderByFields': 'idp_validtime DESC',
        'f': 'json',
    }


def parse_frames(catalog_json: dict[str, Any], subset: str) -> list[int]:
    """Extract sorted-descending frame instants (epoch-ms) from a catalog reply.

    Args:
        catalog_json: The parsed ``query?f=json`` response.
        subset: The subset to keep (defensive — the query already filters it).

    Returns:
        Distinct ``idp_validtime`` values, newest first.

    Raises:
        SourceError: The reply carried an ArcGIS ``error`` object.
    """
    if 'error' in catalog_json:
        raise SourceError(f'catalog error: {catalog_json["error"]}')
    times: set[int] = set()
    for feature in catalog_json.get('features', []):
        attrs = feature.get('attributes', {})
        if attrs.get('idp_subset') not in (None, subset):
            continue
        validtime = attrs.get('idp_validtime')
        if isinstance(validtime, int):
            times.add(validtime)
    return sorted(times, reverse=True)


def export_params(bbox: Bbox, size: tuple[int, int], time_ms: int) -> dict[str, str]:
    """REST ``exportImage`` params for one native-res strip at a frame instant.

    Args:
        bbox: The strip's EPSG:3857 ``(xmin, ymin, xmax, ymax)`` bounds.
        size: ``(width, height)`` in pixels.
        time_ms: The frame instant (a raster's ``idp_validtime``).

    Returns:
        Query-string params for ``<service>/exportImage`` — a transparent
        ``png32`` RGBA image in Web Mercator.
    """
    return {
        'bbox': ','.join(repr(v) for v in bbox),
        'bboxSR': '3857',
        'imageSR': '3857',
        'size': f'{size[0]},{size[1]}',
        'format': 'png32',
        'transparent': 'true',
        'time': str(time_ms),
        'f': 'image',
    }


class ImageServerClient:
    """Minimal httpx wrapper over one ArcGIS ImageServer (radar's source)."""

    def __init__(self, service_url: str, *, timeout: float = 60.0) -> None:
        """Bind the client to a service.

        Args:
            service_url: The ImageServer base URL.
            timeout: Per-request timeout in seconds.
        """
        self._url = service_url.rstrip('/')
        self._client = httpx.Client(headers={'User-Agent': USER_AGENT}, timeout=timeout)

    def __enter__(self) -> ImageServerClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def reachable(self, timeout: float = 8.0) -> bool:
        """Whether the service answers its metadata endpoint (offline preflight).

        Args:
            timeout: Short timeout for the liveness probe.

        Returns:
            True if ``?f=json`` returned 200; False on any network error or
            non-200 — the fetcher treats False as a clean off-grid no-op.
        """
        try:
            resp = self._client.get(self._url, params={'f': 'json'}, timeout=timeout)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def list_frames(self, subset: str) -> list[int]:
        """Enumerate available frame instants for a subset, newest first.

        Args:
            subset: The ``idp_subset`` filter.

        Returns:
            Distinct ``idp_validtime`` epoch-ms values, descending.

        Raises:
            SourceError: The catalog reply was not valid JSON or carried an error.
        """
        resp = self._client.get(f'{self._url}/query', params=catalog_params(subset))
        resp.raise_for_status()
        try:
            payload = resp.json()
        except json.JSONDecodeError as exc:
            raise SourceError(f'catalog returned non-JSON: {resp.text[:200]}') from exc
        return parse_frames(payload, subset)

    def export(self, bbox: Bbox, size: tuple[int, int], time_ms: int) -> Image.Image:
        """Export one RGBA strip at a frame instant.

        Args:
            bbox: The strip's EPSG:3857 bounds.
            size: ``(width, height)`` in pixels.
            time_ms: The frame instant.

        Returns:
            The strip as an RGBA :class:`PIL.Image.Image`.

        Raises:
            SourceError: The service returned a non-image payload (an error
                object even under ``f=image``).
        """
        resp = self._client.get(
            f'{self._url}/exportImage', params=export_params(bbox, size, time_ms)
        )
        resp.raise_for_status()
        if 'image' not in resp.headers.get('content-type', ''):
            raise SourceError(f'exportImage returned non-image: {resp.text[:200]}')
        return Image.open(io.BytesIO(resp.content)).convert('RGBA')
