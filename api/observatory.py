"""Shared data access for the GNSS-observatory routes.

Both ``/api/constellation`` (the 3D globe) and ``/api/passes`` (prediction) start
the same way: pick one observer fix to anchor on, then invert each logged az/el
into an ECEF position on the constellation's nominal orbital sphere, grouped by
satellite. This module holds that shared anchor + reconstruction so the two
routes can't drift apart. See .claude/modules/observatory.md.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import NamedTuple

from common.satgeo import Vec3, look_direction_ecef, orbital_radius_m, ray_sphere_far

# gnssid -> RINEX constellation letter, for satellite names (G01, R13, E11, ...).
GNSS_LETTER = {0: 'G', 1: 'S', 2: 'E', 3: 'C', 5: 'J', 6: 'R'}


def sat_name(gnssid: int, svid: int) -> str:
    """RINEX-style satellite name, e.g. ``G07`` or ``R13``."""
    return f'{GNSS_LETTER.get(gnssid, "?")}{svid:02d}'


class Sample(NamedTuple):
    """One reconstructed observation of a satellite.

    Attributes:
        t: Canonical timestamp string (as stored).
        unix: The same instant as Unix seconds (for orbit math).
        ecef_m: Reconstructed ECEF position, metres.
        snr: Signal strength (dB-Hz) or None.
        used: Whether the receiver used the SV in its fix.
    """

    t: str
    unix: float
    ecef_m: Vec3
    snr: float | None
    used: bool


def unix_seconds(canonical: str) -> float:
    """Unix seconds for a canonical millisecond-UTC timestamp string."""
    return datetime.fromisoformat(canonical.replace('Z', '+00:00')).timestamp()


def anchor_observer(conn: sqlite3.Connection, end_ts: str) -> sqlite3.Row | None:
    """Pick the observer fix to anchor reconstruction on.

    The latest ``gps_points`` fix at or before ``end_ts``; failing that (the
    window predates all data), the latest fix overall. Over a parked session the
    van barely moves against the ~26,000 km orbital baseline, so a single anchor
    is ample.

    Args:
        conn: Open database connection.
        end_ts: Canonical window-end timestamp.

    Returns:
        A row with ``lat, lon, altitude, timestamp``, or None when there is no
        fix at all.
    """
    obs = conn.execute(
        'SELECT lat, lon, altitude, timestamp FROM gps_points '
        'WHERE timestamp <= ? ORDER BY timestamp DESC LIMIT 1',
        [end_ts],
    ).fetchone()
    if obs is None:
        obs = conn.execute(
            'SELECT lat, lon, altitude, timestamp FROM gps_points ORDER BY timestamp DESC LIMIT 1'
        ).fetchone()
    return obs


def reconstruct_tracks(
    rows: list[sqlite3.Row], lat: float, lon: float, origin: Vec3
) -> dict[tuple[int, int], list[Sample]]:
    """Group ``sat_observations`` rows into per-satellite ECEF tracks.

    Each row's az/el is inverted to an ECEF position on its constellation's
    orbital sphere (unmodelled gnssids — IMES — are skipped). Rows must be
    time-ordered for the downstream orbit/normal fits; this preserves their order
    within each satellite.

    Args:
        rows: ``sat_observations`` rows (timestamp, gnssid, svid, az, el, snr,
            used), ordered by satellite then time.
        lat: Observer latitude, degrees.
        lon: Observer longitude, degrees east.
        origin: Observer ECEF position, metres.

    Returns:
        ``{(gnssid, svid): [Sample, ...]}``.
    """
    radii: dict[int, float | None] = {}
    tracks: dict[tuple[int, int], list[Sample]] = {}
    for r in rows:
        gid = r['gnssid']
        if gid not in radii:
            radii[gid] = orbital_radius_m(gid)
        radius = radii[gid]
        if radius is None:
            continue
        direction = look_direction_ecef(lat, lon, r['az'], r['el'])
        pos = ray_sphere_far(origin, direction, radius)
        if pos is None:
            continue
        tracks.setdefault((gid, r['svid']), []).append(
            Sample(r['timestamp'], unix_seconds(r['timestamp']), pos, r['snr'], bool(r['used']))
        )
    return tracks
