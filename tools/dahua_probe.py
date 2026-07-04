"""Dahua fleet probe (kept as a fleet diagnostic).

Read-only CGI survey for the Dahua reader stream: digest-auth GET every
candidate HTTP-API endpoint on the NVR and each active camera, and print the
raw responses with an availability summary. The output is what locks the
``nvr_readings``/``camera_readings`` column sets — which endpoints this
firmware actually serves, and what each returns per device class.

The shared WebUI password comes from ``GPS_DAHUA_PASSWORD`` or an interactive
prompt — never argv (visible in ``ps``), matching the eventual
``/etc/default/gps-dahua`` secret model.

Run on the Pi (the production vantage point)::

    GPS_DAHUA_PASSWORD=... uv run tools/dahua_probe.py          # whole fleet
    uv run tools/dahua_probe.py --host 192.168.42.50            # one device
    uv run tools/dahua_probe.py --endpoint storage --full
"""

from __future__ import annotations

import argparse
import getpass
import os
from dataclasses import dataclass

import requests
import urllib3
from requests.auth import HTTPDigestAuth

from common.cli import run_cli
from sensors.dahua_reader import FLEET, Device

# The NVR redirects HTTP→HTTPS with a self-signed cert; these are LAN devices,
# so skip verification rather than pinning.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TRUNCATE_CHARS = 2000


@dataclass(frozen=True)
class Endpoint:
    """One candidate CGI endpoint."""

    name: str
    path: str
    nvr_only: bool = False


#: Candidate endpoints, per the published Dahua HTTP API. Which of these this
#: firmware serves (and with what fields) is exactly what the probe answers —
#: a 400/404 is a per-endpoint gap, not an error.
ENDPOINTS: tuple[Endpoint, ...] = (
    # Identity/context (confirms device + firmware; not columns).
    Endpoint('system_info', '/cgi-bin/magicBox.cgi?action=getSystemInfo'),
    Endpoint('device_type', '/cgi-bin/magicBox.cgi?action=getDeviceType'),
    Endpoint('software_version', '/cgi-bin/magicBox.cgi?action=getSoftwareVersion'),
    # Uptime — candidate column on both device classes.
    Endpoint('uptime', '/cgi-bin/magicBox.cgi?action=getUpTime'),
    # Clock sanity (cameras NTP-sync from the NVR; drift is a candidate column).
    Endpoint('current_time', '/cgi-bin/global.cgi?action=getCurrentTime'),
    # Host resources — served by some firmwares only; absence is a finding.
    Endpoint('memory_info', '/cgi-bin/magicBox.cgi?action=getMemoryInfo'),
    Endpoint('cpu_usage', '/cgi-bin/magicBox.cgi?action=getCPUUsage'),
    # Storage/HDD health — the core NVR columns (cams may answer for SD slots).
    Endpoint('storage', '/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo'),
    # Recording mode per channel (config; the state complement is camera_state).
    Endpoint('record_mode', '/cgi-bin/configManager.cgi?action=getConfig&name=RecordMode'),
    # NVR's own view of every channel. getCameraState is 501 on this firmware;
    # the live camera-down signal is the VideoLoss event-index list ("No Events"
    # = all up), and getCameraAll is the identity/config table.
    Endpoint(
        'video_loss',
        '/cgi-bin/eventManager.cgi?action=getEventIndexes&code=VideoLoss',
        nvr_only=True,
    ),
    Endpoint(
        'camera_all',
        '/cgi-bin/LogicDeviceManager.cgi?action=getCameraAll',
        nvr_only=True,
    ),
    Endpoint(
        'channel_titles',
        '/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle',
        nvr_only=True,
    ),
)


