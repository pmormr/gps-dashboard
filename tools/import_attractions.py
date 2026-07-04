"""Sync attractions sources (NPS API, RIDB export) into ``gps_history.db``.

Batch importer for the attractions tier (see ``plans/attractions-plan.md``).
Two mutually exclusive modes, each full-replacing its own ``source`` slice of
``attractions`` / ``attraction_events`` / ``attraction_event_dates``:

* **NPS** (default) — walks the NPS API (``developer.nps.gov``) while the Pi
  has WAN, so parks, self-guided tours, things to do, facility hours, and
  event schedules are all browsable offline.
* **RIDB** (``--ridb-zip``) — parses the RIDB full CSV export
  (``ridb.recreation.gov/downloads/RIDBFullExport_V1_CSV.zip``, ~245 MB,
  regenerated ~nightly, no API key) into ``source='ridb'`` rows: the other
  federal agencies' places (FS/USACE/BLM/FWS… trailheads, campgrounds,
  visitor centers, rec areas). Offline once the zip is local.

NPS shape notes that drive the code:

* POI endpoints (``parks``/``thingstodo``/``tours``/``visitorcenters``/
  ``campgrounds``) page with ``limit``/``start`` (0-based); ``events`` pages with
  ``pagesize``/``pagenumber`` (**1-based**).
* Coordinates arrive as strings and are sometimes empty; kinds without their own
  coordinate fall back to their park's. Tour stops carry no coordinates — they
  reference ``places`` assets by id, so ``places`` is fetched purely as a
  coordinate/name join (places are not stored as rows — an open plan item).
* ``events`` carry a pre-expanded ``dates`` array (concrete upcoming dates), so
  no RRULE parsing: each date × time window becomes one ``attraction_event_dates``
  row. Dates are park-local calendar dates, kept as published — not ms-UTC.
* The events feed can repeat an id across pages; rows dedupe on the source GUID.

RIDB shape notes:

* The export has **no operating-hours table** (the Phase-0 plan assumed one);
  descriptions/directions/fee text/phone are what exists. Facilities carry a
  free-text ``FacilityTypeDescription`` → mapped to our kind vocabulary
  (``campground``/``visitorcenter`` reused; ``facility`` = the generic
  trailhead/site type; ``permit`` = Permit + Timed Entry, kept as planning
  signals). Pure reservation products (Activity Pass, Tree Permit, Ticket
  Facility, Venue Reservations) are excluded — recreation.gov purchase
  constructs, not places.
* NPS-org rows (org id 128, via ``OrgEntities``) are excluded entirely — the
  richer native NPS source already covers them.
* Rec areas import as kind ``recarea`` (the park-analog container); for RIDB
  rows ``park_code`` is the owning ``RecAreaID`` (numeric strings — no
  collision with NPS alpha codes). Facilities without a coordinate fall back
  to their rec area's; blank or ``0.0`` coordinates are treated as absent.
* Individual campsites do NOT become rows; they aggregate per campground into
  ``details.campsites`` (site count + per-equipment max vehicle length, feet —
  the "can my van fit" field).

The NPS API key resolves from ``--api-key``, then ``$NPS_API_KEY``, then
``/etc/default/gps-attractions`` (the project's usual secret-file pattern), so a
plain SSH invocation on the Pi needs no env sourcing. RIDB mode needs no key.

Examples::

    # Pi: sync NPS into the live DB (key read from /etc/default/gps-attractions)
    GPS_DB_PATH=/mnt/nvme/data/gps_history.db uv run tools/import_attractions.py

    # Pi: import a previously scp'd RIDB export
    GPS_DB_PATH=/mnt/nvme/data/gps_history.db \\
        uv run tools/import_attractions.py --ridb-zip /mnt/nvme/data/ridb.zip

    # Dev: fetch + report only
    NPS_API_KEY=... uv run tools/import_attractions.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from api.db import get_connection, init_db, now_canonical
from common.cli import run_cli

API_ROOT = 'https://developer.nps.gov/api/v1'
DEFAULT_ENV_FILE = Path('/etc/default/gps-attractions')
PAGE_SIZE = 200
EVENT_PAGE_SIZE = 100
RETRIES = 3
SUMMARY_CHARS = 300

# Response keys that never earn their bytes offline (search-engine scores,
# video/audio manifests, passport-stamp art). Images are trimmed, not dropped.
_DROP_KEYS = frozenset(
    {'relevanceScore', 'multimedia', 'passportStampImages', 'geometryPoiId', 'imageidlist'}
)

SOURCE = 'nps'


@dataclass(frozen=True)
class Attraction:
    """One POI row destined for ``attractions``.

    Attributes:
        source_kind: The NPS kind (``park``/``thingstodo``/``tour``/
            ``visitorcenter``/``campground``).
        source_id: The NPS GUID (unique per source).
        park_code: Owning park's code (e.g. ``romo``), or None.
        name: Display name.
        lat: Latitude (deg), or None when the source has no coordinate.
        lon: Longitude (deg), or None.
        summary: Short plain-text teaser for list views.
        details: Trimmed source record (display-only structure), stored as JSON.
    """

    source_kind: str
    source_id: str
    park_code: str | None
    name: str
    lat: float | None
    lon: float | None
    summary: str | None
    details: dict[str, Any]


@dataclass(frozen=True)
class EventDate:
    """One concrete event occurrence (a date × time window).

    Attributes:
        date: Park-local calendar date, ``YYYY-MM-DD`` as published.
        time_start: 24h ``HH:MM`` start, or None (all-day / unspecified).
        time_end: 24h ``HH:MM`` end, or None.
    """

    date: str
    time_start: str | None
    time_end: str | None


@dataclass(frozen=True)
class Event:
    """One scheduled event destined for ``attraction_events`` (+ its dates).

    Attributes:
        source_id: The NPS event GUID.
        park_code: Owning park's code (the event ``sitecode``), or None.
        name: Event title.
        lat: Latitude (deg), or None.
        lon: Longitude (deg), or None.
        location_text: Meeting-point description, or None.
        is_free: Whether attendance is free, or None when unstated.
        needs_reservation: Whether registration/reservation is required, or None.
        dates: The expanded occurrences.
        details: Trimmed source record, stored as JSON.
    """

    source_id: str
    park_code: str | None
    name: str
    lat: float | None
    lon: float | None
    location_text: str | None
    is_free: bool | None
    needs_reservation: bool | None
    dates: tuple[EventDate, ...]
    details: dict[str, Any]


# --- Field coercion --------------------------------------------------------------


def _coord(value: Any) -> float | None:
    """Coerce an NPS coordinate (string, number, or empty) to a float or None.

    Args:
        value: The raw ``latitude``/``longitude`` field.

    Returns:
        The coordinate in decimal degrees, or None when absent/blank/malformed.
    """
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    """Coerce an NPS boolean (real bool or ``'true'``/``'false'`` string) or None.

    Args:
        value: The raw field value.

    Returns:
        The boolean, or None when absent or unrecognizable.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    return None


