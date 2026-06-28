import re

from flask import Blueprint, jsonify

from common import proc

status_ntp_bp = Blueprint('status_ntp', __name__)


_TRACKING_DEFAULTS = {
    'reference': None,
    'synced': False,
    'stratum': None,
    'offset_ms': None,
    'offset_dir': None,
    'rms_ms': None,
    'leap_status': None,
}


def _parse_tracking():
    _, out, _ = proc.run(['chronyc', 'tracking'])
    if not out:
        return dict(_TRACKING_DEFAULTS)

    def _field(pattern):
        m = re.search(pattern, out, re.IGNORECASE)
        return m.group(1).strip() if m else None

    ref_match = re.search(r'Reference ID\s*:\s*\S+\s*\((.+?)\)', out)
    offset_match = re.search(r'System time\s*:\s*([\d.]+)\s*seconds\s*(fast|slow)', out)
    stratum_match = re.search(r'Stratum\s*:\s*(\d+)', out)
    rms_match = re.search(r'RMS offset\s*:\s*([\d.]+(?:e[+-]?\d+)?)\s*seconds', out)

    offset_ms = None
    offset_dir = None
    if offset_match:
        offset_ms = float(offset_match.group(1)) * 1000
        offset_dir = offset_match.group(2)

    return {
        'reference': ref_match.group(1) if ref_match else None,
        'synced': 'Not synchronised' not in out and bool(ref_match),
        'stratum': int(stratum_match.group(1)) if stratum_match else None,
        'offset_ms': offset_ms,
        'offset_dir': offset_dir,
        'rms_ms': float(rms_match.group(1)) * 1000 if rms_match else None,
        'leap_status': _field(r'Leap status\s*:\s*(.+)'),
    }


def _parse_sources():
    _, out, _ = proc.run(['chronyc', 'sources'])
    sources = []
    for line in out.splitlines():
        # Lines starting with # are reference clocks (GPS/PPS)
        m = re.match(r'([#^])([\*\+\-\?x ])\s+(\S+)\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.*)', line)
        if m:
            sources.append(
                {
                    'type': 'refclock' if m.group(1) == '#' else 'server',
                    'selected': m.group(2) == '*',
                    'name': m.group(3),
                    'stratum': int(m.group(4)),
                    'sample': m.group(5).strip(),
                }
            )
    return sources


_CONFLICTING = ['ntpd', 'ntp', 'systemd-timesyncd', 'openntpd']


def _conflicting_services():
    return [svc for svc in _CONFLICTING if proc.service_active(svc)]


def _ntp_serving():
    _, out, _ = proc.run(['ss', '-lnup'])
    return bool(re.search(r'[*\d]:123\s', out))


def _collect() -> dict:
    """Gather chrony sync state, sources, and the derived PASS/FAIL checks.

    Returns:
        The NTP status document served by ``/api/ntp`` and rendered by the Van OS
        Systems view: service state, tracking summary, sources, the check list,
        and the GPS/PPS mode flags.
    """
    service_state = proc.service_state('chrony')
    tracking = _parse_tracking()
    sources = _parse_sources()
    serving = _ntp_serving()
    conflicts = _conflicting_services()

    gps_source = next((s for s in sources if 'GPS' in s['name']), None)
    pps_source = next((s for s in sources if 'PPS' in s['name']), None)
    pps_mode = pps_source is not None

    checks = [
        {'name': 'no conflicting services', 'ok': not conflicts},
        {'name': 'chrony service', 'ok': service_state == 'active'},
        {'name': 'GPS SHM source', 'ok': gps_source is not None},
        {'name': 'synchronised', 'ok': tracking.get('synced', False)},
        {'name': 'stratum ≤ 10', 'ok': (tracking.get('stratum') or 99) <= 10},
        {'name': 'NTP serving (LAN)', 'ok': serving},
    ]
    if pps_mode:
        checks.insert(
            3, {'name': 'PPS source selected', 'ok': bool(pps_source and pps_source['selected'])}
        )

    return {
        'overall_ok': all(c['ok'] for c in checks),
        'checks': checks,
        'service_state': service_state,
        'tracking': tracking,
        'sources': sources,
        'gps_source': gps_source,
        'pps_source': pps_source,
        'pps_mode': pps_mode,
        'serving': serving,
        'conflicts': conflicts,
    }


@status_ntp_bp.get('/api/ntp')
def ntp_api():
    """NTP/chrony status as JSON for the Van OS Systems view."""
    return jsonify(_collect())
