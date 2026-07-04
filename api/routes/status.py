"""Status-home aggregate: one read that powers the Van OS Home glance.

Collapses the headline metric of each domain — location, GNSS health, house power
(Victron), cabin environment (BME680), the van (OBD), the Pi host, the van-edge
router, and the recording fleet (NVR + a cameras-online aggregate, never per-cam) —
plus systemd service health into a single JSON document, so the Home page makes one
request instead of fanning out per domain. Every domain block carries its source
``timestamp`` (and the response a server ``now``) so the client decides freshness
itself: a parked van, a sleeping Victron GX, or an engine that's off all read as
*stale*, not as zero.

Read-only and schema-light: the per-domain tables always exist (``api.db`` creates
them), so a domain with no rows yet simply reports ``null``.
"""

from __future__ import annotations

import sqlite3

from flask import Blueprint, Response, jsonify

from api.db import get_connection, now_canonical
from common import proc

status_bp = Blueprint('status', __name__)

#: Units surfaced on the Home health strip. The enabled-gated ones (radio-control,
#: sensor-*) report ``inactive`` when dormant by design — the client distinguishes
#: that from ``failed``. ``gps-dashboard`` is omitted: it's serving this request.
_STATUS_SERVICES = (
    'gps-logger',
    'gps-processor',
    'mqtt-ingest',
    'sensor-victron',
    'sensor-obd',
    'sensor-pi',
    'sensor-openwrt',
    'sensor-dahua',
    'radio-control',
    'chrony',
)


def _latest(conn: sqlite3.Connection, table: str, cols: list[str]) -> dict | None:
    """Return the most recent row of ``table`` (timestamp + ``cols``), or None.

    Args:
        conn: Open SQLite connection.
        table: Source table — a trusted literal, never user input.
        cols: Metric columns to select alongside ``timestamp``.

    Returns:
        The latest row as a dict, or None when the table is empty.
    """
    row = conn.execute(
        f'SELECT timestamp, {", ".join(cols)} FROM {table} ORDER BY timestamp DESC LIMIT 1'
    ).fetchone()
    return dict(row) if row else None


def _obd_link(conn: sqlite3.Connection) -> str | None:
    """Return the van OBD stream's link state from the sensors registry.

    The OBD reader publishes its physical-link classification on the retained
    status topic (``online`` / ``no_adapter`` / ``no_car``; ``offline`` = reader
    process down), which ingest lands in ``sensors.status``. None when the stream
    has never registered.

    Args:
        conn: Open SQLite connection.

    Returns:
        The registry status string, or None.
    """
    row = conn.execute("SELECT status FROM sensors WHERE type = 'obd' LIMIT 1").fetchone()
    return row['status'] if row else None


def _cameras(conn: sqlite3.Connection) -> dict | None:
    """Return the camera fleet's online/total aggregate, or None with no cams.

    Home shows the fleet headline only, never per-camera cards. Latest row per
    camera (SQLite's bare-column-with-MAX guarantee picks the max-timestamp row),
    then counted; ``timestamp`` is the *oldest* of those latest rows, so the fleet
    reads stale as soon as any camera's stream stops updating.

    Args:
        conn: Open SQLite connection.

    Returns:
        ``{timestamp, online, total}``, or None when no camera has ever reported.
    """
    row = conn.execute(
        'SELECT COUNT(*) AS total, SUM(online) AS online, MIN(timestamp) AS timestamp '
        'FROM (SELECT sensor_id, online, MAX(timestamp) AS timestamp '
        '      FROM camera_readings GROUP BY sensor_id)'
    ).fetchone()
    if not row or row['total'] == 0:
        return None
    return {'timestamp': row['timestamp'], 'online': row['online'] or 0, 'total': row['total']}


def _ntp_synced() -> bool | None:
    """Best-effort chrony sync state for the health strip.

    Returns:
        True/False when ``chronyc tracking`` is readable, or None when chrony
        isn't reachable (not on the Pi, or the daemon is down).
    """
    rc, out, _ = proc.run(['chronyc', 'tracking'], timeout=5)
    if rc != 0 or not out:
        return None
    return 'Not synchronised' not in out and 'Reference ID' in out


@status_bp.get('/api/status')
def status() -> Response:
    """The Home glance: latest per-domain reading + service health in one read."""
    conn = get_connection()
    return jsonify(
        {
            'now': now_canonical(),
            'location': _latest(conn, 'gps_points', ['lat', 'lon', 'speed', 'mode']),
            'gnss': _latest(conn, 'receiver_metadata', ['nsat_used', 'nsat_seen', 'hdop', 'pdop']),
            'house': _latest(
                conn,
                'victron_readings',
                ['battery_soc', 'battery_power', 'pv_power', 'dc_system_power'],
            ),
            'cabin': _latest(conn, 'bme680_readings', ['temp_c', 'humidity_pct', 'iaq']),
            'van': _latest(conn, 'obd_readings', ['rpm', 'coolant_c', 'speed_kph']),
            'obd_link': _obd_link(conn),
            'pi': _latest(
                conn,
                'system_readings',
                ['cpu_temp_c', 'load_1m', 'mem_used_pct', 'disk_nvme_free_gb', 'throttled'],
            ),
            'router': _latest(
                conn,
                'openwrt_readings',
                ['wan_up', 'wan_ping_ms', 'halow_rssi_dbm', 'halow_stations'],
            ),
            'nvr': _latest(conn, 'nvr_readings', ['hdd_ok', 'hdd_temp_c', 'channels_video_loss']),
            'cameras': _cameras(conn),
            'services': [{'name': s, 'state': proc.service_state(s)} for s in _STATUS_SERVICES],
            'ntp': {'synced': _ntp_synced()},
        }
    )
