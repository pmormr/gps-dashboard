"""Shared gpsd access: a short-lived socket snapshot plus constellation/device helpers.

The dashboard talks to gpsd in two modes. The *logger* is a long-lived streaming
reader with its own reconnect/staleness logic (``logger/gps_logger.py``) and is
deliberately not built on this module. Everything else — the status page, the
skyplot feed, the validate tool — needs a one-shot *snapshot*: connect, ask for a
report, read the first TPV/SKY, disconnect. Each had its own copy of that loop;
:func:`query_gpsd` is the single implementation.
"""

from __future__ import annotations

import json
import re
import socket
import time
from typing import Any

GPSD_HOST = '127.0.0.1'
GPSD_PORT = 2947
_WATCH = b'?WATCH={"enable":true,"json":true}\n'

GPSD_CONFIG_PATH = '/etc/default/gpsd'

# gpsd TPV fix mode -> label, title-cased for UI display.
FIX_LABELS = {0: 'Unknown', 1: 'No Fix', 2: '2D Fix', 3: '3D Fix'}

# gpsd/u-blox gnssid -> constellation name. The M9N tracks GPS + GLONASS +
# Galileo + BeiDou, plus SBAS/QZSS augmentation.
GNSS_NAMES = {0: 'GPS', 1: 'SBAS', 2: 'Galileo', 3: 'BeiDou', 4: 'IMES', 5: 'QZSS', 6: 'GLONASS'}


def query_gpsd(timeout: float = 5, want_sky: bool = True) -> dict[str, Any]:
    """Open a short-lived gpsd socket and snapshot the first TPV and SKY reports.

    Sends a WATCH request, then reads until the wanted reports have arrived or
    ``timeout`` elapses, whichever comes first. Never raises — a refused or
    timed-out socket yields ``connected=False`` with empty reports, so callers
    branch on the returned dict rather than handling exceptions.

    With ``want_sky=False`` the read returns on the first TPV — the receiver
    emits TPV at its full nav rate (5 Hz), so this path lands in ≤ ~200 ms and
    is cheap enough to poll (the Drive view's live feed).

    Args:
        timeout: Combined socket and read-loop budget, in seconds.
        want_sky: Whether to also wait for a SKY report.

    Returns:
        ``{'connected': bool, 'tpv': dict, 'sky': dict}`` — ``tpv`` and ``sky``
        are the raw gpsd report dicts, or empty dicts if none arrived in time.
    """
    result: dict[str, Any] = {'connected': False, 'tpv': {}, 'sky': {}}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((GPSD_HOST, GPSD_PORT))
        s.sendall(_WATCH)
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
            if result['tpv'] and (result['sky'] or not want_sky):
                break
        s.close()
    except Exception:
        pass
    return result


def constellation(sat: dict[str, Any]) -> str:
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


def configured_gpsd_device() -> str | None:
    """Read the ``DEVICES="..."`` value from ``/etc/default/gpsd``.

    Returns:
        The configured device string (may name several space-separated devices),
        or None if the file or the setting is absent.
    """
    try:
        with open(GPSD_CONFIG_PATH) as f:
            content = f.read()
    except FileNotFoundError:
        return None
    m = re.search(r'DEVICES="([^"]*)"', content)
    return m.group(1).strip() if m else None
