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

from sensors.dahua_rpc import Rpc2Error, Rpc2Session
from sensors.runner import Reading, run_fleet_publisher

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

READ_INTERVAL_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 5.0

PATH_STORAGE = '/cgi-bin/storageDevice.cgi?action=getDeviceAllInfo'
PATH_VIDEO_LOSS = '/cgi-bin/eventManager.cgi?action=getEventIndexes&code=VideoLoss'
PATH_CURRENT_TIME = '/cgi-bin/global.cgi?action=getCurrentTime'
PATH_RECORD_MODE = '/cgi-bin/configManager.cgi?action=getConfig&name=RecordMode'


#: The NVR's HDD device name for the SMART query (single-disk unit).
NVR_HDD_NAME = '/dev/sda'


@dataclass(frozen=True)
class Device:
    """One fleet member."""

    node: str
    host: str
    is_nvr: bool

    @property
    def rpc_base(self) -> str:
        """The device's RPC2 base URL — the NVR only speaks RPC2 over HTTPS."""
        return f'{"https" if self.is_nvr else "http"}://{self.host}'


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
    """Parse ``getCurrentTime`` and return the device's clock drift in seconds.

    ``getCurrentTime`` returns the device's *local display time*, and the fleet's
    TZ settings are inconsistent (live 2026-07-03: NVR −4 h, cameras −5 h vs UTC),
    so a raw ``device − Pi`` difference measures timezone config, not clock
    health. Instead the offset is folded to the nearest whole hour: an NTP-synced
    device reads within seconds of some whole-hour offset; a drifting clock walks
    away from it. Assumes no :30/:45 timezone — true for this installation.

    Args:
        text: A body like ``result=2026-07-02 19:09:25``.
        now_utc: The Pi-side UTC timestamp taken at fetch time.

    Returns:
        Drift from the nearest whole-hour offset in ``[-1800, 1800)`` seconds
        (positive = device ahead), or None on parse failure.
    """
    _, _, value = text.partition('=')
    try:
        device_dt = datetime.strptime(value.strip(), '%Y-%m-%d %H:%M:%S').replace(tzinfo=UTC)
    except ValueError:
        return None
    offset = (device_dt - now_utc).total_seconds()
    return round((offset + 1800) % 3600 - 1800, 1)


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


def mem_used_pct_from(info: dict[str, float]) -> float | None:
    """Compute used-memory percent from an RPC2 ``getMemoryInfo`` reply.

    Args:
        info: The reply params (``free``/``total`` bytes).

    Returns:
        Percent used rounded to 0.1, or None when the fields are missing/zero.
    """
    total, free = info.get('total'), info.get('free')
    if not total or free is None:
        return None
    return round((1 - free / total) * 100, 1)


def smart_metrics(values: list[dict[str, object]]) -> tuple[float | None, int | None, int | None]:
    """Extract the health columns from an RPC2 ``getSmartValue`` attribute list.

    Args:
        values: SMART attribute dicts (``ID``/``Name``/``Raw``/…).

    Returns:
        ``(temp_c, realloc_sectors, power_on_hours)`` from attributes 194/5/9,
        each None when absent or unparsable.
    """
    raw_by_id: dict[object, object] = {v.get('ID'): v.get('Raw') for v in values}

    def as_int(attr_id: int) -> int | None:
        try:
            return int(str(raw_by_id[attr_id]))
        except (KeyError, ValueError):
            return None

    temp = as_int(194)
    return (float(temp) if temp is not None else None, as_int(5), as_int(9))