def strip_tags(text: str) -> str:
    """Drop HTML tags and collapse whitespace (event descriptions embed markup).

    Args:
        text: Possibly-HTML source text.

    Returns:
        Plain text with single spaces.
    """
    plain = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip()
    return re.sub(r'\s+([.,;:!?])', r'\1', plain)


def summarize(text: Any, limit: int = SUMMARY_CHARS) -> str | None:
    """Build the plain-text list-view teaser from a source description.

    Args:
        text: The raw description (may be HTML, empty, or absent).
        limit: Maximum characters, cut on a word boundary with an ellipsis.

    Returns:
        The teaser, or None when there is no usable text.
    """
    if not text or not isinstance(text, str):
        return None
    plain = strip_tags(text)
    if not plain:
        return None
    if len(plain) <= limit:
        return plain
    return plain[:limit].rsplit(' ', 1)[0] + '…'


def parse_ampm(value: Any) -> str | None:
    """Convert an NPS ``'07:30 AM'`` time string to 24h ``'07:30'``.

    Args:
        value: The raw ``timestart``/``timeend`` field.

    Returns:
        The 24h ``HH:MM`` string, or None when absent/malformed.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), '%I:%M %p').strftime('%H:%M')
    except ValueError:
        return None


def _trim_details(record: dict[str, Any]) -> dict[str, Any]:
    """Copy a source record minus dead-weight keys, with images slimmed to essentials.

    Args:
        record: The raw API record.

    Returns:
        The details dict to persist as JSON.
    """
    details = {k: v for k, v in record.items() if k not in _DROP_KEYS}
    images = record.get('images')
    if isinstance(images, list):
        details['images'] = [
            {key: img.get(key) for key in ('url', 'title', 'caption')}
            for img in images
            if isinstance(img, dict)
        ]
    return details


# --- Per-kind transforms -----------------------------------------------------------


def parse_park(record: dict[str, Any]) -> Attraction:
    """Transform one ``parks`` record.

    Args:
        record: The raw API record.

    Returns:
        The park as an :class:`Attraction`.
    """
    return Attraction(
        source_kind='park',
        source_id=record['id'],
        park_code=record.get('parkCode'),
        name=record.get('fullName') or record.get('name') or '',
        lat=_coord(record.get('latitude')),
        lon=_coord(record.get('longitude')),
        summary=summarize(record.get('description')),
        details=_trim_details(record),
    )


def parse_thingstodo(record: dict[str, Any]) -> Attraction:
    """Transform one ``thingstodo`` record (park code via ``relatedParks``).

    Args:
        record: The raw API record.

    Returns:
        The activity as an :class:`Attraction` (coords may need park fallback).
    """
    related = record.get('relatedParks') or []
    park_code = related[0].get('parkCode') if related else None
    return Attraction(
        source_kind='thingstodo',
        source_id=record['id'],
        park_code=park_code,
        name=record.get('title') or '',
        lat=_coord(record.get('latitude')),
        lon=_coord(record.get('longitude')),
        summary=summarize(record.get('shortDescription') or record.get('longDescription')),
        details=_trim_details(record),
    )


def parse_tour(
    record: dict[str, Any], places: dict[str, tuple[float | None, float | None]]
) -> Attraction:
    """Transform one ``tours`` record, resolving stop coordinates via ``places``.

    Tours carry no coordinates of their own; each stop references a ``places``
    asset by id. Stops get ``lat``/``lon`` embedded (mappable offline) and the
    tour pins at its first located stop, falling back to the park later.

    Args:
        record: The raw API record.
        places: ``place id → (lat, lon)`` from the ``places`` endpoint.

    Returns:
        The tour as an :class:`Attraction`.
    """
    park = record.get('park') or {}
    details = _trim_details(record)
    lat = lon = None
    stops = []
    for stop in sorted(record.get('stops') or [], key=lambda s: int(s.get('ordinal') or 0)):
        stop_lat, stop_lon = places.get(stop.get('assetId', ''), (None, None))
        stops.append({**stop, 'lat': stop_lat, 'lon': stop_lon})
        if lat is None and stop_lat is not None:
            lat, lon = stop_lat, stop_lon
    details['stops'] = stops
    return Attraction(
        source_kind='tour',
        source_id=record['id'],
        park_code=park.get('parkCode'),
        name=record.get('title') or '',
        lat=lat,
        lon=lon,
        summary=summarize(record.get('description')),
        details=details,
    )


def parse_facility(kind: str, record: dict[str, Any]) -> Attraction:
    """Transform one ``visitorcenters``/``campgrounds`` record.

    Args:
        kind: ``'visitorcenter'`` or ``'campground'``.
        record: The raw API record.

    Returns:
        The facility as an :class:`Attraction` (operating hours ride in details).
    """
    return Attraction(
        source_kind=kind,
        source_id=record['id'],
        park_code=record.get('parkCode'),
        name=record.get('name') or '',
        lat=_coord(record.get('latitude')),
        lon=_coord(record.get('longitude')),
        summary=summarize(record.get('description')),
        details=_trim_details(record),
    )


def parse_event(record: dict[str, Any]) -> Event:
    """Transform one ``events`` record, expanding its occurrence list.

    Each published date crosses with each time window (the ``times`` list is
    event-level); a date with no parseable times still yields one row with NULL
    times, so all-day and unspecified events stay queryable by date.

    Args:
        record: The raw API record.

    Returns:
        The :class:`Event` with expanded :class:`EventDate` rows.
    """
    dates: list[str] = record.get('dates') or []
    if not dates and record.get('datestart'):
        dates = [record['datestart']]
    windows = [
        (parse_ampm(t.get('timestart')), parse_ampm(t.get('timeend')))
        for t in (record.get('times') or [])
        if isinstance(t, dict)
    ]
    windows = [w for w in windows if w != (None, None)] or [(None, None)]
    occurrences = tuple(EventDate(date, start, end) for date in dates for (start, end) in windows)
    return Event(
        source_id=record['id'],
        park_code=(record.get('sitecode') or None),
        name=record.get('title') or '',
        lat=_coord(record.get('latitude')),
        lon=_coord(record.get('longitude')),
        location_text=(record.get('location') or None),
        is_free=_bool(record.get('isfree')),
        needs_reservation=_bool(record.get('isregresrequired')),
        dates=occurrences,
        details=_trim_details(record),
    )


def apply_park_fallback(attractions: list[Attraction]) -> list[Attraction]:
    """Fill missing coordinates from the owning park's.

    Args:
        attractions: All parsed rows (parks included).

    Returns:
        The rows with ``lat``/``lon`` backfilled where the park is located.
    """
    park_coords = {
        a.park_code: (a.lat, a.lon)
        for a in attractions
        if a.source_kind == 'park' and a.park_code and a.lat is not None
    }
    return [
        replace(a, lat=park_coords[a.park_code][0], lon=park_coords[a.park_code][1])
        if a.lat is None and a.park_code in park_coords
        else a
        for a in attractions
    ]


def dedupe_by_source_id(rows: list[Any]) -> list[Any]:
    """Drop repeated source ids, keeping first occurrence (events repeat across pages).

    Args:
        rows: Parsed :class:`Attraction` or :class:`Event` rows.

    Returns:
        The rows with unique ``source_id``.
    """
    seen: set[str] = set()
    unique = []
    for row in rows:
        if row.source_id in seen:
            continue
        seen.add(row.source_id)
        unique.append(row)
    return unique


# --- Fetch -------------------------------------------------------------------------


def _get_json(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET one API page with retries; raise on an error payload.

    Args:
        session: The shared HTTP session.
        url: Full endpoint URL.
        params: Query params (including the api key).

    Returns:
        The decoded response body.

    Raises:
        RuntimeError: On an API-level error payload (e.g. rate limit).
        requests.RequestException: When all attempts fail at transport level.
    """
    last_exc: requests.RequestException | None = None
    for attempt in range(RETRIES):
        try:
            response = session.get(url, params=params, timeout=60)
            response.raise_for_status()
            body: dict[str, Any] = response.json()
            if 'error' in body:
                raise RuntimeError(f'NPS API error: {body["error"]}')
            return body
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def fetch_paged(session: requests.Session, endpoint: str, api_key: str) -> list[dict[str, Any]]:
    """Walk a standard ``limit``/``start``-paged endpoint to exhaustion.

    Args:
        session: The shared HTTP session.
        endpoint: Endpoint name (e.g. ``'parks'``).
        api_key: The NPS api key.

    Returns:
        Every record the endpoint serves.
    """
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        body = _get_json(
            session,
            f'{API_ROOT}/{endpoint}',
            {'limit': PAGE_SIZE, 'start': start, 'api_key': api_key},
        )
        data = body.get('data') or []
        rows.extend(data)
        start += len(data)
        if not data or start >= int(body.get('total') or 0):
            return rows


