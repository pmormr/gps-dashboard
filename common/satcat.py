"""Fetch and cache CelesTrak SATCAT metadata for GNSS satellites.

Companion to the TLE backtest (``tools/tle_validate.py``): the two-line elements
say *where* a satellite is, the SATCAT says *what* it is. CelesTrak's satellite
catalogue holds, per NORAD id: the international designator, owner-country, launch
date and site, decay date, orbit geometry (period, inclination, apogee, perigee),
radar cross-section size, object type, and operational status. The block /
generation is carried in the object name (``GPS BIIF-2``, ``GSAT0210``); the bus
manufacturer ("make") is *not* in SATCAT and is intentionally out of scope here.

Metadata keys by NORAD id, and the geometric matcher resolves each ``(gnssid,
svid)`` to a NORAD id (``Satrec.satnum``), so the two join with no fragile
PRN/slot table. Fetched online and cached to JSON so it is available offline
thereafter — the "fetch while online, use offline" pattern the tile/terrain
builders use. See .claude/modules/observatory.md.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

SATCAT_URL = 'https://celestrak.org/satcat/records.php'
_USER_AGENT = 'gps-dashboard satcat fetch (https://github.com/pmormr/gps-dashboard)'

#: Where the fetched catalogue lives. Shared by the fetch CLI
#: (tools/fetch_satcat.py) and the offline-data freshness probe
#: (updater/probes.py) so both sides look at the same file.
DEFAULT_CACHE_PATH = Path.home() / '.cache' / 'gps-dashboard' / 'gnss-satcat.json'


@dataclass(frozen=True)
class SatMeta:
    """Descriptive catalogue metadata for one satellite.

    Attributes:
        norad: NORAD catalogue id (the join key to a fitted/propagated orbit).
        name: Catalogue object name (carries the block/generation and PRN).
        intl_designator: International designator, e.g. ``2000-040A``.
        prn: Parsed PRN label (``22``, ``E11``, ``194``) or None.
        object_type: ``PAY`` (payload), ``R/B`` (rocket body), ``DEB``, ...
        ops_status: Operational-status code (``+`` = operational).
        owner: Owner-country code, e.g. ``US``, ``CIS``, ``ESA``, ``PRC``.
        launch_date: Launch date ``YYYY-MM-DD`` (empty if unknown).
        launch_site: Launch-site code, e.g. ``AFETR``.
        decay_date: Decay date ``YYYY-MM-DD`` (empty if still on orbit).
        period_min: Orbital period in minutes, or None.
        inclination_deg: Inclination in degrees, or None.
        apogee_km: Apogee altitude in km, or None.
        perigee_km: Perigee altitude in km, or None.
        rcs_m2: Radar cross-section in m², or None when not on file.
        orbit_type: Orbit class code, e.g. ``ORB``.
    """

    norad: int
    name: str
    intl_designator: str
    prn: str | None
    object_type: str
    ops_status: str
    owner: str
    launch_date: str
    launch_site: str
    decay_date: str
    period_min: float | None
    inclination_deg: float | None
    apogee_km: float | None
    perigee_km: float | None
    rcs_m2: float | None
    orbit_type: str


def _f(value: Any) -> float | None:
    """Coerce a SATCAT field to float, mapping blanks/non-numbers to None."""
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _prn_from_name(name: str) -> str | None:
    """Extract the PRN label from a catalogue name, or None if absent."""
    upper = name.upper()
    marker = 'PRN'
    idx = upper.find(marker)
    if idx < 0:
        return None
    token = upper[idx + len(marker) :].strip()
    label = ''
    for ch in token:
        if ch.isalnum() and not (label and ch.isalpha()):
            label += ch
        else:
            break
    return label or None


def _parse(rec: dict[str, Any]) -> SatMeta:
    """Build a :class:`SatMeta` from one raw SATCAT JSON record."""
    name = str(rec.get('OBJECT_NAME', '')).strip()
    return SatMeta(
        norad=int(rec['NORAD_CAT_ID']),
        name=name,
        intl_designator=str(rec.get('OBJECT_ID', '')),
        prn=_prn_from_name(name),
        object_type=str(rec.get('OBJECT_TYPE', '')),
        ops_status=str(rec.get('OPS_STATUS_CODE', '')),
        owner=str(rec.get('OWNER', '')),
        launch_date=str(rec.get('LAUNCH_DATE', '')),
        launch_site=str(rec.get('LAUNCH_SITE', '')),
        decay_date=str(rec.get('DECAY_DATE', '')),
        period_min=_f(rec.get('PERIOD')),
        inclination_deg=_f(rec.get('INCLINATION')),
        apogee_km=_f(rec.get('APOGEE')),
        perigee_km=_f(rec.get('PERIGEE')),
        rcs_m2=_f(rec.get('RCS')),
        orbit_type=str(rec.get('ORBIT_TYPE', '')),
    )


def fetch_satcat(group: str = 'gnss') -> list[dict[str, Any]]:
    """Fetch raw SATCAT JSON records for a CelesTrak group (raises on error)."""
    resp = requests.get(
        SATCAT_URL,
        params={'GROUP': group, 'FORMAT': 'json'},
        headers={'User-Agent': _USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    records: list[dict[str, Any]] = resp.json()
    return records


def load_satcat(
    cache: Path, max_age_h: float = 168.0, fetch: bool = True, group: str = 'gnss'
) -> dict[int, SatMeta]:
    """Load SATCAT metadata from the cache, fetching when stale or forced.

    Args:
        cache: Snapshot JSON path (written after a successful fetch).
        max_age_h: Reuse the cache when younger than this; refetch otherwise.
            SATCAT changes slowly (launches/decays), so the default is a week.
        fetch: When False, never hit the network — fail if the cache is missing.
        group: CelesTrak SATCAT group (``gnss`` for all GNSS constellations).

    Returns:
        ``{norad_id: SatMeta}`` for every record in the group.

    Raises:
        FileNotFoundError: ``fetch`` is False and no cache exists.
    """
    records: list[dict[str, Any]] | None = None
    if cache.exists():
        blob = json.loads(cache.read_text())
        age_h = (time.time() - blob['fetched_unix']) / 3600.0
        if not fetch or age_h <= max_age_h:
            records = blob['records']
    if records is None:
        if not fetch:
            raise FileNotFoundError(f'No SATCAT cache at {cache} and fetch disabled')
        records = fetch_satcat(group)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({'fetched_unix': time.time(), 'records': records}))
    return {m.norad: m for m in (_parse(r) for r in records)}
