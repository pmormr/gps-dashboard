"""Build the OSM POI transfer DB for the places tier (laptop/NAS only — never the Pi).

Phase 1 of the POI expansion (``plans/attractions-poi-plan.md``): turn Geofabrik
PBF extracts into a standalone SQLite *transfer DB* whose ``places`` table is
column-compatible with the sidecar's (``api.db._init_places_schema``) plus the
two broad-POI columns, ``category`` and ``rank``. The Pi never sees a PBF: this
tool runs where the download lives (laptop or rex-nas), and the finished DB is
scp'd to the Pi and merged by ``tools/import_places.py --osm-db`` (Phase 2,
full-replace of ``source='osm'``).

Pipeline::

    Geofabrik .osm.pbf ──osmium tags-filter──▶ POI-only .pbf ──pyosmium──▶ transfer .db

* The C++ prefilter (``osmium tags-filter``; ``osmium-tool`` via brew/apt) cuts
  the input by ~99% so the Python pass stays minutes, not hours. Referenced
  nodes are kept by default, so way geometry survives the filter. The filtered
  file lands beside the source with the filter-expression hash in its name and
  is reused on re-runs while fresh — taxonomy edits change the hash, so a stale
  prefilter can't survive a scope change.
* The pyosmium pass processes nodes, linear ways, AND assembled areas (closed
  ways + multipolygon relations) — shops/restaurants are frequently mapped on
  building ways, so a nodes-only scan silently drops about half the POIs.
  Ways/areas pin at the average of their (outer-ring) vertices: a
  representative point, not a true centroid — fine for a map pin. Non-area
  relations (``type=site``…) are not extracted; rare, accepted.
* ``TAXONOMY`` below is the reviewable decision table (plan open decision B):
  each element's primary tag (first ``TAXONOMY`` key present, declaration order
  = priority) maps to a unified ``category`` plus a ``rank`` pin-zoom tier.
  The ``osmium tags-filter`` expressions derive from the same table, so filter
  and taxonomy cannot drift apart. Unnamed rows fall back to brand/operator,
  then a humanized tag value ("Drinking water") — searchable by what they are.

Rank tiers (Phase 3 owns the actual zoom gates; these are the intent):

1. major destination (theme park, airport) — visible from far out (~z5+)
2. significant stop (fuel, campground, supermarket, peak, hospital) (~z9+)
3. common POI (restaurant, hotel, most shops, trailhead-scale nature) (~z12+)
4. minor POI (ATM, playground, toilets) (~z14+)
5. micro furniture (bench, waste basket, hydrant) — search-only, never pinned

Examples::

    # Scope sanity check: per-category counts, no DB written
    uv run tools/build_osm_pois.py ~/osm-lab/colorado-latest.osm.pbf --dry-run

    # Build a transfer DB from one extract
    uv run tools/build_osm_pois.py ~/osm-lab/colorado-latest.osm.pbf \\
        -o ~/osm-lab/osm-places.db

    # Full NA build (both extracts cover the basemap bbox, which also clips here)
    uv run tools/build_osm_pois.py north-america-latest.osm.pbf \\
        central-america-latest.osm.pbf -o osm-places.db
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import osmium

from common.cli import run_cli
from common.timefmt import now_canonical

SOURCE = 'osm'
# Matches the vector basemap archive footprint (min_lon, min_lat, max_lon, max_lat) —
# searchable data everywhere the map renders, nothing where it doesn't.
BASEMAP_BBOX = (-168.0, 7.0, -52.0, 72.0)
BATCH_ROWS = 5000
PROGRESS_EVERY = 250_000

# --- Taxonomy (plan open decision B — the reviewable table) --------------------------

# category + rank for one primary-tag value; '*' = key-level default. Keys with
# no '*' are *selective*: unlisted values never even leave the prefilter.
# Declaration order of the outer dict is the primary-key priority (a
# tourism=hotel + amenity=restaurant classifies as the hotel).
Rule = tuple[str, int]

TAXONOMY: dict[str, dict[str, Rule]] = {
    'tourism': {
        '*': ('attraction', 3),
        'attraction': ('attraction', 2),
        'museum': ('attraction', 2),
        'gallery': ('attraction', 3),
        'theme_park': ('attraction', 1),
        'zoo': ('attraction', 1),
        'aquarium': ('attraction', 2),
        'artwork': ('attraction', 4),
        'information': ('attraction', 4),
        'viewpoint': ('outdoors', 3),
        'picnic_site': ('outdoors', 3),
        'camp_site': ('camping', 2),
        'caravan_site': ('camping', 2),
        'camp_pitch': ('camping', 4),
        'wilderness_hut': ('camping', 3),
        'alpine_hut': ('camping', 3),
        'hotel': ('lodging', 3),
        'motel': ('lodging', 3),
        'hostel': ('lodging', 3),
        'guest_house': ('lodging', 3),
        'chalet': ('lodging', 3),
        'apartment': ('lodging', 4),
    },
    'historic': {
        # Default 4: the long tail is small plaques/markers (memorial, wayside_*).
        '*': ('historic', 4),
        'castle': ('historic', 2),
        'fort': ('historic', 2),
        'monument': ('historic', 2),
        'archaeological_site': ('historic', 3),
        'battlefield': ('historic', 3),
        'ruins': ('historic', 3),
        'mine': ('historic', 3),
        'wreck': ('historic', 3),
    },
    # Selective: destination natural features only — the decision-1 floor keeps
    # mass-mapped non-places (individual trees, cliffs-as-lines) out entirely.
    'natural': {
        'peak': ('outdoors', 2),
        'volcano': ('outdoors', 2),
        'hot_spring': ('outdoors', 2),
        'geyser': ('outdoors', 2),
        'saddle': ('outdoors', 4),
        'spring': ('outdoors', 3),
        'cave_entrance': ('outdoors', 3),
        'arch': ('outdoors', 3),
        'sinkhole': ('outdoors', 4),
        'beach': ('outdoors', 3),
        'glacier': ('outdoors', 3),
        'dune': ('outdoors', 4),
        'cape': ('outdoors', 3),
    },
    'waterway': {
        'waterfall': ('outdoors', 2),
    },
    'leisure': {
        '*': ('recreation', 4),
        'park': ('park', 3),
        'nature_reserve': ('park', 2),
        'garden': ('park', 3),
        'dog_park': ('park', 4),
        'common': ('park', 4),
        'outdoor_seating': ('utility', 5),
        'bleachers': ('utility', 5),
        'stadium': ('recreation', 2),
        'sports_centre': ('recreation', 3),
        'golf_course': ('recreation', 3),
        'marina': ('recreation', 3),
        'slipway': ('recreation', 3),
        'fitness_centre': ('recreation', 3),
        'swimming_pool': ('recreation', 4),
        'playground': ('recreation', 4),
        'pitch': ('recreation', 5),
        'track': ('recreation', 5),
        'picnic_table': ('utility', 5),
        'firepit': ('utility', 4),
    },
    'amenity': {
        '*': ('services', 4),
        'restaurant': ('food_drink', 3),
        'cafe': ('food_drink', 3),
        'fast_food': ('food_drink', 3),
        'bar': ('food_drink', 3),
        'pub': ('food_drink', 3),
        'biergarten': ('food_drink', 3),
        'ice_cream': ('food_drink', 3),
        'food_court': ('food_drink', 3),
        'fuel': ('automotive', 2),
        'charging_station': ('automotive', 2),
        'car_wash': ('automotive', 4),
        'car_rental': ('automotive', 3),
        'parking': ('automotive', 4),
        'bus_station': ('transport', 3),
        'ferry_terminal': ('transport', 2),
        'bicycle_rental': ('transport', 4),
        'taxi': ('transport', 4),
        'hospital': ('health', 2),
        'clinic': ('health', 3),
        'doctors': ('health', 3),
        'pharmacy': ('health', 3),
        'dentist': ('health', 4),
        'veterinary': ('health', 3),
        'townhall': ('civic', 3),
        'courthouse': ('civic', 3),
        'police': ('civic', 3),
        'fire_station': ('civic', 3),
        'library': ('civic', 3),
        'post_office': ('civic', 3),
        'community_centre': ('civic', 4),
        'place_of_worship': ('civic', 4),
        'social_facility': ('civic', 4),
        'polling_station': ('civic', 5),
        'school': ('civic', 4),
        'college': ('civic', 3),
        'university': ('civic', 3),
        'theatre': ('attraction', 3),
        'cinema': ('attraction', 3),
        'arts_centre': ('attraction', 3),
        'casino': ('attraction', 3),
        'bank': ('services', 3),
        'atm': ('services', 4),
        # Van-life essentials get deliberately generous ranks — these are the
        # things searched for while driving.
        'drinking_water': ('utility', 3),
        'water_point': ('utility', 2),
        'sanitary_dump_station': ('utility', 2),
        'toilets': ('utility', 3),
        'shower': ('utility', 3),
        'waste_disposal': ('utility', 4),
        'recycling': ('utility', 4),
        'shelter': ('utility', 4),
        'bbq': ('utility', 4),
        'fountain': ('utility', 4),
        'watering_place': ('utility', 4),
        'bench': ('utility', 5),
        'waste_basket': ('utility', 5),
        'vending_machine': ('utility', 5),
        'telephone': ('utility', 5),
        'post_box': ('utility', 5),
        'clock': ('utility', 5),
        'bicycle_parking': ('utility', 5),
        'letter_box': ('utility', 5),
        'loading_dock': ('utility', 5),
        'stadium_seating': ('utility', 5),
    },
    'healthcare': {
        '*': ('health', 4),
    },
    'shop': {
        '*': ('shopping', 3),
        'supermarket': ('grocery', 2),
        'convenience': ('grocery', 3),
        'bakery': ('grocery', 3),
        'butcher': ('grocery', 4),
        'greengrocer': ('grocery', 4),
        'deli': ('grocery', 4),
        'health_food': ('grocery', 4),
        'farm': ('grocery', 4),
        'wholesale': ('grocery', 3),
        'department_store': ('shopping', 2),
        'mall': ('shopping', 2),
        'laundry': ('utility', 3),
        'car_repair': ('automotive', 3),
        'car': ('automotive', 3),
        'car_parts': ('automotive', 4),
        'tyres': ('automotive', 4),
        'fuel': ('automotive', 3),
    },
    'craft': {
        '*': ('services', 4),
    },
    'office': {
        '*': ('services', 4),
    },
    'emergency': {
        '*': ('emergency', 4),
        'fire_hydrant': ('emergency', 5),
        'defibrillator': ('emergency', 5),
    },
    # Selective: landmark structures only — man_made at large is mass-mapped
    # infrastructure (comms towers, pipelines, survey markers).
    'man_made': {
        'lighthouse': ('landmark', 2),
        'windmill': ('landmark', 3),
        'watermill': ('landmark', 3),
        'obelisk': ('landmark', 3),
        'observatory': ('landmark', 2),
        'water_tap': ('utility', 4),
    },
    # Selective, and an addition beyond the plan's decision-1 key list:
    # airports are unambiguous destinations.
    'aeroway': {
        'aerodrome': ('transport', 1),
    },
}

# Mass-mapped non-places inside otherwise-included keys (the decision-1 floor).
# A match drops the element entirely — no fall-through to another key.
EXCLUDED_VALUES: dict[str, frozenset[str]] = {
    'amenity': frozenset({'parking_space', 'parking_entrance'}),
}

# Placeholder values that classify nothing — fall through to the next key.
# 'designated' is access-tag value misuse (seen on emergency=designated).
JUNK_VALUES = frozenset({'yes', 'no', 'fixme', 'vacant', 'disused', 'unknown', 'designated'})

_EMPTY: frozenset[str] = frozenset()


def filter_expressions() -> list[str]:
    """Derive the ``osmium tags-filter`` expressions from ``TAXONOMY``.

    Keys with a ``'*'`` default extract wholesale (``nwr/amenity``); selective
    keys extract only their listed values (``nwr/natural=peak,…``).

    Returns:
        One ``nwr/…`` expression per taxonomy key.
    """
    exprs: list[str] = []
    for key, table in TAXONOMY.items():
        if '*' in table:
            exprs.append(f'nwr/{key}')
        else:
            exprs.append(f'nwr/{key}=' + ','.join(v for v in table))
    return exprs


def classify(tags: Mapping[str, str]) -> tuple[str, str, int] | None:
    """Map an element's tags to ``(source_kind, category, rank)``.

    The first ``TAXONOMY`` key present in the tags (declaration order) is the
    primary tag; its value looks up in that key's table, falling back to the
    key's ``'*'`` default. Multi-values (``restaurant;cafe``) classify on the
    first entry.

    Args:
        tags: The element's OSM tags.

    Returns:
        ``(source_kind, category, rank)`` — kind is the primary ``key=value`` —
        or None when nothing classifies (or an excluded value matched).
    """
    for key, table in TAXONOMY.items():
        raw = tags.get(key)
        if not raw:
            continue
        value = raw.split(';', 1)[0].strip()
        if not value or value in JUNK_VALUES:
            continue
        if value in EXCLUDED_VALUES.get(key, _EMPTY):
            return None
        rule = table.get(value) or table.get('*')
        if rule is None:
            continue
        return f'{key}={value}', rule[0], rule[1]
    return None


# --- Row building ---------------------------------------------------------------------


@dataclass(frozen=True)
class PoiRow:
    """One extracted POI destined for the transfer DB's ``places`` table.

    Attributes:
        source_kind: Primary OSM tag as ``key=value`` (e.g. ``amenity=cafe``).
        source_id: OSM element ref (``node/…``/``way/…``/``relation/…``).
        name: Display name (tag name → brand/operator → humanized value).
        lat: Pin latitude (deg).
        lon: Pin longitude (deg).
        summary: Short teaser for list views, or None.
        details: Full OSM tag dict as JSON.
        category: Unified taxonomy category.
        rank: Pin-zoom tier (1 major … 5 search-only).
    """

    source_kind: str
    source_id: str
    name: str
    lat: float
    lon: float
    summary: str | None
    details: str
    category: str
    rank: int


def humanize(value: str) -> str:
    """Turn an OSM tag value into display text (``fast_food`` → ``Fast food``)."""
    text = value.replace('_', ' ').strip()
    return text[:1].upper() + text[1:]


def display_name(tags: Mapping[str, str], value: str) -> str:
    """Pick a display name: name tags, then brand/operator, then the tag value.

    Unnamed micro furniture stays searchable by what it is ("Drinking water");
    unnamed chain amenities surface their brand (an unnamed Shell fuel node).

    Args:
        tags: The element's OSM tags.
        value: The classified primary-tag value (the humanized fallback).

    Returns:
        A non-empty display name.
    """
    for key in ('name', 'name:en', 'brand', 'operator'):
        text = (tags.get(key) or '').strip()
        if text:
            return text
    return humanize(value)


def build_summary(tags: Mapping[str, str], kind: str, name: str) -> str | None:
    """Compose the list-view teaser from kind label, cuisine, and brand.

    Args:
        tags: The element's OSM tags.
        kind: The ``key=value`` source kind.
        name: The chosen display name (a brand equal to it is not repeated).

    Returns:
        E.g. ``'Fast food · Burgers · Five Guys'``, or None if empty.
    """
    parts = [humanize(kind.split('=', 1)[1])]
    cuisine = tags.get('cuisine')
    if cuisine:
        parts.append(humanize(cuisine.replace(';', ', ')))
    brand = (tags.get('brand') or '').strip()
    if brand and brand != name:
        parts.append(brand)
    summary = ' · '.join(p for p in parts if p)
    return summary or None


# --- Extraction -----------------------------------------------------------------------


@dataclass
class Stats:
    """Counters accumulated over an extraction run (drives the dry-run report)."""

    kept: int = 0
    named: int = 0
    by_category: Counter[str] = field(default_factory=Counter)
    by_kind: Counter[tuple[str, str]] = field(default_factory=Counter)
    defaulted: Counter[str] = field(default_factory=Counter)
    dropped: Counter[str] = field(default_factory=Counter)
    unmatched: Counter[str] = field(default_factory=Counter)


def _way_point(way: osmium.osm.Way) -> tuple[float, float] | None:
    """Average of a linear way's node locations, or None if none are cached."""
    lats: list[float] = []
    lons: list[float] = []
    for node in way.nodes:
        if node.location.valid():
            lats.append(node.location.lat)
            lons.append(node.location.lon)
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _area_point(area: osmium.osm.Area) -> tuple[float, float] | None:
    """Average of an area's outer-ring vertices, or None if none are cached.

    The rings' closing vertices are counted twice — negligible for a pin.
    """
    lats: list[float] = []
    lons: list[float] = []
    for ring in area.outer_rings():
        for node in ring:
            if node.location.valid():
                lats.append(node.location.lat)
                lons.append(node.location.lon)
    if not lats:
        return None
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _first_primary(tags: Mapping[str, str]) -> str:
    """The first taxonomy ``key=value`` present, for the unmatched report."""
    for key in TAXONOMY:
        if tags.get(key):
            return f'{key}={tags[key]}'
    return '(none)'