def fetch_events(session: requests.Session, api_key: str) -> list[dict[str, Any]]:
    """Walk the ``events`` endpoint (``pagesize``/``pagenumber``, 1-based).

    Args:
        session: The shared HTTP session.
        api_key: The NPS api key.

    Returns:
        Every event record served.
    """
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        body = _get_json(
            session,
            f'{API_ROOT}/events',
            {'pagesize': EVENT_PAGE_SIZE, 'pagenumber': page, 'api_key': api_key},
        )
        data = body.get('data') or []
        rows.extend(data)
        page += 1
        if not data or len(rows) >= int(body.get('total') or 0):
            return rows


# --- RIDB (full CSV export) ----------------------------------------------------------

RIDB_SOURCE = 'ridb'
RIDB_NPS_ORG_ID = '128'
RIDB_KIND_BY_TYPE = {
    'Campground': 'campground',
    'Visitor Center': 'visitorcenter',
    'Permit': 'permit',
    'Timed Entry': 'permit',
}
RIDB_TYPE_EXCLUDE = frozenset(
    {'Activity Pass', 'Tree Permit', 'Ticket Facility', 'Venue Reservations'}
)


@dataclass
class RidbExport:
    """The joined-up slice of the RIDB export the transform needs.

    Attributes:
        recareas: Raw ``RecAreas`` records (CSV string fields).
        facilities: Raw ``Facilities`` records.
        recarea_orgs: ``RecAreaID → OrgID`` (via ``OrgEntities``).
        facility_orgs: ``FacilityID → OrgID``.
        org_names: ``OrgID → abbreviated org name`` (e.g. ``FS``, ``BLM``).
        facility_recarea: ``FacilityID → RecAreaID`` (first link wins).
        facility_address: ``FacilityID → (city, state)``.
        recarea_activities: ``RecAreaID → sorted activity names``.
        facility_activities: ``FacilityID → sorted activity names``.
        campsites: ``FacilityID → details.campsites dict`` (count + equipment).
    """

    recareas: list[dict[str, str]] = field(default_factory=list)
    facilities: list[dict[str, str]] = field(default_factory=list)
    recarea_orgs: dict[str, str] = field(default_factory=dict)
    facility_orgs: dict[str, str] = field(default_factory=dict)
    org_names: dict[str, str] = field(default_factory=dict)
    facility_recarea: dict[str, str] = field(default_factory=dict)
    facility_address: dict[str, tuple[str, str]] = field(default_factory=dict)
    recarea_activities: dict[str, list[str]] = field(default_factory=dict)
    facility_activities: dict[str, list[str]] = field(default_factory=dict)
    campsites: dict[str, dict[str, Any]] = field(default_factory=dict)


