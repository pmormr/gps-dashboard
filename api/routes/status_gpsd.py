import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, render_template

from api.db import canonical_timestamp, get_connection

status_gpsd_bp = Blueprint('status_gpsd', __name__)

FIX_LABELS = {0: 'Unknown', 1: 'No Fix', 2: '2D Fix', 3: '3D Fix'}

# gpsd/u-blox gnssid → constellation name. The M9N tracks GPS + GLONASS +
# Galileo + BeiDou, plus SBAS/QZSS augmentation.
GNSS_NAMES = {0: 'GPS', 1: 'SBAS', 2: 'Galileo', 3: 'BeiDou',
              4: 'IMES', 5: 'QZSS', 6: 'GLONASS'}


def _constellation(sat):
    """Resolve a SKY satellite's constellation name.

    Prefers gpsd's explicit ``gnssid``; falls back to coarse legacy PRN ranges
    for older gpsd builds that omit it.

    Args:
        sat: One satellite object from a gpsd SKY message.

    Returns:
        Constellation name (e.g. 'GPS', 'Galileo'), or 'Other' if unresolved.
    """
    gid = sat.get('gnssid')
    if gid in GNSS_NAMES:
        return GNSS_NAMES[gid]
    prn = sat.get('PRN') or 0
    if 1 <= prn <= 32:
        return 'GPS'
    if 65 <= prn <= 96:
        return 'GLONASS'
    if 120 <= prn <= 158:
        return 'SBAS'
    if 193 <= prn <= 199:
        return 'QZSS'
    if 201 <= prn <= 263:
        return 'BeiDou'
    if 301 <= prn <= 336:
        return 'Galileo'
    return 'Other'

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
        cutoff = canonical_timestamp(
            (datetime.now(timezone.utc)
             - timedelta(seconds=FROZEN_WINDOW_SECONDS)).isoformat()
        )
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


@status_gpsd_bp.get('/skyplot')
def skyplot():
    """Live 3D satellite skyplot page (client polls ``/api/gpsd/sky``)."""
    return render_template('skyplot.html')


@status_gpsd_bp.get('/api/gpsd/sky')
def gpsd_sky():
    """Live satellite sky sourced straight from gpsd's SKY message.

    Opens a short-lived gpsd socket (no DB, no schema — the live skyplot reads
    gpsd directly) and returns the current constellation for the polar/3D plot:
    per-satellite azimuth, elevation, SNR, used-flag, and constellation, plus
    DOP and used/seen counts. Satellites without an az/el (no almanac yet) are
    omitted from ``satellites`` but still counted in ``seen``.

    Returns:
        JSON dict with ``connected``, ``fix_mode``/``fix_label``, ``used``,
        ``seen``, ``hdop``/``vdop``/``pdop``, and a ``satellites`` list of
        ``{prn, az, el, ss, used, gnss}``.
    """
    gpsd = _query_gpsd()
    sky = gpsd['sky']
    tpv = gpsd['tpv']
    sats = sky.get('satellites', []) or []

    plotted = []
    for s in sats:
        az = s.get('az')
        el = s.get('el')
        if az is None or el is None:
            continue
        plotted.append({
            'prn': s.get('PRN'),
            'az': az,
            'el': el,
            'ss': s.get('ss'),
            'used': bool(s.get('used')),
            'gnss': _constellation(s),
        })

    mode = tpv.get('mode', 0)
    return {
        'connected': gpsd['connected'],
        'fix_mode': mode,
        'fix_label': FIX_LABELS.get(mode, 'Unknown'),
        'used': sum(1 for s in sats if s.get('used')),
        'seen': len(sats),
        'hdop': sky.get('hdop'),
        'vdop': sky.get('vdop'),
        'pdop': sky.get('pdop'),
        'satellites': plotted,
    }