def iter_pois(
    path: Path, bbox: tuple[float, float, float, float] | None, stats: Stats
) -> Iterator[PoiRow]:
    """Stream classified POI rows out of a (prefiltered) PBF.

    Nodes pin at their location; linear ways at their vertex average; closed
    ways and multipolygon relations arrive as assembled areas (closed ways are
    skipped in way form so nothing double-counts) and pin at their outer-ring
    vertex average.

    Args:
        path: A PBF, normally the ``tags-filter`` output.
        bbox: Optional ``(min_lon, min_lat, max_lon, max_lat)`` clip.
        stats: Counters to accumulate into.

    Yields:
        One :class:`PoiRow` per classified element.
    """
    fp = (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY | osmium.osm.AREA)
        .with_areas()
        .with_filter(osmium.filter.KeyFilter(*TAXONOMY.keys()))
    )
    started = time.monotonic()
    seen = 0
    for obj in fp:
        seen += 1
        if seen % PROGRESS_EVERY == 0:
            rate = seen / max(time.monotonic() - started, 1e-9)
            print(
                f'  … {seen:,} elements scanned, {stats.kept:,} kept ({rate:,.0f}/s)',
                file=sys.stderr,
            )
        if isinstance(obj, osmium.osm.Way):
            if obj.is_closed():
                continue  # arrives again as an assembled area
            ref = f'way/{obj.id}'
        elif isinstance(obj, osmium.osm.Area):
            kind_ = 'way' if obj.from_way() else 'relation'
            ref = f'{kind_}/{obj.orig_id()}'
        elif isinstance(obj, osmium.osm.Node):
            ref = f'node/{obj.id}'
        else:
            continue

        tags = {t.k: t.v for t in obj.tags}
        classified = classify(tags)
        if classified is None:
            stats.dropped['unclassified'] += 1
            stats.unmatched[_first_primary(tags)] += 1
            continue
        kind, category, rank = classified

        if isinstance(obj, osmium.osm.Node):
            point = (obj.location.lat, obj.location.lon) if obj.location.valid() else None
        elif isinstance(obj, osmium.osm.Area):
            point = _area_point(obj)
        else:
            point = _way_point(obj)
        if point is None:
            stats.dropped['no_location'] += 1
            continue
        lat, lon = point
        if bbox is not None and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            stats.dropped['outside_bbox'] += 1
            continue

        name = display_name(tags, kind.split('=', 1)[1])
        stats.kept += 1
        if 'name' in tags:
            stats.named += 1
        stats.by_category[category] += 1
        stats.by_kind[category, kind] += 1
        key, value = kind.split('=', 1)
        if value not in TAXONOMY[key]:
            stats.defaulted[kind] += 1
        yield PoiRow(
            source_kind=kind,
            source_id=ref,
            name=name,
            lat=round(lat, 7),
            lon=round(lon, 7),
            summary=build_summary(tags, kind, name),
            details=json.dumps(tags, ensure_ascii=False),
            category=category,
            rank=rank,
        )


