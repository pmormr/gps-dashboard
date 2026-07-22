"""syslog-ng relay + buffer health for the Van OS Systems view.

The Pi runs ``syslog-ng`` as the van's store-and-forward log relay: it receives
other van devices' syslog on ``:514`` and forwards everything (its own logs plus
the relayed traffic) to the house Graylog over TCP through a *reliable on-disk
buffer*. Off-grid, the buffer holds messages until Graylog is reachable again, then
drains — so a growing backlog is **healthy**, not a fault. This route surfaces that:
service liveness, the ``:514`` listeners, and the destination's queue counters.

Only an actual loss (``dropped`` > 0 = the buffer overflowed its cap), a stopped
service, or a missing listener is a failure. The queue depth (``queued``) is
reported as information, never as a check.

Buffer counters come from ``syslog-ng-ctl stats``, whose control socket is
root-owned; we read it via passwordless ``sudo -n`` and degrade softly (the
service/listening checks still stand) if that isn't available.
"""

from __future__ import annotations

import re

from flask import Blueprint, Response, jsonify

from common import proc

status_syslog_bp = Blueprint('status_syslog', __name__)

#: The Graylog forward target, for display only (the authoritative value lives in
#: the Pi's ``/etc/syslog-ng/conf.d/graylog.conf``, which this process can't read).
_DEST = 'rex-nas.rex.pmormr.com:514'

#: Absolute path — the service PATH omits ``/usr/sbin``.
_CTL = '/usr/sbin/syslog-ng-ctl'


def _listening() -> tuple[bool, bool]:
    """Whether the relay is accepting syslog on ``:514`` over UDP and TCP.

    Returns:
        ``(udp, tcp)`` booleans, parsed from ``ss`` listening tables (no
        privilege needed; the same approach the NTP route uses for ``:123``).
    """
    _, udp, _ = proc.run(['ss', '-lnu'])
    _, tcp, _ = proc.run(['ss', '-lnt'])
    return bool(re.search(r':514\s', udp)), bool(re.search(r':514\s', tcp))


def _parse_stats(text: str) -> dict:
    """Parse ``syslog-ng-ctl stats`` output into the counters we surface.

    The output is ``;``-separated rows ``name;id;instance;state;metric;number``.
    We pull the Graylog destination's queue health and the per-source message
    counts (``s_net`` = relayed from other devices, ``s_src`` = this Pi's own).

    Args:
        text: Raw stdout from ``syslog-ng-ctl stats``.

    Returns:
        ``{queued, dropped, written, eps_1h, relayed, local}``; any metric absent
        from the output is ``None`` so a stats-format change degrades softly.
    """
    graylog: dict[str, float] = {}
    relayed: int | None = None
    local: int | None = None
    for line in text.splitlines():
        parts = line.split(';')
        if len(parts) != 6:
            continue
        name, sid, _instance, _state, metric, number = parts
        try:
            value = float(number)
        except ValueError:
            continue
        if name == 'dst.network' and sid.startswith('d_graylog'):
            graylog[metric] = value
        elif name == 'source' and sid == 's_net' and metric == 'processed':
            relayed = int(value)
        elif name == 'source' and sid == 's_src' and metric == 'processed':
            local = int(value)

    def _int(key: str) -> int | None:
        return int(graylog[key]) if key in graylog else None

    return {
        'queued': _int('queued'),
        'dropped': _int('dropped'),
        'written': _int('written'),
        'eps_1h': graylog.get('eps_last_1h'),
        'relayed': relayed,
        'local': local,
    }


def _stats() -> dict | None:
    """Read + parse the buffer counters, or None when the socket isn't readable.

    Returns:
        The :func:`_parse_stats` dict, or None when ``sudo -n syslog-ng-ctl
        stats`` fails (sudo unavailable in this context, or syslog-ng down).
    """
    rc, out, _ = proc.run(['sudo', '-n', _CTL, 'stats'], timeout=5)
    if rc != 0 or not out:
        return None
    return _parse_stats(out)


def _collect() -> dict:
    """Gather the relay's service/listener/buffer state and the PASS/FAIL checks.

    Returns:
        The document served by ``/api/syslog`` and rendered by the Systems view.
    """
    service_state = proc.service_state('syslog-ng')
    udp, tcp = _listening()
    stats = _stats()

    checks = [
        {'name': 'syslog-ng service', 'ok': service_state == 'active'},
        {'name': 'relay listening (UDP :514)', 'ok': udp},
        {'name': 'relay listening (TCP :514)', 'ok': tcp},
    ]
    # Buffering off-grid (queued > 0, dropped == 0) is healthy; only a buffer
    # overflow loses data. Include the loss check only when stats are readable.
    if stats is not None and stats['dropped'] is not None:
        checks.append({'name': 'no dropped messages', 'ok': stats['dropped'] == 0})

    return {
        'overall_ok': all(c['ok'] for c in checks),
        'checks': checks,
        'service_state': service_state,
        'listening': {'udp': udp, 'tcp': tcp},
        'destination': _DEST,
        'stats': stats,
    }


@status_syslog_bp.get('/api/syslog')
def syslog_api() -> Response:
    """syslog-ng relay + Graylog forward/buffer health as JSON."""
    return jsonify(_collect())