def _ridb_label(name: str) -> str:
    """Title-case an ALL-CAPS RIDB label, keeping ``RV`` an acronym.

    Args:
        name: The raw label (e.g. ``'PICKUP CAMPER'``, ``'RV/MOTORHOME'``).

    Returns:
        The display label (``'Pickup Camper'``, ``'RV/Motorhome'``).
    """
    return re.sub(r'\bRv\b', 'RV', name.strip().title())


def _ridb_coord(value: str | None) -> float | None:
    """Coerce a RIDB coordinate; blank and ``0.0`` (Null Island) are absent.

    Args:
        value: The raw CSV coordinate field.

    Returns:
        The coordinate in decimal degrees, or None.
    """
    coord = _coord(value)
    return None if coord == 0.0 else coord


def _ridb_rows(zf: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    """Stream one CSV member of the export as dict rows.

    Args:
        zf: The open export archive.
        name: Member name (e.g. ``'Facilities_API_v1.csv'``).

    Yields:
        One record per CSV row.
    """
    with zf.open(name) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig', newline=''))


def read_ridb_zip(path: Path) -> RidbExport:
    """Read and join the needed members of the RIDB full export.

    Streams each CSV once; campsites and permitted equipment (the two big
    member files) collapse to per-campground aggregates on the fly.

    Args:
        path: The downloaded ``RIDBFullExport_V1_CSV.zip``.

    Returns:
        The joined export slice.
    """
    export = RidbExport()
    with zipfile.ZipFile(path) as zf:
        for org in _ridb_rows(zf, 'Organizations_API_v1.csv'):
            export.org_names[org['OrgID']] = org['OrgAbbrevName'] or org['OrgName']
        for link in _ridb_rows(zf, 'OrgEntities_API_v1.csv'):
            target = (
                export.recarea_orgs if link['EntityType'] == 'Rec Area' else export.facility_orgs
            )
            target.setdefault(link['EntityID'], link['OrgID'])
        for link in _ridb_rows(zf, 'RecAreaFacilities_API_v1.csv'):
            export.facility_recarea.setdefault(link['FacilityID'], link['RecAreaID'])

        # Physical > Default > Mailing; first of the best rank wins.
        address_rank = {'Physical': 0, 'Default': 1, 'Mailing': 2}
        best_rank: dict[str, int] = {}
        for addr in _ridb_rows(zf, 'FacilityAddresses_API_v1.csv'):
            rank = address_rank.get(addr['FacilityAddressType'], 3)
            fac_id = addr['FacilityID']
            if rank < best_rank.get(fac_id, 4) and (addr['City'] or addr['AddressStateCode']):
                best_rank[fac_id] = rank
                export.facility_address[fac_id] = (
                    addr['City'].strip(),
                    addr['AddressStateCode'].strip(),
                )

        activity_names = {
            a['ActivityID']: _ridb_label(a['ActivityName'])
            for a in _ridb_rows(zf, 'Activities_API_v1.csv')
        }
        activity_sets: dict[tuple[bool, str], set[str]] = {}
        for act in _ridb_rows(zf, 'EntityActivities_API_v1.csv'):
            name = act['ActivityDescription'].strip() or activity_names.get(act['ActivityID'], '')
            if not name:
                continue
            key = (act['EntityType'] == 'Rec Area', act['EntityID'])
            activity_sets.setdefault(key, set()).add(name)
        for (is_recarea, entity_id), names in activity_sets.items():
            target_acts = export.recarea_activities if is_recarea else export.facility_activities
            target_acts[entity_id] = sorted(names)

        site_facility: dict[str, str] = {}
        for site in _ridb_rows(zf, 'Campsites_API_v1.csv'):
            site_facility[site['CampsiteID']] = site['FacilityID']
            agg = export.campsites.setdefault(site['FacilityID'], {'count': 0, 'equipment': {}})
            agg['count'] += 1
        for equip in _ridb_rows(zf, 'PermittedEquipment_API_v1.csv'):
            site_fac = site_facility.get(equip['CampsiteID'])
            if site_fac is None:
                continue
            lengths = export.campsites[site_fac]['equipment']
            length = _coord(equip['MaxLength']) or 0.0
            name = _ridb_label(equip['EquipmentName'])
            lengths[name] = max(lengths.get(name, 0.0), length)

        export.recareas = list(_ridb_rows(zf, 'RecAreas_API_v1.csv'))
        export.facilities = list(_ridb_rows(zf, 'Facilities_API_v1.csv'))
    return export


def _ridb_details(pairs: dict[str, Any]) -> dict[str, Any]:
    """Build a details dict, dropping empty CSV values.

    Args:
        pairs: Candidate key → raw value pairs.

    Returns:
        The dict with blank/None/empty values removed.
    """
    return {k: v for k, v in pairs.items() if v not in (None, '', [], {})}


def _campsites_detail(agg: dict[str, Any] | None) -> dict[str, Any] | None:
    """Shape one campground's campsite aggregate for the details JSON.

    Args:
        agg: The raw aggregate (``count`` + ``equipment`` name → max length).

    Returns:
        ``{'count': n, 'equipment': [{'name': …, 'maxLengthFt': …}]}`` with
        zero lengths omitted (length-irrelevant gear like tents), or None.
    """
    if not agg:
        return None
    equipment = [
        {'name': name, **({'maxLengthFt': length} if length else {})}
        for name, length in sorted(agg['equipment'].items())
    ]
    return {'count': agg['count'], 'equipment': equipment}


def parse_ridb_recarea(record: dict[str, str], export: RidbExport) -> Attraction:
    """Transform one ``RecAreas`` record (the park-analog container row).

    Args:
        record: The raw CSV record.
        export: The joined export (org + activity lookups).

    Returns:
        The rec area as an :class:`Attraction` (kind ``recarea``; its own
        ``RecAreaID`` as ``park_code``, like NPS parks carry their park code).
    """
    recarea_id = record['RecAreaID']
    org_id = export.recarea_orgs.get(recarea_id)
    details = _ridb_details(
        {
            'description': record.get('RecAreaDescription'),
            'directions': record.get('RecAreaDirections'),
            'feeText': record.get('RecAreaUseFeeDescription'),
            'phone': record.get('RecAreaPhone'),
            'email': record.get('RecAreaEmail'),
            'reservationUrl': record.get('RecAreaReservationURL'),
            'mapUrl': record.get('RecAreaMapURL'),
            'keywords': record.get('Keywords'),
            'stayLimit': record.get('StayLimit'),
            'org': export.org_names.get(org_id or ''),
            'activities': export.recarea_activities.get(recarea_id),
        }
    )
    return Attraction(
        source_kind='recarea',
        source_id=f'ra:{recarea_id}',
        park_code=recarea_id,
        name=(record.get('RecAreaName') or '').strip(),
        lat=_ridb_coord(record.get('RecAreaLatitude')),
        lon=_ridb_coord(record.get('RecAreaLongitude')),
        summary=summarize(record.get('RecAreaDescription')),
        details=details,
    )


def parse_ridb_facility(
    record: dict[str, str], export: RidbExport, recarea_names: dict[str, str]
) -> Attraction:
    """Transform one ``Facilities`` record.

    Args:
        record: The raw CSV record.
        export: The joined export (org/address/activity/campsite lookups).
        recarea_names: ``RecAreaID → name`` for the owning-area label.

    Returns:
        The facility as an :class:`Attraction` (kind via
        :data:`RIDB_KIND_BY_TYPE`, default ``facility``; coordinates may still
        need the rec-area fallback).
    """
    facility_id = record['FacilityID']
    org_id = export.facility_orgs.get(facility_id)
    recarea_id = export.facility_recarea.get(facility_id)
    city, state = export.facility_address.get(facility_id, ('', ''))
    details = _ridb_details(
        {
            'description': record.get('FacilityDescription'),
            'directions': record.get('FacilityDirections'),
            'feeText': record.get('FacilityUseFeeDescription'),
            'phone': record.get('FacilityPhone'),
            'email': record.get('FacilityEmail'),
            'reservationUrl': record.get('FacilityReservationURL'),
            'mapUrl': record.get('FacilityMapURL'),
            'adaAccess': record.get('FacilityAdaAccess'),
            'accessibilityText': record.get('FacilityAccessibilityText'),
            'keywords': record.get('Keywords'),
            'stayLimit': record.get('StayLimit'),
            'reservable': record.get('Reservable') == 'true' or None,
            'org': export.org_names.get(org_id or ''),
            'recAreaName': recarea_names.get(recarea_id or ''),
            'city': city,
            'state': state,
            'activities': export.facility_activities.get(facility_id),
            'campsites': _campsites_detail(export.campsites.get(facility_id)),
        }
    )
    kind = RIDB_KIND_BY_TYPE.get(record.get('FacilityTypeDescription') or '', 'facility')
    return Attraction(
        source_kind=kind,
        source_id=f'fac:{facility_id}',
        park_code=recarea_id,
        name=(record.get('FacilityName') or '').strip(),
        lat=_ridb_coord(record.get('FacilityLatitude')),
        lon=_ridb_coord(record.get('FacilityLongitude')),
        summary=summarize(record.get('FacilityDescription')),
        details=details,
    )


def build_ridb_attractions(export: RidbExport) -> list[Attraction]:
    """Transform the joined export into attraction rows.

    Excludes NPS-org entities (the native NPS source is richer), pure
    reservation-product facility types, and nameless rows (a handful of source
    junk — unbrowsable), then backfills missing facility coordinates from the
    owning rec area (mirroring the NPS park fallback).

    Args:
        export: The joined export slice.

    Returns:
        The ``recarea`` + facility rows, coordinates backfilled where possible.
    """
    recareas = [
        parse_ridb_recarea(r, export)
        for r in export.recareas
        if export.recarea_orgs.get(r['RecAreaID']) != RIDB_NPS_ORG_ID
    ]
    recarea_names = {r['RecAreaID']: r['RecAreaName'] for r in export.recareas}
    recarea_coords = {a.park_code: (a.lat, a.lon) for a in recareas if a.lat is not None}
    facilities = [
        parse_ridb_facility(r, export, recarea_names)
        for r in export.facilities
        if export.facility_orgs.get(r['FacilityID']) != RIDB_NPS_ORG_ID
        and (r.get('FacilityTypeDescription') or '') not in RIDB_TYPE_EXCLUDE
    ]
    facilities = [
        replace(a, lat=recarea_coords[a.park_code][0], lon=recarea_coords[a.park_code][1])
        if a.lat is None and a.park_code in recarea_coords
        else a
        for a in facilities
    ]
    return [a for a in recareas + facilities if a.name]


# --- Load --------------------------------------------------------------------------


def load(
    conn: sqlite3.Connection,
    attractions: list[Attraction],
    events: list[Event],
    source: str = SOURCE,
) -> None:
    """Full-replace one source's slice of the attractions tier.

    One transaction: the tier is derived and rebuildable, so replace-on-sync is
    the whole idempotency story (mirrors the phone tier).

    Args:
        conn: An open, initialized connection.
        attractions: The POI rows.
        events: The event rows with expanded dates.
        source: The source whose slice to replace (``'nps'``/``'ridb'``).
    """
    now = now_canonical()
    with conn:
        conn.execute(
            'DELETE FROM attraction_event_dates WHERE event_id IN '
            '(SELECT id FROM attraction_events WHERE source = ?)',
            (source,),
        )
        conn.execute('DELETE FROM attraction_events WHERE source = ?', (source,))
        conn.execute('DELETE FROM attractions WHERE source = ?', (source,))
        conn.executemany(
            'INSERT INTO attractions (source, source_kind, source_id, park_code, name, '
            'lat, lon, summary, details, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (
                    source,
                    a.source_kind,
                    a.source_id,
                    a.park_code,
                    a.name,
                    a.lat,
                    a.lon,
                    a.summary,
                    json.dumps(a.details),
                    now,
                )
                for a in attractions
            ],
        )
        for event in events:
            cursor = conn.execute(
                'INSERT INTO attraction_events (source, source_id, park_code, name, lat, lon, '
                'location_text, is_free, needs_reservation, details, synced_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (
                    source,
                    event.source_id,
                    event.park_code,
                    event.name,
                    event.lat,
                    event.lon,
                    event.location_text,
                    None if event.is_free is None else int(event.is_free),
                    None if event.needs_reservation is None else int(event.needs_reservation),
                    json.dumps(event.details),
                    now,
                ),
            )
            conn.executemany(
                'INSERT INTO attraction_event_dates (event_id, date, time_start, time_end) '
                'VALUES (?, ?, ?, ?)',
                [(cursor.lastrowid, d.date, d.time_start, d.time_end) for d in event.dates],
            )