# --- Prefilter ------------------------------------------------------------------------


def filtered_path(src: Path) -> Path:
    """The prefilter output path for a source PBF, expression-hash suffixed.

    The hash makes reuse safe: editing the taxonomy changes the expressions,
    which changes the filename, so a stale prefilter is never picked up.
    """
    digest = hashlib.sha1('\n'.join(filter_expressions()).encode()).hexdigest()[:8]
    base = src.name.removesuffix('.pbf').removesuffix('.osm')
    return src.with_name(f'{base}.pois-{digest}.osm.pbf')


def prefilter(src: Path) -> Path:
    """Run ``osmium tags-filter`` on a source PBF (or reuse a fresh output).

    Args:
        src: The raw Geofabrik extract.

    Returns:
        The POI-only PBF path.

    Raises:
        RuntimeError: If ``osmium`` is not on PATH.
        subprocess.CalledProcessError: If the filter fails.
    """
    if shutil.which('osmium') is None:
        raise RuntimeError('osmium not found — install osmium-tool (brew install osmium-tool)')
    dst = filtered_path(src)
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        print(f'Reusing prefiltered {dst.name}', file=sys.stderr)
        return dst
    print(f'Prefiltering {src.name} → {dst.name}', file=sys.stderr)
    subprocess.run(
        ['osmium', 'tags-filter', str(src), *filter_expressions(), '-o', str(dst), '--overwrite'],
        check=True,
    )
    return dst


