"""Dahua fleet reader: HTTP-CGI poll (NVR + cameras) → multi-node MQTT publisher.

One process polls the whole recording fleet each interval — the NVR (node
``van-nvr``, type ``nvr``) and each active camera (nodes ``van-cam-*``, type
``camera``) — and publishes one reading per device through
:func:`sensors.runner.run_fleet_publisher` (a session per stream; see runner.py
for the LWT rationale). ``tools/dahua_probe.py`` imports the fleet table from
here.

Auth is digest with the shared WebUI password from ``GPS_DAHUA_PASSWORD``
(deployed via ``/etc/default/gps-dahua``, root-owned 600, out of git). The NVR
redirects HTTP→HTTPS with a self-signed cert, so requests run ``verify=False``
— these are LAN devices.

Failure semantics differ by device class, matching the schema: an unreachable
*camera* still publishes a row (``online=0``, other columns NULL) so outages
chart; an unreachable *NVR* is a dropped reading. A device's first
connection-level error ends its poll for the cycle (early-out), so a dark
fleet costs one timeout per device, not one per endpoint.

Run::

    GPS_DAHUA_PASSWORD=... uv run sensors/dahua_reader.py --once
    uv run sensors/dahua_reader.py --fake
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

import requests
import urllib3
from requests.auth import HTTPDigestAuth

from sensors.runner import Reading, run_fleet_publisher

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

READ_INTERVAL_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 5.0

PATH_STORAGE = '/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo'
PATH_VIDEO_LOSS = '/cgi-bin/eventManager.cgi?action=getEventIndexes&code=VideoLoss'
PATH_CURRENT_TIME = '/cgi-bin/global.cgi?action=getCurrentTime'
PATH_RECORD_MODE = '/cgi-bin/configManager.cgi?action=getConfig&name=RecordMode'


@dataclass(frozen=True)
class Device:
    """One fleet member."""

    node: str
    host: str
    is_nvr: bool


#: The active Dahua fleet (vault hostnames). Hikvision cams (.55/.56) are out of
#: scope — different API (ISAPI), not recording.
FLEET: tuple[Device, ...] = (
    Device('van-nvr', '192.168.42.50', is_nvr=True),
    Device('van-cam-front', '192.168.42.51', is_nvr=False),
    Device('van-cam-blind-left', '192.168.42.52', is_nvr=False),
    Device('van-cam-blind-right', '192.168.42.53', is_nvr=False),
    Device('van-cam-rear', '192.168.42.54', is_nvr=False),
)

#: Node → sensor type, the ``run_fleet_publisher`` stream map.
FLEET_STREAMS: dict[str, str] = {d.node: ('nvr' if d.is_nvr else 'camera') for d in FLEET}


def parse_storage(text: str) -> tuple[int | None, int | None]:
    """Parse ``storageDevice getDeviceAllInfo`` into the two HDD health columns.

    Args:
        text: The key=value response body.

    Returns:
        ``(hdd_ok, err_partitions)`` — ``hdd_ok`` is 1 iff every storage device
        reports ``State=Success``; ``err_partitions`` counts partition Details
        flagged ``IsError=true``. ``(None, None)`` when no storage lines parse.
    """
    states = [line.split('=', 1)[1].strip() for line in text.splitlines() if '.State=' in line]
    if not states:
        return None, None
    errors = sum(1 for line in text.splitlines() if line.rstrip().endswith('.IsError=true'))
    return int(all(state == 'Success' for state in states)), errors


def parse_video_loss(text: str) -> int | None:
    """Parse ``getEventIndexes&code=VideoLoss`` into a down-channel count.

    Args:
        text: The response body — ``Error: No Events`` when every channel is
            delivering video, else ``channels[i]=N`` lines.

    Returns:
        The number of channels flagged VideoLoss (0 = all recording), or None
        when the body matches neither form.
    """
    if 'No Events' in text:
        return 0
    count = len([line for line in text.splitlines() if line.strip().startswith('channels[')])
    return count if count else None


def parse_clock_offset_s(text: str, now_utc: datetime) -> float | None:
    """Parse ``getCurrentTime`` and return device clock − Pi clock in seconds.

    The fleet's device clocks are UTC (the NVR's TZ is UTC and the cameras
    NTP-sync from it; the malformed camera TZ strings affect WebUI display
    only), so the returned wall time compares directly.

    Args:
        text: A body like ``result=2026-07-02 19:09:25``.
        now_utc: The Pi-side UTC timestamp taken at fetch time.

    Returns:
        Offset in seconds (positive = device ahead), or None on parse failure.
    """
    _, _, value = text.partition('=')
    try:
        device_dt = datetime.strptime(value.strip(), '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)
    except ValueError:
        return None
    return round((device_dt - now_utc).total_seconds(), 1)


def parse_record_mode(text: str) -> int | None:
    """Parse the first channel's mode from a ``RecordMode`` config body.

    Args:
        text: ``table.RecordMode[i].Mode=N`` lines.

    Returns:
        The mode enum (0 auto / 1 manual / 2 off), or None on parse failure.
    """
    for line in text.splitlines():
        if '.Mode=' in line:
            try:
                return int(line.split('=', 1)[1].strip())
            except ValueError:
                return None
    return None


class DahuaFleetSensor:
    """The recording fleet's health, polled device-by-device each read."""

    def __init__(
        self,
        password: str,
        *,
        fleet: tuple[Device, ...] = FLEET,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        """Args:
        password: The shared WebUI admin password (digest auth).
        fleet: Devices to poll.
        timeout: Per-request timeout in seconds.
        """
        self._fleet = fleet
        self._timeout = timeout
        self._session = requests.Session()
        self._session.auth = HTTPDigestAuth('admin', password)

    def _fetch(self, host: str, path: str) -> str | None:
        """GET one endpoint; body on 200, None on an HTTP error status.

        Connection-level failures (``requests.RequestException``) propagate to
        the per-device early-out in :meth:`read`.
        """
        resp = self._session.get(f'http://{host}{path}', timeout=self._timeout, verify=False)
        return resp.text if resp.status_code == 200 else None

    def _read_nvr(self, device: Device) -> Reading | None:
        """Poll the NVR; None (dropped reading) when unreachable."""
        try:
            storage = self._fetch(device.host, PATH_STORAGE)
            video_loss = self._fetch(device.host, PATH_VIDEO_LOSS)
            now_utc = datetime.now(UTC)
            current_time = self._fetch(device.host, PATH_CURRENT_TIME)
        except requests.RequestException:
            return None
        hdd_ok, err_partitions = parse_storage(storage or '')
        return {
            'hdd_ok': hdd_ok,
            'hdd_err_partitions': err_partitions,
            'channels_video_loss': parse_video_loss(video_loss or ''),
            'clock_offset_s': (
                parse_clock_offset_s(current_time, now_utc) if current_time else None
            ),
        }

    def _read_camera(self, device: Device) -> Reading:
        """Poll one camera; an unreachable camera is an ``online=0`` row."""
        try:
            now_utc = datetime.now(UTC)
            current_time = self._fetch(device.host, PATH_CURRENT_TIME)
            record_mode = self._fetch(device.host, PATH_RECORD_MODE)
        except requests.RequestException:
            return {'online': 0, 'clock_offset_s': None, 'record_mode': None}
        return {
            'online': 1,
            'clock_offset_s': (
                parse_clock_offset_s(current_time, now_utc) if current_time else None
            ),
            'record_mode': parse_record_mode(record_mode or ''),
        }

    def read(self) -> dict[str, Reading | None]:
        """Return one fleet snapshot: node → reading (None = NVR unreachable)."""
        readings: dict[str, Reading | None] = {}
        for device in self._fleet:
            if device.is_nvr:
                readings[device.node] = self._read_nvr(device)
            else:
                readings[device.node] = self._read_camera(device)
        return readings


class FakeDahuaFleetSensor:
    """Synthesize a healthy fleet for pipeline testing off-LAN."""

    def read(self) -> dict[str, Reading | None]:
        """Return one synthetic fleet snapshot (all up, tiny clock jitter)."""
        readings: dict[str, Reading | None] = {}
        for device in FLEET:
            offset = round(random.uniform(-1.5, 1.5), 1)
            if device.is_nvr:
                readings[device.node] = {
                    'hdd_ok': 1,
                    'hdd_err_partitions': 0,
                    'channels_video_loss': 0,
                    'clock_offset_s': offset,
                }
            else:
                readings[device.node] = {
                    'online': 1,
                    'clock_offset_s': offset,
                    'record_mode': 0,
                }
        return readings


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list, or None to read from ``sys.argv``.

    Returns:
        Parsed arguments with ``fake``, ``once``, and ``interval``.

    Note:
        No ``--node``: the fleet's nodes are fixed by :data:`FLEET`.
    """
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument(
        '--fake', action='store_true', help='Publish synthetic readings instead of CGI-polling.'
    )
    parser.add_argument(
        '--once', action='store_true', help='Publish a single fleet reading and exit.'
    )
    parser.add_argument(
        '--interval',
        type=float,
        default=READ_INTERVAL_SECONDS,
        help=f'Seconds between fleet polls (default {READ_INTERVAL_SECONDS}).',
    )
    return parser.parse_args(argv)


def main() -> int:
    """Run the fleet reader loop, publishing readings until interrupted.

    Returns:
        Process exit code: 0 on graceful shutdown, 2 when the secret is missing.
    """
    args = parse_args()
    sensor: DahuaFleetSensor | FakeDahuaFleetSensor
    if args.fake:
        sensor = FakeDahuaFleetSensor()
    else:
        password = os.environ.get('GPS_DAHUA_PASSWORD', '')
        if not password:
            print(
                'GPS_DAHUA_PASSWORD is not set (deployed via /etc/default/gps-dahua)',
                file=sys.stderr,
            )
            return 2
        sensor = DahuaFleetSensor(password)
    run_fleet_publisher(
        sensor,
        streams=FLEET_STREAMS,
        interval=args.interval,
        once=args.once,
        started_msg=f'dahua fleet reader started ({len(FLEET)} devices, fake={args.fake})',
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
