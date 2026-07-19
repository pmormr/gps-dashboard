"""Shared machinery for the satellite-backtest validators.

``tools/passes_validate.py`` (self-consistency vs held-out observations) and
``tools/tle_validate.py`` (absolute vs CelesTrak TLEs) share the same scaffolding:
anchor an observer and reconstruct logged az/el into per-satellite tracks, split
each track into an earlier fit and a later holdout, and print a per-constellation
error table. Holding that here keeps the two tools from drifting.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from api.db import canonical_timestamp, now_canonical
from api.observatory import Sample, anchor_observer, reconstruct_tracks
from common.gpsd import GNSS_NAMES
from common.satgeo import Vec3, observer_ecef


def percentile(values: list[float], frac: float) -> float:
    """Linear-index percentile of a non-empty list (frac in [0, 1])."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(frac * (len(ordered) - 1) + 0.5))]


def split_holdout(
    samples: list[Sample], hold_frac: float, min_fit: int
) -> tuple[list[Sample], list[Sample]]:
    """Earlier-fit / later-holdout split; empty fit when the record is too short."""
    k = int(len(samples) * (1.0 - hold_frac))
    if k < min_fit or len(samples) - k < 1:
        return [], []
    return samples[:k], samples[k:]


def print_error_table(
    errors: dict[int, list[float]], unit: str = '°', title: str | None = None
) -> None:
    """Print a per-constellation median/p90/max error table with an ALL row.

    Args:
        errors: Per-gnssid lists of angular errors.
        unit: Suffix appended to each value column (default degrees).
        title: Optional heading printed above the table.
    """
    if title:
        print(f'\n{title}')
    print(f'{"System":<10}{"SVs/obs":>10}{"median":>10}{"p90":>10}{"max":>10}')
    allv: list[float] = []
    for gnssid in sorted(errors):
        errs = errors[gnssid]
        allv.extend(errs)
        name = GNSS_NAMES.get(gnssid, f'gnss{gnssid}')
        print(
            f'{name:<10}{len(errs):>10}{percentile(errs, 0.5):>9.2f}{unit}'
            f'{percentile(errs, 0.9):>9.2f}{unit}{max(errs):>9.2f}{unit}'
        )
    if allv:
        print(
            f'{"ALL":<10}{len(allv):>10}{percentile(allv, 0.5):>9.2f}{unit}'
            f'{percentile(allv, 0.9):>9.2f}{unit}{max(allv):>9.2f}{unit}'
        )


def load_observation_tracks(
    conn: sqlite3.Connection, hours: float
) -> tuple[float, float, Vec3, dict[tuple[int, int], list[Sample]]] | None:
    """Anchor an observer and reconstruct logged az/el into per-satellite tracks.

    Args:
        conn: Open DB connection.
        hours: Trailing observation window.

    Returns:
        ``(lat, lon, origin_ecef, tracks)``, or None when no GPS fix is available
        to anchor the observer (the caller prints and exits).
    """
    obs = anchor_observer(conn, now_canonical())
    if obs is None:
        return None
    lat, lon = obs['lat'], obs['lon']
    origin = observer_ecef(lat, lon, obs['altitude'] or 0.0)
    start_ts = canonical_timestamp((datetime.now(UTC) - timedelta(hours=hours)).isoformat())
    rows = conn.execute(
        'SELECT timestamp, gnssid, svid, az, el, snr, used FROM sat_observations '
        'WHERE timestamp BETWEEN ? AND ? AND az IS NOT NULL AND el IS NOT NULL '
        'ORDER BY gnssid, svid, timestamp',
        [start_ts, now_canonical()],
    ).fetchall()
    tracks = reconstruct_tracks(rows, lat, lon, origin)
    return lat, lon, origin, tracks