# --- Transfer DB ----------------------------------------------------------------------

# Column-compatible with places_db.places (api.db._init_places_schema) plus the
# broad-POI columns the sidecar gains at merge time (Phase 2): category + rank.
TRANSFER_SCHEMA = """
    CREATE TABLE places (
        source      TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        source_id   TEXT NOT NULL,
        park_code   TEXT,
        name        TEXT NOT NULL,
        lat         REAL,
        lon         REAL,
        summary     TEXT,
        details     TEXT NOT NULL,
        synced_at   TEXT NOT NULL,
        category    TEXT NOT NULL,
        rank        INTEGER NOT NULL,
        UNIQUE (source, source_id)
    )
"""


def write_transfer_db(out: Path, rows: Iterator[PoiRow], synced_at: str) -> int:
    """Write extracted rows to a fresh transfer DB.

    Durability pragmas are off — the file is a rebuildable build product. The
    unique key dedupes overlap between adjacent Geofabrik extracts (first
    occurrence wins; the duplicates carry identical tags).

    Args:
        out: Transfer DB path (its ``places`` table is replaced).
        rows: The extraction stream.
        synced_at: Canonical build timestamp stamped on every row.

    Returns:
        Total rows in the finished table.
    """
    conn = sqlite3.connect(out)
    try:
        conn.execute('PRAGMA journal_mode = OFF')
        conn.execute('PRAGMA synchronous = OFF')
        conn.execute('DROP TABLE IF EXISTS places')
        conn.execute(TRANSFER_SCHEMA)
        batch: list[tuple[Any, ...]] = []

        def flush() -> None:
            conn.executemany(
                'INSERT OR IGNORE INTO places (source, source_kind, source_id, park_code, '
                'name, lat, lon, summary, details, synced_at, category, rank) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                batch,
            )
            conn.commit()
            batch.clear()

        for row in rows:
            batch.append(
                (
                    SOURCE,
                    row.source_kind,
                    row.source_id,
                    None,
                    row.name,
                    row.lat,
                    row.lon,
                    row.summary,
                    row.details,
                    synced_at,
                    row.category,
                    row.rank,
                )
            )
            if len(batch) >= BATCH_ROWS:
                flush()
        flush()
        total = conn.execute('SELECT COUNT(*) FROM places').fetchone()[0]
        return int(total)
    finally:
        conn.close()