# --- Orchestration -------------------------------------------------------------------


def resolve_api_key(cli_key: str | None, env_file: Path) -> str:
    """Resolve the NPS api key: ``--api-key`` → ``$NPS_API_KEY`` → the env file.

    Args:
        cli_key: The ``--api-key`` value, if given.
        env_file: The secret file to fall back to (``NPS_API_KEY=...`` line).

    Returns:
        The key.

    Raises:
        SystemExit: When no key can be found anywhere.
    """
    if cli_key:
        return cli_key
    env_key = os.environ.get('NPS_API_KEY')
    if env_key:
        return env_key
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            name, _, value = line.strip().partition('=')
            if name == 'NPS_API_KEY' and value:
                return value.strip('\'"')
    raise SystemExit(
        f'No NPS API key: pass --api-key, set NPS_API_KEY, or add NPS_API_KEY=... to {env_file}'
    )


def run_ridb(args: argparse.Namespace) -> int:
    """Parse a local RIDB export zip, transform, and (unless ``--dry-run``) load.

    Args:
        args: Parsed arguments (``ridb_zip`` set).

    Returns:
        A process exit code.
    """
    import api.db

    zip_path = Path(args.ridb_zip)
    if not zip_path.exists():
        print(f'RIDB export not found: {zip_path}', file=sys.stderr)
        return 1
    print(f'Reading {zip_path} ...', flush=True)
    export = read_ridb_zip(zip_path)
    print(f'  {len(export.recareas)} rec areas, {len(export.facilities)} facilities', flush=True)

    attractions = build_ridb_attractions(export)
    located = sum(1 for a in attractions if a.lat is not None)
    by_kind = ', '.join(
        f'{kind}={sum(1 for a in attractions if a.source_kind == kind)}'
        for kind in ('recarea', 'facility', 'campground', 'visitorcenter', 'permit')
    )
    print(f'Parsed {len(attractions)} attractions ({located} with coordinates): {by_kind}')
    if args.dry_run:
        print('Dry run — not writing.', flush=True)
        return 0

    conn = get_connection()
    init_db(conn)
    load(conn, attractions, [], source=RIDB_SOURCE)
    print(f'Loaded into {api.db.DB_PATH}', flush=True)
    return 0


