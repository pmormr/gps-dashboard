"""Sync OwnTracks phone positions from the home Recorder into ``gps_history.db``.

The pull half of the live phone-tracking tier (``plans/phone-tracking-plan.md``):
the OwnTracks app POSTs fixes to an OwnTracks Recorder on rex-nas (from home
WiFi or through the phone's WireGuard peer); this tool pulls the Recorder's REST
API over the van↔home link and appends to ``owntracks_points``. Run 5-minutely
by ``gps-owntracks-sync.timer``, so a near-live latest-position read falls out
of the cadence.

Sync model (the drone home-sync shape, HTTP instead of SSH):

* **Preflight** — one short-timeout request; unreachable → exit 0 quietly
  (boondocking is normal, the timer simply fires again later).
* **Cursor** — per (user, device), the stored ``MAX(timestamp)``, re-pulled
  minus an hour of slack; the unique ``(device, timestamp)`` key plus
  ``INSERT OR IGNORE`` make the overlap free (idempotent).
* **Discovery** — users/devices come from the Recorder (``/api/0/list``), so a
  second phone needs no change here.

The tier is append-only and fully rebuildable: the Recorder's ``/store`` on
rex-nas is the source of truth — delete every row and re-run to backfill.

Examples::

    # Dev: pull into a throwaway DB
    uv run tools/sync_owntracks.py --base-url http://10.1.100.224:8083 --db ./local.db

    # Pi (what the timer runs)
    GPS_DB_PATH=/mnt/nvme/data/gps_history.db \\
        uv run tools/sync_owntracks.py --base-url http://10.1.100.224:8083
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any

import requests

import api.db
from api.db import get_connection, init_db, now_canonical
from common.cli import run_cli
from common.timefmt import format_canonical, parse_iso

PREFLIGHT_TIMEOUT_S = 5.0
FETCH_TIMEOUT_S = 60.0
CURSOR_SLACK = timedelta(hours=1)

_INSERT_SQL = (
    'INSERT OR IGNORE INTO owntracks_points '
    '(user, device, timestamp, lat, lon, accuracy, altitude, velocity, battery, synced_at) '
    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
)

Row = tuple[str, str, str, float, float, Any, Any, Any, Any, str]


def recorder_from_param(cursor: str | None, slack: timedelta = CURSOR_SLACK) -> str:
    """Build the Recorder ``from`` query param for an incremental pull.

    The Recorder filters ``from`` as a naive ``YYYY-MM-DDTHH:MM:SS`` in its own
    timezone — the container runs UTC, matching the canonical axis. The cursor
    is re-pulled minus ``slack`` so fixes that landed out of order near the
    boundary are not missed; idempotent inserts make the overlap free.

    Args:
        cursor: The stored ``MAX(timestamp)`` for the device (canonical ms-UTC),
            or ``None`` when the device has no rows yet.
        slack: Overlap re-pulled behind the cursor.

    Returns:
        The ``from`` parameter value (second precision, UTC, no suffix).
    """
    if cursor is None:
        return '1970-01-01T00:00:00'
    return (parse_iso(cursor) - slack).strftime('%Y-%m-%dT%H:%M:%S')


def location_row(rec: dict[str, Any], user: str, device: str, synced_at: str) -> Row | None:
    """Map one Recorder location record to an ``owntracks_points`` insert row.

    Args:
        rec: One record from the ``/api/0/locations`` response ``data``.
        user: The OwnTracks user the record was queried for.
        device: The OwnTracks device the record was queried for.
        synced_at: Canonical sync time stamped onto the row.

    Returns:
        The insert tuple, or ``None`` for a non-location record or one missing
        a required field (``tst``/``lat``/``lon``).
    """
    if rec.get('_type') != 'location':
        return None
    tst, lat, lon = rec.get('tst'), rec.get('lat'), rec.get('lon')
    if (
        not isinstance(tst, int | float)
        or not isinstance(lat, int | float)
        or not isinstance(lon, int | float)
    ):
        return None
    timestamp = format_canonical(datetime.fromtimestamp(tst, tz=UTC))
    return (
        user,
        device,
        timestamp,
        lat,
        lon,
        rec.get('acc'),
        rec.get('alt'),
        rec.get('vel'),
        rec.get('batt'),
        synced_at,
    )


def _get_json(base_url: str, path: str, params: dict[str, str], timeout: float) -> Any:
    """GET a Recorder endpoint and return its parsed JSON body.

    Args:
        base_url: Recorder base URL (no trailing slash).
        path: Endpoint path, e.g. ``/api/0/list``.
        params: Query parameters.
        timeout: Request timeout in seconds.

    Returns:
        The decoded JSON payload.

    Raises:
        requests.RequestException: On connection failure or a non-2xx status.
    """
    resp = requests.get(f'{base_url}{path}', params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def list_users(base_url: str) -> list[str]:
    """List the users the Recorder has data for.

    Args:
        base_url: Recorder base URL.

    Returns:
        User names (``/api/0/list`` ``results``).
    """
    return list(_get_json(base_url, '/api/0/list', {}, FETCH_TIMEOUT_S).get('results', []))


def list_devices(base_url: str, user: str) -> list[str]:
    """List one user's devices.

    Args:
        base_url: Recorder base URL.
        user: The OwnTracks user.

    Returns:
        Device names (``/api/0/list?user=…`` ``results``).
    """
    payload = _get_json(base_url, '/api/0/list', {'user': user}, FETCH_TIMEOUT_S)
    return list(payload.get('results', []))


def fetch_locations(base_url: str, user: str, device: str, frm: str) -> list[dict[str, Any]]:
    """Fetch one device's location records from ``frm`` onward.

    Args:
        base_url: Recorder base URL.
        user: The OwnTracks user.
        device: The OwnTracks device.
        frm: The ``from`` bound (see :func:`recorder_from_param`).

    Returns:
        Location records (``/api/0/locations`` ``data``).
    """
    params = {'user': user, 'device': device, 'from': frm, 'format': 'json'}
    return list(_get_json(base_url, '/api/0/locations', params, FETCH_TIMEOUT_S).get('data', []))


def sync(conn: sqlite3.Connection, base_url: str, dry_run: bool = False) -> int:
    """Run one incremental pull of every Recorder device into the DB.

    Args:
        conn: Open SQLite connection with the schema present.
        base_url: Recorder base URL.
        dry_run: Fetch and report without writing.

    Returns:
        Rows actually inserted across all devices.
    """
    synced_at = now_canonical()
    total = 0
    for user in list_users(base_url):
        for device in list_devices(base_url, user):
            cursor = conn.execute(
                'SELECT MAX(timestamp) FROM owntracks_points WHERE user = ? AND device = ?',
                (user, device),
            ).fetchone()[0]
            frm = recorder_from_param(cursor)
            records = fetch_locations(base_url, user, device, frm)
            rows = [row for rec in records if (row := location_row(rec, user, device, synced_at))]
            inserted = 0
            if rows and not dry_run:
                inserted = conn.executemany(_INSERT_SQL, rows).rowcount
            note = ' (dry-run)' if dry_run else ''
            print(
                f'{user}/{device}: fetched {len(records)}, inserted {inserted}{note} (from {frm})'
            )
            total += inserted
    conn.commit()
    return total


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments.

    Returns:
        The parsed namespace; exits with a usage error when no base URL is given.
    """
    parser = argparse.ArgumentParser(
        description='Pull OwnTracks Recorder fixes into the owntracks_points tier.'
    )
    parser.add_argument(
        '--base-url',
        default=os.environ.get('GPS_OWNTRACKS_URL'),
        help='Recorder base URL, e.g. http://10.1.100.224:8083 (default: $GPS_OWNTRACKS_URL)',
    )
    parser.add_argument('--db', help='SQLite DB path (default: GPS_DB_PATH / ~/gps_history.db)')
    parser.add_argument('--dry-run', action='store_true', help='Fetch and report, write nothing')
    args = parser.parse_args()
    if not args.base_url:
        parser.error('--base-url (or GPS_OWNTRACKS_URL) is required')
    return args


def main() -> int | None:
    """Entry point: preflight the Recorder, then run one incremental sync.

    Returns:
        ``1`` on a mid-sync request failure, ``None`` (exit 0) otherwise —
        including the unreachable-Recorder no-op, which is normal off-grid.
    """
    args = parse_args()
    api.db.apply_path_overrides(args.db)
    base_url = args.base_url.rstrip('/')

    try:
        requests.get(f'{base_url}/api/0/list', timeout=PREFLIGHT_TIMEOUT_S).raise_for_status()
    except requests.RequestException:
        print('Recorder unreachable — skipping (normal off-grid).')
        return None

    conn = get_connection()
    init_db(conn)
    try:
        total = sync(conn, base_url, dry_run=args.dry_run)
    except requests.RequestException as exc:
        print(f'Recorder request failed mid-sync: {exc}')
        return 1
    finally:
        conn.close()
    print(f'Total inserted: {total}')
    return None


if __name__ == '__main__':
    run_cli(main)