# --- Reporting ------------------------------------------------------------------------


def print_report(stats: Stats) -> None:
    """Print the per-category table plus the taxonomy-tuning signals."""
    print(f'\nKept {stats.kept:,} POIs ({stats.named:,} with a proper name tag)')
    print(f'{"category":<12} {"rows":>10}   top kinds')
    for category, count in stats.by_category.most_common():
        kinds = Counter({k: n for (cat, k), n in stats.by_kind.items() if cat == category})
        tops = ' · '.join(f'{kind.split("=", 1)[1]} {n:,}' for kind, n in kinds.most_common(4))
        print(f'{category:<12} {count:>10,}   {tops}')
    if stats.defaulted:
        print('\nAbsorbed by key-level defaults (top 25 — promote or exclude as needed):')
        for kind, count in stats.defaulted.most_common(25):
            print(f'  {kind:<40} {count:>8,}')
    if stats.dropped:
        drops = ' · '.join(f'{reason} {n:,}' for reason, n in stats.dropped.most_common())
        print(f'\nDropped: {drops}')
    if stats.unmatched:
        print('Top unclassified primary tags (excluded/junk/selective-miss):')
        for kind, count in stats.unmatched.most_common(15):
            print(f'  {kind:<40} {count:>8,}')


# --- CLI ------------------------------------------------------------------------------