def run(args: argparse.Namespace) -> int:
    """Fetch every endpoint, transform, and (unless ``--dry-run``) load.

    Args:
        args: Parsed arguments.

    Returns:
        A process exit code.
    """
    import api.db

    if args.ridb_zip:
        return run_ridb(args)

    api_key = resolve_api_key(args.api_key, Path(args.env_file))
    session = requests.Session()

    def fetch(endpoint: str) -> list[dict[str, Any]]:
        print(f'Fetching {endpoint} ...', flush=True)
        rows = fetch_paged(session, endpoint, api_key)
        print(f'  {len(rows)} records', flush=True)
        return rows

    parks = [parse_park(r) for r in fetch('parks')]
    place_coords = {
        r['id']: (_coord(r.get('latitude')), _coord(r.get('longitude'))) for r in fetch('places')
    }
    attractions = dedupe_by_source_id(
        parks
        + [parse_thingstodo(r) for r in fetch('thingstodo')]
        + [parse_tour(r, place_coords) for r in fetch('tours')]
        + [parse_facility('visitorcenter', r) for r in fetch('visitorcenters')]
        + [parse_facility('campground', r) for r in fetch('campgrounds')]
    )
    attractions = apply_park_fallback(attractions)

    print('Fetching events ...', flush=True)
    events = dedupe_by_source_id([parse_event(r) for r in fetch_events(session, api_key)])
    n_dates = sum(len(e.dates) for e in events)
    print(f'  {len(events)} events ({n_dates} occurrences)', flush=True)

    located = sum(1 for a in attractions if a.lat is not None)
    print(
        f'Parsed {len(attractions)} attractions ({located} with coordinates), {len(events)} events',
        flush=True,
    )
    if args.dry_run:
        print('Dry run — not writing.', flush=True)
        return 0

    conn = get_connection()
    init_db(conn)
    load(conn, attractions, events)
    print(f'Loaded into {api.db.DB_PATH}', flush=True)
    return 0


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        The parsed argument namespace.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--db', help='SQLite DB path (default: GPS_DB_PATH / ~/gps_history.db)')
    parser.add_argument('--api-key', help='NPS api key (default: $NPS_API_KEY, then --env-file)')
    parser.add_argument(
        '--env-file',
        default=str(DEFAULT_ENV_FILE),
        help=f'Secret file with NPS_API_KEY=... (default: {DEFAULT_ENV_FILE})',
    )
    parser.add_argument(
        '--ridb-zip',
        help='Import a local RIDB full CSV export zip (source=ridb) instead of the NPS API',
    )
    parser.add_argument(
        '--dry-run', action='store_true', help='Fetch + report, but do not write the DB'
    )
    return parser.parse_args()


def main() -> None:
    """Entry point: parse args, set the DB path, run the importer."""
    args = parse_args()
    if args.db:
        import api.db

        api.db.DB_PATH = Path(args.db)
    sys.exit(run(args))


if __name__ == '__main__':
    run_cli(main)
