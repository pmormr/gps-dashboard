import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template

from api.db import get_connection

status_gpsd_bp = Blueprint('status_gpsd', __name__)

FIX_LABELS = {0: 'Unknown', 1: 'No Fix', 2: '2D Fix', 3: '3D Fix'}

# Mirror of the logger's frozen-fix detection, derived from the DB instead of the
# live gpsd stream so the status page (a separate process) needs no IPC. A stuck
# receiver keeps writing valid points, so a window of recent points that are all
# byte-identical means the position is frozen even though every other check is
# green. See FROZEN_POSITION_SECONDS in logger/gps_logger.py.
FROZEN_WINDOW_SECONDS = 120
FROZEN_MIN_POINTS = 10


def _service_state():
    try:
        r = subprocess.run(['systemctl', 'is-active', 'gpsd'],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return 'unknown'


def _configured_device():
    try:
        with open('/etc/default/gpsd') as f:
            content = f.read()
        m = re.search(r'DEVICES="([^"]*)"', content)
        return m.group(1).strip() if m else None
    except FileNotFoundError:
        return None


def _query_gpsd(timeout=5):
    result = {'connected': False, 'tpv': {}, 'sky': {}}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(('127.0.0.1', 2947))
        s.sendall(b'?WATCH={"enable":true,"json":true}\n')
        f = s.makefile('r', encoding='utf-8', errors='replace')
        result['connected'] = True
        deadline = time.monotonic() + timeout
        for line in f:
            if time.monotonic() > deadline:
                break
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            cls = r.get('class')
            if cls == 'TPV' and not result['tpv']:
                result['tpv'] = r
            elif cls == 'SKY' and not result['sky']:
                result['sky'] = r
            if result['tpv'] and result['sky']:
                break
        s.close()
    except Exception:
        pass
    return result


def _latest_point():
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT timestamp, lat, lon, speed, altitude FROM gps_points "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _position_frozen():
    """Whether recent logged points are all the same coordinate (a stuck fix).

    Reads the trailing FROZEN_WINDOW_SECONDS of points and reports frozen only
    when there are enough of them spanning most of the window and every one
    shares the same lat/lon. A live fix jitters in the low digits even parked,
    so byte-identical coordinates across the window are the freeze signature.
    Returns False on too little data — a no-data/stale case the freshness check
    already covers, so this never double-fails for an outage.

    Returns:
        True if the position appears frozen, False otherwise.
    """
    try:
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(seconds=FROZEN_WINDOW_SECONDS)
                  ).strftime('%Y-%m-%dT%H:%M:%SZ')
        conn = get_connection()
        rows = conn.execute(
            "SELECT lat, lon, timestamp FROM gps_points "
            "WHERE timestamp >= ? ORDER BY id", (cutoff,)
        ).fetchall()
        if len(rows) < FROZEN_MIN_POINTS:
            return False
        span = (datetime.fromisoformat(rows[-1]['timestamp'].replace('Z', '+00:00'))
                - datetime.fromisoformat(rows[0]['timestamp'].replace('Z', '+00:00'))
                ).total_seconds()
        if span < FROZEN_WINDOW_SECONDS * 0.8:
            return False
        return len({(r['lat'], r['lon']) for r in rows}) == 1
    except Exception:
        return False


@status_gpsd_bp.get('/gpsd')
def gpsd_status():
    service_state = _service_state()
    device = _configured_device()
    gpsd = _query_gpsd()
    latest = _latest_point()

    tpv = gpsd['tpv']
    sky = gpsd['sky']

    fix_mode = tpv.get('mode', 0)
    satellites = sky.get('satellites', [])
    sats_used = sum(1 for s in satellites if s.get('used'))
    sats_visible = len(satellites)

    devices = device.split() if device else []
    device_present = any(os.path.exists(d) for d in devices)

    data_age = data_fresh = None
    if latest:
        try:
            ts = datetime.fromisoformat(latest['timestamp'].replace('Z', '+00:00'))
            data_age = int((datetime.now(timezone.utc) - ts).total_seconds())
            data_fresh = data_age < 30
        except Exception:
            data_fresh = False

    # Only assert a freeze when data is fresh; otherwise a stale stream would
    # both fail "data fresh" and read as frozen for the same outage.
    frozen = bool(data_fresh) and _position_frozen()

    checks = [
        ('gpsd service',       service_state == 'active'),
        ('device present',     device_present),
        ('port 2947 open',     gpsd['connected']),
        ('GPS fix',            fix_mode >= 2),
        ('data fresh (< 30s)', bool(data_fresh)),
        ('position moving',    not frozen),
    ]

    overall_ok = all(ok for _, ok in checks)

    return render_template('gpsd.html',
        overall_ok=overall_ok,
        checks=checks,
        service_state=service_state,
        device=device or 'not configured',
        device_present=device_present,
        fix_mode=fix_mode,
        fix_label=FIX_LABELS.get(fix_mode, 'Unknown'),
        sats_used=sats_used,
        sats_visible=sats_visible,
        latest=latest,
        data_age=data_age,
        frozen=frozen,
    )
