"""Derived-freshness probes for the offline-data chunk registry.

Every probe computes freshness from the data itself at read time — a DB
slice's ``synced_at``, a file's mtime+size, a cache directory's contents —
never from stored sync-state, which would drift the moment an importer runs
outside the system (plans/data-update-plan.md, decision 2).

All probes share one signature (an open :func:`api.db.get_connection`-style
connection in, a :class:`Freshness` out) so the registry can treat them
uniformly; file-backed probes simply ignore the connection. Module-level
paths (``api.tile_layers``, ``common.satcat``) are read through their module
at call time so tests can monkeypatch them.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api import tile_layers
from common import satcat
from common.timefmt import _canonical


@dataclass(frozen=True)
class Freshness:
    """One chunk's derived-at-read-time freshness snapshot.

    Attributes:
        synced_at: Canonical ms-UTC of the chunk's last update, or None when
            the chunk has never been synced (empty slice, absent file).
        detail: Probe-specific facts (row counts, file sizes, paths) surfaced
            verbatim in the status payload.
    """

    synced_at: str | None
    detail: dict[str, Any] = field(default_factory=dict)


def _mtime_canonical(mtime: float) -> str:
    """Format a filesystem mtime as a canonical ms-UTC timestamp."""
    return _canonical(datetime.fromtimestamp(mtime, tz=UTC))


def _file_freshness(path: Path) -> Freshness:
    """Freshness of a single-file asset: mtime + size; absent file = never."""
    try:
        st = path.stat()
    except OSError:
        return Freshness(None, {'path': str(path)})
    return Freshness(
        _mtime_canonical(st.st_mtime),
        {'path': str(path), 'size_bytes': st.st_size},
    )


def places_slice(conn: sqlite3.Connection, source: str) -> Freshness:
    """Freshness of one places-tier source slice (``nps``/``ridb``/``osm``/``gnis``).

    Imports are transactional full-replaces stamping one uniform ``synced_at``
    across the slice (the OSM merge carries the transfer DB's build stamp — the
    true data vintage), so a single-row seek answers "when" without scanning
    the slice; the row count is the sanity-floor context (a 470→12 parks
    import names itself).

    Args:
        conn: An open connection with the places sidecar attached.
        source: The ``places.source`` slice to probe.

    Returns:
        The slice's freshness, with the row count in ``detail``.
    """
    synced = conn.execute(
        'SELECT synced_at FROM places WHERE source = ? LIMIT 1', (source,)
    ).fetchone()
    n = conn.execute('SELECT count(*) FROM places WHERE source = ?', (source,)).fetchone()[0]
    return Freshness(synced[0] if synced else None, {'rows': n})


def place_wiki(conn: sqlite3.Connection) -> Freshness:
    """Freshness of the Wikipedia summary/thumbnail cache.

    ``max(fetched_at)`` (an O(1) seek via ``idx_place_wiki_fetched``) is the
    ordering signal against the OSM slice: the fetch is incremental, so a run
    that found nothing new leaves it unmoved — an acceptable approximation;
    the exact uncached-key gap count needs an OSM-slice scan and stays a
    runner-preflight concern, not a status read.

    Args:
        conn: An open connection with the places sidecar attached.

    Returns:
        The cache's freshness, with the article count in ``detail``.
    """
    row = conn.execute('SELECT max(fetched_at), count(*) FROM place_wiki').fetchone()
    return Freshness(row[0], {'rows': row[1]})


def phone_tier(conn: sqlite3.Connection) -> Freshness:
    """Freshness of the phone location-history tier (last Takeout import)."""
    row = conn.execute('SELECT max(imported_at), count(*) FROM phone_paths').fetchone()
    return Freshness(row[0], {'paths': row[1]})


def drone_tier(conn: sqlite3.Connection) -> Freshness:
    """Freshness of the drone telemetry tier (last flight import, any path)."""
    row = conn.execute('SELECT max(imported_at), count(*) FROM drone_flights').fetchone()
    return Freshness(row[0], {'flights': row[1]})


def basemap_archive(_conn: sqlite3.Connection) -> Freshness:
    """Freshness of the vector OSM basemap PMTiles archive (file mtime)."""
    return _file_freshness(tile_layers.PMTILES_PATH)


def terrain_archive(_conn: sqlite3.Connection) -> Freshness:
    """Presence of the terrain DEM PMTiles archive (a static dataset)."""
    return _file_freshness(tile_layers.TERRAIN_PMTILES_PATH)


def satcat_cache(_conn: sqlite3.Connection) -> Freshness:
    """Freshness of the CelesTrak SATCAT metadata cache (file mtime)."""
    return _file_freshness(satcat.DEFAULT_CACHE_PATH)


def raster_cache(_conn: sqlite3.Connection) -> Freshness:
    """Contents of the USGS raster tile cache: tile count, bytes, newest tile.

    Walks the cache directory stat-ing every file — tens of thousands of
    tiles, so this is the one probe that isn't O(1). Acceptable for a
    drill-in page read; revisit if the Phase 4 precache integration makes
    the status read hot.
    """
    root = tile_layers.TILE_CACHE_DIR
    if not root.is_dir():
        return Freshness(None, {'path': str(root)})
    tiles = 0
    size = 0
    newest = 0.0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            try:
                st = os.stat(os.path.join(dirpath, name))
            except OSError:
                continue
            size += st.st_size
            if name.endswith('.png'):
                tiles += 1
                newest = max(newest, st.st_mtime)
    if not tiles:
        return Freshness(None, {'path': str(root), 'tiles': 0})
    return Freshness(
        _mtime_canonical(newest),
        {'path': str(root), 'tiles': tiles, 'size_bytes': size},
    )


def docs_vault(_conn: sqlite3.Connection) -> Freshness:
    """Last push/auto-commit into the network-docs vault's bare repo.

    Reads ref-file mtimes (``packed-refs`` + ``refs/heads/*``) instead of
    spawning git — the vault updates only via pushes and the Docs tab's
    auto-commits, both of which move a ref.
    """
    git_dir = os.environ.get('GPS_NETWORK_DOCS_GIT_DIR')
    if not git_dir:
        return Freshness(None, {'configured': False})
    base = Path(git_dir)
    heads = base / 'refs' / 'heads'
    candidates = [base / 'packed-refs']
    if heads.is_dir():
        candidates.extend(heads.iterdir())
    times = [p.stat().st_mtime for p in candidates if p.is_file()]
    if not times:
        return Freshness(None, {'configured': True, 'path': git_dir})
    return Freshness(_mtime_canonical(max(times)), {'path': git_dir})