class DahuaFleetSensor:
    """The recording fleet's health, polled device-by-device each read.

    Two protocols per cycle: the legacy CGI for what it serves (storage state,
    VideoLoss, clock, record mode) and an RPC2 session per device for what it
    doesn't (CPU/memory everywhere, HDD SMART on the NVR, uptime on cameras).
    RPC2 failure NULLs its columns without touching the CGI ones.
    """

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
        self._password = password
        self._session = requests.Session()
        self._session.auth = HTTPDigestAuth('admin', password)

    def _fetch(self, host: str, path: str) -> str | None:
        """GET one endpoint; body on 200, None on an HTTP error status.

        Connection-level failures (``requests.RequestException``) propagate to
        the per-device early-out in :meth:`read`.
        """
        resp = self._session.get(f'http://{host}{path}', timeout=self._timeout, verify=False)
        return resp.text if resp.status_code == 200 else None

    def _rpc_nvr_metrics(self, device: Device) -> dict[str, float | int | None]:
        """RPC2 host + SMART metrics for the NVR; all None on any RPC2 failure."""
        empty: dict[str, float | int | None] = dict.fromkeys(
            ('cpu_pct', 'mem_used_pct', 'hdd_temp_c', 'hdd_realloc_sectors', 'hdd_power_on_h')
        )
        try:
            with Rpc2Session(
                device.rpc_base, 'admin', self._password, timeout=self._timeout
            ) as rpc:
                cpu = rpc.call('magicBox.getCPUUsage', {'index': 0})['params']
                mem = rpc.call('magicBox.getMemoryInfo')['params']
                handle = rpc.call('devStorage.factory.instance', {'name': NVR_HDD_NAME})['result']
                values = rpc.call('devStorage.getSmartValue', obj=handle)['params']['values']
                rpc.call('devStorage.destroy', obj=handle, check=False)
        except (requests.RequestException, Rpc2Error, KeyError, TypeError, ValueError):
            return empty
        temp_c, realloc, power_on_h = smart_metrics(values)
        return {
            'cpu_pct': cpu.get('usage'),
            'mem_used_pct': mem_used_pct_from(mem),
            'hdd_temp_c': temp_c,
            'hdd_realloc_sectors': realloc,
            'hdd_power_on_h': power_on_h,
        }

    def _rpc_camera_metrics(self, device: Device) -> dict[str, float | int | None]:
        """RPC2 host metrics for one camera; all None on any RPC2 failure.

        ``getUpTime``'s field names are misleading on this firmware: ``Total``
        resets on boot (the uptime we want) while ``Last`` accumulates across
        boots — verified live by ``Total < Last`` with both advancing 1 s/s.
        """
        empty: dict[str, float | int | None] = dict.fromkeys(
            ('cpu_pct', 'mem_used_pct', 'uptime_s')
        )
        try:
            with Rpc2Session(
                device.rpc_base, 'admin', self._password, timeout=self._timeout
            ) as rpc:
                cpu = rpc.call('magicBox.getCPUUsage', {'index': 0})['params']
                mem = rpc.call('magicBox.getMemoryInfo')['params']
                uptime = rpc.call('magicBox.getUpTime')['params']['info']
        except (requests.RequestException, Rpc2Error, KeyError, TypeError, ValueError):
            return empty
        return {
            'cpu_pct': cpu.get('usage'),
            'mem_used_pct': mem_used_pct_from(mem),
            'uptime_s': uptime.get('Total'),
        }

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
            **self._rpc_nvr_metrics(device),
        }

    def _read_camera(self, device: Device) -> Reading:
        """Poll one camera; an unreachable camera is an ``online=0`` row."""
        offline: dict[str, float | int | None] = dict.fromkeys(
            ('clock_offset_s', 'record_mode', 'cpu_pct', 'mem_used_pct', 'uptime_s')
        )
        try:
            now_utc = datetime.now(UTC)
            current_time = self._fetch(device.host, PATH_CURRENT_TIME)
            record_mode = self._fetch(device.host, PATH_RECORD_MODE)
        except requests.RequestException:
            return {'online': 0, **offline}
        return {
            'online': 1,
            'clock_offset_s': (
                parse_clock_offset_s(current_time, now_utc) if current_time else None
            ),
            'record_mode': parse_record_mode(record_mode or ''),
            **self._rpc_camera_metrics(device),
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
                    'cpu_pct': round(random.uniform(4, 12)),
                    'mem_used_pct': round(random.uniform(40, 50), 1),
                    'hdd_temp_c': round(random.uniform(42, 52)),
                    'hdd_realloc_sectors': 0,
                    'hdd_power_on_h': 22376,
                }
            else:
                readings[device.node] = {
                    'online': 1,
                    'clock_offset_s': offset,
                    'record_mode': 0,
                    'cpu_pct': round(random.uniform(10, 20)),
                    'mem_used_pct': round(random.uniform(50, 60), 1),
                    'uptime_s': 170000,
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