def parse_bbox(text: str) -> tuple[float, float, float, float]:
    """Parse ``min_lon,min_lat,max_lon,max_lat`` into a bbox tuple."""
    parts = [float(p) for p in text.split(',')]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError('bbox must be min_lon,min_lat,max_lon,max_lat')
    return parts[0], parts[1], parts[2], parts[3]


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('pbf', nargs='+', type=Path, help='Geofabrik extract(s)')
    parser.add_argument(
        '-o', '--out', type=Path, default=Path('osm-places.db'), help='transfer DB output path'
    )
    parser.add_argument(
        '--dry-run', action='store_true', help='extract + report counts; write no DB'
    )
    parser.add_argument(
        '--filtered',
        action='store_true',
        help='inputs are already tags-filtered PBFs — skip the osmium prefilter',
    )
    parser.add_argument(
        '--bbox',
        type=parse_bbox,
        default=BASEMAP_BBOX,
        help='clip to min_lon,min_lat,max_lon,max_lat (default: the basemap bbox)',
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> int:
    """Drive prefilter → extraction → transfer DB (or dry-run report)."""
    for src in args.pbf:
        if not src.exists():
            print(f'No such file: {src}', file=sys.stderr)
            return 1
    inputs = [src if args.filtered else prefilter(src) for src in args.pbf]

    stats = Stats()
    started = time.monotonic()

    def rows() -> Iterator[PoiRow]:
        for path in inputs:
            print(f'Extracting {path.name}', file=sys.stderr)
            yield from iter_pois(path, args.bbox, stats)

    if args.dry_run:
        for _ in rows():
            pass
    else:
        total = write_transfer_db(args.out, rows(), now_canonical())
        size_mb = args.out.stat().st_size / 1e6
        print(f'\nWrote {total:,} rows → {args.out} ({size_mb:,.0f} MB)', file=sys.stderr)

    print_report(stats)
    print(f'\nElapsed: {time.monotonic() - started:,.0f}s', file=sys.stderr)
    return 0


def main() -> None:
    """CLI entrypoint."""
    sys.exit(run(parse_args()))


if __name__ == '__main__':
    run_cli(main)