def fetch(
    session: requests.Session, device: Device, endpoint: Endpoint, timeout: float
) -> tuple[str, str]:
    """GET one endpoint on one device.

    Args:
        session: Authed session (digest state persists across requests).
        device: Target device.
        endpoint: Candidate endpoint.
        timeout: Per-request timeout in seconds.

    Returns:
        ``(verdict, body)`` — verdict is ``ok``, ``HTTP <code>``, or an error
        class name; body is the (possibly empty) response text.
    """
    url = f'http://{device.host}{endpoint.path}'
    try:
        resp = session.get(url, timeout=timeout, verify=False)
    except requests.RequestException as exc:
        return type(exc).__name__, str(exc)
    if resp.status_code == 200:
        return 'ok', resp.text.strip()
    return f'HTTP {resp.status_code}', resp.text.strip()


def probe_device(
    device: Device, password: str, endpoints: tuple[Endpoint, ...], timeout: float, full: bool
) -> dict[str, str]:
    """Probe every applicable endpoint on one device, printing raw responses.

    Args:
        device: Target device.
        password: Shared WebUI admin password.
        endpoints: Candidate endpoints (NVR-only ones skipped on cameras).
        timeout: Per-request timeout in seconds.
        full: Print full bodies instead of truncating at ``TRUNCATE_CHARS``.

    Returns:
        Endpoint name → verdict, for the summary table.
    """
    print(f'\n{"=" * 60}\n{device.node} ({device.host})\n{"=" * 60}')
    verdicts: dict[str, str] = {}
    with requests.Session() as session:
        session.auth = HTTPDigestAuth('admin', password)
        for endpoint in endpoints:
            if endpoint.nvr_only and not device.is_nvr:
                continue
            verdict, body = fetch(session, device, endpoint, timeout)
            verdicts[endpoint.name] = verdict
            print(f'\n--- {endpoint.name} [{verdict}] {endpoint.path}')
            if body:
                shown = (
                    body
                    if full or len(body) <= TRUNCATE_CHARS
                    else (
                        body[:TRUNCATE_CHARS] + f'\n… (+{len(body) - TRUNCATE_CHARS} chars, --full)'
                    )
                )
                print(shown)
    return verdicts


def print_summary(all_verdicts: dict[str, dict[str, str]]) -> None:
    """Print the device × endpoint availability matrix.

    Args:
        all_verdicts: Node name → (endpoint name → verdict).
    """
    names = [e.name for e in ENDPOINTS]
    width = max(len(n) for n in names)
    print(f'\n=== availability summary ===\n{"":{width}s}  ' + '  '.join(all_verdicts))
    for name in names:
        cells = []
        for node in all_verdicts:
            verdict = all_verdicts[node].get(name, '-')
            cells.append(f'{("ok" if verdict == "ok" else verdict):{len(node)}s}')
        print(f'{name:{width}s}  ' + '  '.join(cells))


def main() -> int:
    """Parse args, resolve the secret, probe the fleet, and print the matrix."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--host', help='probe a single device by address (default: whole fleet)')
    parser.add_argument(
        '--endpoint',
        action='append',
        choices=sorted(e.name for e in ENDPOINTS),
        help='probe only this endpoint (repeatable; default all)',
    )
    parser.add_argument(
        '--timeout', type=float, default=5.0, help='per-request timeout seconds (default 5)'
    )
    parser.add_argument('--full', action='store_true', help='print full bodies (no truncation)')
    args = parser.parse_args()

    password = os.environ.get('GPS_DAHUA_PASSWORD') or getpass.getpass('Dahua admin password: ')
    if not password:
        parser.error('empty password')

    if args.host:
        known = {d.host: d for d in FLEET}
        devices: tuple[Device, ...] = (
            (known[args.host],) if args.host in known else (Device(args.host, args.host, True),)
        )
    else:
        devices = FLEET
    endpoints = (
        tuple(e for e in ENDPOINTS if e.name in set(args.endpoint)) if args.endpoint else ENDPOINTS
    )

    all_verdicts: dict[str, dict[str, str]] = {}
    for device in devices:
        all_verdicts[device.node] = probe_device(
            device, password, endpoints, args.timeout, args.full
        )
    print_summary(all_verdicts)
    return 0


if __name__ == '__main__':
    run_cli(main)
