"""The offline-data chunk registry: every chunk the van needs to go dark.

Declarative, the ``api/sensor_schema.py`` pattern: one :class:`Chunk` entry
per offline data chunk names how its freshness is derived (probe), when it
counts as stale, how it will update (action), what an update costs
(transfer), and its ordering dependencies. Adding a chunk is adding an entry.

Ordering rules that used to live in docs and operator memory ("re-run GNIS
after every OSM merge") are derived comparisons here: a chunk whose
``synced_at`` predates one of its ``deps`` gets a warning in the status
payload. See plans/data-update-plan.md.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from common.timefmt import now_canonical
from updater import probes
from updater.probes import Freshness

#: How a chunk updates. ``pi_run``: the Pi-side runner executes it (Phase 2+).
#: ``staged_file``: needs a file staged by the user first (e.g. a Takeout
#: export). ``recipe_only``: no update action — a documented laptop/NAS recipe
#: (terrain coverage expansion). ``readonly``: informational row for a flow
#: owned elsewhere (timer, git push).
ACTIONS = ('pi_run', 'staged_file', 'recipe_only', 'readonly')


@dataclass(frozen=True)
class Chunk:
    """One offline data chunk's registry entry.

    Attributes:
        id: Stable identifier (API paths, run bookkeeping).
        label: Human name shown in the UI.
        section: UI grouping key (``places``/``map``/``sky``/``history``/``docs``).
        action: One of :data:`ACTIONS` — the target update path.
        cadence: Human-readable target cadence, shown in the UI.
        transfer: Expected transfer cost of an update (shown before the
            Phase 2 button is pressed), or None when not applicable.
        stale_days: Age threshold before the chunk reads as stale; None =
            informational only (on-demand/static chunks never go stale).
        probe: Derived-freshness implementation (see ``updater.probes``).
        deps: Chunk ids this chunk must be refreshed *after*; being older
            than a dep derives an ordering warning.
    """

    id: str
    label: str
    section: str
    action: str
    cadence: str
    transfer: str | None
    stale_days: int | None
    probe: Callable[[sqlite3.Connection], Freshness]
    deps: tuple[str, ...] = ()


#: Display order is registry order. Staleness tiers: 45 d for the federal
#: schedule-bearing source (NPS events go stale fast), 180 d for the bulk
#:  seasonal sources and archives, 30 d for the tiny weekly SATCAT fetch.
CHUNKS: tuple[Chunk, ...] = (
    Chunk(
        id='nps',
        label='NPS places + events',
        section='places',
        action='pi_run',
        cadence='~monthly',
        transfer='~100 MB API walk',
        stale_days=45,
        probe=lambda conn: probes.places_slice(conn, 'nps'),
    ),
    Chunk(
        id='ridb',
        label='RIDB places',
        section='places',
        action='pi_run',
        cadence='~seasonal',
        transfer='245 MB zip',
        stale_days=180,
        probe=lambda conn: probes.places_slice(conn, 'ridb'),
    ),
    Chunk(
        id='osm',
        label='OSM POIs',
        section='places',
        action='pi_run',
        cadence='~seasonal',
        transfer='~16 GB PBFs → ~3 GB transfer DB',
        stale_days=180,
        probe=lambda conn: probes.places_slice(conn, 'osm'),
    ),
    Chunk(
        id='gnis',
        label='GNIS names',
        section='places',
        action='pi_run',
        cadence='~seasonal, after OSM',
        transfer='37 MB zip',
        stale_days=180,
        probe=lambda conn: probes.places_slice(conn, 'gnis'),
        deps=('osm',),
    ),
    Chunk(
        id='wiki',
        label='Wikipedia cache',
        section='places',
        action='pi_run',
        cadence='after OSM',
        transfer='~2–3 GB API fetch',
        stale_days=180,
        probe=probes.place_wiki,
        deps=('osm',),
    ),
    Chunk(
        id='basemap',
        label='Vector basemap',
        section='map',
        action='pi_run',
        cadence='~seasonal',
        transfer='~32 GB pmtiles extract',
        stale_days=180,
        probe=probes.basemap_archive,
    ),
    Chunk(
        id='terrain',
        label='Terrain DEM',
        section='map',
        action='recipe_only',
        cadence='static dataset',
        transfer=None,
        stale_days=None,
        probe=probes.terrain_archive,
    ),
    Chunk(
        id='raster',
        label='USGS raster cache',
        section='map',
        action='pi_run',
        cadence='on demand',
        transfer='user-chosen',
        stale_days=None,
        probe=probes.raster_cache,
    ),
    Chunk(
        id='satcat',
        label='SATCAT metadata',
        section='sky',
        action='pi_run',
        cadence='~weekly when online',
        transfer='~1 MB',
        stale_days=30,
        probe=probes.satcat_cache,
    ),
    Chunk(
        id='phone',
        label='Phone timeline',
        section='history',
        action='staged_file',
        cadence='on demand',
        transfer='Takeout export',
        stale_days=None,
        probe=probes.phone_tier,
    ),
    Chunk(
        id='drone',
        label='Drone sync',
        section='history',
        action='readonly',
        cadence='timer-driven',
        transfer=None,
        stale_days=None,
        probe=probes.drone_tier,
    ),
    Chunk(
        id='docs',
        label='Docs vault',
        section='docs',
        action='readonly',
        cadence='git push',
        transfer=None,
        stale_days=None,
        probe=probes.docs_vault,
    ),
)


def _age_days(synced_at: str, now: datetime) -> float:
    """Age of a canonical ms-UTC timestamp, in fractional days."""
    return (now - datetime.fromisoformat(synced_at)).total_seconds() / 86400.0


def _dep_warnings(chunk: Chunk, freshness: dict[str, Freshness]) -> list[str]:
    """Derive ordering warnings: this chunk predates one of its deps.

    Canonical timestamps are fixed-width, so lexical comparison is
    chronological. Missing sides compare as no-warning — there is nothing
    to order against.
    """
    labels = {c.id: c.label for c in CHUNKS}
    warnings = []
    for dep in chunk.deps:
        mine = freshness[chunk.id].synced_at
        theirs = freshness[dep].synced_at
        if mine is not None and theirs is not None and mine < theirs:
            warnings.append(
                f'Updated before the current {labels[dep]} slice — re-run after every '
                f'{labels[dep]} update.'
            )
    return warnings


def status_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    """Derive the full ``/api/data/status`` document from the live data.

    Runs every registered probe and folds in staleness state and ordering
    warnings. A probe failure degrades that chunk to ``state: 'error'``
    rather than taking down the whole readiness glance.

    Args:
        conn: An open :func:`api.db.get_connection` connection.

    Returns:
        ``{'generated_at': ..., 'chunks': [...]}`` — one entry per registry
        chunk with ``state`` ok | stale | missing | error.
    """
    now = datetime.now(UTC)
    freshness: dict[str, Freshness] = {}
    errors: dict[str, str] = {}
    for chunk in CHUNKS:
        try:
            freshness[chunk.id] = chunk.probe(conn)
        except Exception as exc:  # noqa: BLE001 — one bad probe must not hide the rest
            freshness[chunk.id] = Freshness(None)
            errors[chunk.id] = str(exc)

    chunks: list[dict[str, Any]] = []
    for chunk in CHUNKS:
        fresh = freshness[chunk.id]
        if chunk.id in errors:
            state, age = 'error', None
        elif fresh.synced_at is None:
            state, age = 'missing', None
        else:
            age = _age_days(fresh.synced_at, now)
            state = 'stale' if chunk.stale_days is not None and age > chunk.stale_days else 'ok'
        chunks.append(
            {
                'id': chunk.id,
                'label': chunk.label,
                'section': chunk.section,
                'action': chunk.action,
                'cadence': chunk.cadence,
                'transfer': chunk.transfer,
                'stale_days': chunk.stale_days,
                'synced_at': fresh.synced_at,
                'age_days': age,
                'state': state,
                'detail': fresh.detail,
                'warnings': _dep_warnings(chunk, freshness),
                **({'error': errors[chunk.id]} if chunk.id in errors else {}),
            }
        )
    return {'generated_at': now_canonical(), 'chunks': chunks}
