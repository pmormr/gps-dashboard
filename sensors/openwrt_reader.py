"""OpenWrt router reader: SSH-poll → MQTT publisher (network infra as a sensor).

Polls an OpenWrt router (van-edge; multi-target by design via ``--host/--node``)
over SSH — one ``sh -s`` round-trip per interval running a fixed remote snippet,
marker-parsed into sections (the same mechanism as ``tools/openwrt_probe.py``,
which imports the helpers from here) — and publishes a JSON snapshot to
``sensors/<node>/openwrt``.

Auth is the Pi's SSH key on the target (``BatchMode=yes``: no key → fast fail,
never a password hang). Throughput columns are reader-side deltas of the
``/proc/net/dev`` byte counters — the sensor object carries last-poll state, and
a negative delta (router reboot / counter reset) yields NULL for that cycle and
reseeds. The HaLow link columns come from ``iwinfo`` (the Morse driver serves
standard nl80211 — verified by the Phase-0 probe); on a router without that
radio the columns are NULL and the rest of the snapshot stands.

An unreachable router yields no reading (the ``dropped`` heartbeat counter
names it); ``wan_ping_ms`` NULL with ``wan_up=1`` means the uplink is up but the
internet is dark (off-grid Starlink), which is signal, not failure.

Run::

    uv run sensors/openwrt_reader.py --node van-edge
    uv run sensors/openwrt_reader.py --fake --node van-edge
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

from sensors.runner import (
    Reading,
    SimpleSensor,
    add_publisher_args,
    bounded_walk,
    run_simple_publisher,
)
from sensors.system_reader import parse_mem_used_pct

SENSOR_TYPE = 'openwrt'
READ_INTERVAL_SECONDS = 30.0
DEFAULT_HOST = '192.168.42.1'
DEFAULT_USER = 'root'
DEFAULT_WAN_IFACE = 'wan'
DEFAULT_HALOW_IFACE = 'wlan0'
SSH_TIMEOUT_SECONDS = 20.0
PING_TARGET = '8.8.8.8'

BEGIN_MARK = '### PROBE BEGIN'
END_MARK = '### PROBE END'


def build_remote_script(sections: dict[str, str]) -> str:
    """Assemble one remote sh script with parse markers around each section.

    Args:
        sections: Section name → BusyBox-sh snippet.

    Returns:
        A shell script emitting ``BEGIN name`` / ``END name rc=N`` marker lines
        around each snippet's combined stdout+stderr.
    """
    parts = []
    for name, snippet in sections.items():
        parts.append(
            f'echo "{BEGIN_MARK} {name}"\n( {snippet} ) 2>&1\necho "{END_MARK} {name} rc=$?"'
        )
    return '\n'.join(parts) + '\n'


def parse_marked_sections(output: str) -> dict[str, tuple[int, str]]:
    """Split marker-wrapped remote output back into per-section results.

    Args:
        output: The remote combined output.

    Returns:
        Section name → ``(rc, body)``; sections missing from the output (SSH cut
        short) are absent.
    """
    results: dict[str, tuple[int, str]] = {}
    pattern = re.compile(
        rf'^{re.escape(BEGIN_MARK)} (\S+)\n(.*?)^{re.escape(END_MARK)} \1 rc=(\d+)$',
        re.DOTALL | re.MULTILINE,
    )
    for match in pattern.finditer(output):
        name, body, rc = match.group(1), match.group(2), int(match.group(3))
        results[name] = (rc, body.rstrip('\n'))
    return results


def poll_sections(wan_iface: str, halow_iface: str) -> dict[str, str]:
    """Return the per-poll remote snippet set for one router.

    Args:
        wan_iface: The WAN device name in ``/proc/net/dev``.
        halow_iface: The HaLow radio interface (``iwinfo`` target).

    Returns:
        Section name → snippet, covering every ``openwrt_readings`` source.
    """
    return {
        'loadavg': 'cat /proc/loadavg',
        'meminfo': 'grep -E "^(MemTotal|MemAvailable):" /proc/meminfo',
        'uptime': 'cat /proc/uptime',
        'net_dev': 'cat /proc/net/dev',
        'wan_status': 'ubus call network.interface.wan status',
        'iwinfo_info': f'iwinfo {halow_iface} info',
        'assoclist': f'iwinfo {halow_iface} assoclist',
        'radio_temp': f'morse_cli -i {halow_iface} stats | grep "^Temperature"',
        'dhcp_leases': 'wc -l < /tmp/dhcp.leases',
        'conntrack': 'cat /proc/sys/net/netfilter/nf_conntrack_count',
        'ping': f'ping -c 3 -W 2 -q {PING_TARGET}',
    }


def parse_first_float(text: str) -> float | None:
    """Return the first whitespace-separated field as a float, or None."""
    try:
        return float(text.split()[0])
    except (ValueError, IndexError):
        return None


def parse_int(text: str) -> int | None:
    """Return the stripped text as an int, or None."""
    try:
        return int(text.strip())
    except ValueError:
        return None


def parse_wan_up(text: str) -> int | None:
    """Parse ubus WAN-interface status JSON to a 0/1 up flag.

    Args:
        text: ``ubus call network.interface.wan status`` output.

    Returns:
        1 when the logical interface is up, 0 when down, None on parse failure.
    """
    try:
        return int(bool(json.loads(text)['up']))
    except (ValueError, KeyError, TypeError):
        return None


def parse_net_dev(text: str) -> dict[str, tuple[int, int]]:
    """Parse ``/proc/net/dev`` into per-interface cumulative byte counters.

    Args:
        text: The file contents.

    Returns:
        Interface name → ``(rx_bytes, tx_bytes)``.
    """
    counters: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        name, sep, rest = line.partition(':')
        fields = rest.split()
        if sep and len(fields) >= 9:
            try:
                counters[name.strip()] = (int(fields[0]), int(fields[8]))
            except ValueError:
                continue
    return counters


def parse_iwinfo_signal(text: str) -> tuple[float | None, float | None]:
    """Extract device-level ``(signal_dbm, noise_dbm)`` from ``iwinfo <dev> info``.

    Args:
        text: The ``iwinfo info`` output.

    Returns:
        ``(signal, noise)`` in dBm, each None if absent (radio down / no peer).
    """
    match = re.search(r'Signal:\s*(-?\d+)\s*dBm\s+Noise:\s*(-?\d+)\s*dBm', text)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def parse_assoclist(text: str) -> tuple[int, float | None, float | None]:
    """Extract station count and first-station link rates from an assoclist.

    The first station is the HaLow bridge on the current single-peer link; the
    count column is what says when that assumption stops holding.

    Args:
        text: The ``iwinfo <dev> assoclist`` output.

    Returns:
        ``(station_count, rx_mbps, tx_mbps)`` — rates None with no station.
    """
    stations = len(re.findall(r'^[0-9A-Fa-f:]{17}\s', text, re.MULTILINE))
    rx = re.search(r'RX:\s*([\d.]+)\s*MBit/s', text)
    tx = re.search(r'TX:\s*([\d.]+)\s*MBit/s', text)
    return (
        stations,
        float(rx.group(1)) if rx else None,
        float(tx.group(1)) if tx else None,
    )


def parse_ping_avg_ms(text: str) -> float | None:
    """Extract the average RTT from BusyBox ``ping -q`` output.

    Args:
        text: The ping output.

    Returns:
        Average round-trip ms, or None when unreachable / unparsable.
    """
    match = re.search(r'=\s*[\d.]+/([\d.]+)/', text)
    return float(match.group(1)) if match else None


def parse_radio_temp_c(text: str) -> float | None:
    """Extract the Morse radio die temperature from its grepped stats line.

    The mt7621 SoC has no temp sensor; the MM8108 radio reports its own as
    ``Temperature (C) : 59`` in ``morse_cli stats``.

    Args:
        text: The grepped ``Temperature`` line(s).

    Returns:
        Die temperature in °C, or None (no Morse radio / command absent).
    """
    match = re.search(r'Temperature\s*\(C\)\s*:\s*(-?\d+)', text)
    return float(match.group(1)) if match else None


class ThroughputState:
    """Per-interface byte-counter deltas → kbps (the reader-side rate state)."""

    def __init__(self) -> None:
        self._prev: dict[str, tuple[float, int, int]] = {}

    def rate_kbps(
        self, iface: str, counters: dict[str, tuple[int, int]]
    ) -> tuple[float | None, float | None]:
        """Advance one interface's state and return its ``(rx_kbps, tx_kbps)``.

        The first observation, a missing interface, and a counter that went
        backwards (router reboot / counter reset) all yield ``(None, None)`` —
        the last case also reseeds so the next cycle is a clean delta.

        Args:
            iface: Interface name.
            counters: This cycle's :func:`parse_net_dev` result.

        Returns:
            ``(rx_kbps, tx_kbps)`` for the elapsed window, or Nones.
        """
        now = time.monotonic()
        current = counters.get(iface)
        if current is None:
            self._prev.pop(iface, None)
            return None, None
        prev = self._prev.get(iface)
        self._prev[iface] = (now, *current)
        if prev is None:
            return None, None
        dt = now - prev[0]
        d_rx, d_tx = current[0] - prev[1], current[1] - prev[2]
        if dt <= 0 or d_rx < 0 or d_tx < 0:
            return None, None
        return round(d_rx * 8 / 1000 / dt, 1), round(d_tx * 8 / 1000 / dt, 1)


class OpenwrtSensor:
    """One router's telemetry, gathered over a single SSH round-trip per read."""

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        user: str = DEFAULT_USER,
        wan_iface: str = DEFAULT_WAN_IFACE,
        halow_iface: str = DEFAULT_HALOW_IFACE,
        timeout: float = SSH_TIMEOUT_SECONDS,
    ) -> None:
        """Args:
        host: Router address.
        user: SSH user (root on OpenWrt).
        wan_iface: WAN device in ``/proc/net/dev``.
        halow_iface: HaLow radio interface for ``iwinfo``.
        timeout: Overall SSH timeout per poll.
        """
        self._host = host
        self._user = user
        self._wan_iface = wan_iface
        self._halow_iface = halow_iface
        self._timeout = timeout
        self._script = build_remote_script(poll_sections(wan_iface, halow_iface))
        self._throughput = ThroughputState()

    def _run_remote(self) -> str | None:
        """Run the poll script on the router; None on any SSH-level failure."""
        cmd = [
            'ssh',
            '-o',
            'BatchMode=yes',
            '-o',
            'ConnectTimeout=5',
            '-o',
            'StrictHostKeyChecking=accept-new',
            f'{self._user}@{self._host}',
            'sh -s',
        ]
        try:
            proc = subprocess.run(
                cmd, input=self._script, capture_output=True, text=True, timeout=self._timeout
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        if proc.returncode == 255 or not proc.stdout:
            return None
        return proc.stdout

    def read(self) -> Reading | None:
        """Return one router snapshot, or None when the router is unreachable.

        A section that failed on the router (radio down, missing file) yields
        None for its columns; the rest of the snapshot stands.

        Returns:
            A dict of metric → value matching the ``openwrt`` schema entry.
        """
        output = self._run_remote()
        if output is None:
            return None
        sections = parse_marked_sections(output)

        def body(name: str) -> str:
            rc_body = sections.get(name)
            return rc_body[1] if rc_body and rc_body[0] == 0 else ''

        counters = parse_net_dev(body('net_dev'))
        wan_rx, wan_tx = self._throughput.rate_kbps(self._wan_iface, counters)
        halow_rx, halow_tx = self._throughput.rate_kbps(self._halow_iface, counters)
        rssi, noise = parse_iwinfo_signal(body('iwinfo_info'))
        stations, link_rx, link_tx = parse_assoclist(body('assoclist'))
        return {
            'load_1m': parse_first_float(body('loadavg')),
            'mem_used_pct': parse_mem_used_pct(body('meminfo')),
            'uptime_s': parse_first_float(body('uptime')),
            'wan_up': parse_wan_up(body('wan_status')),
            'wan_rx_kbps': wan_rx,
            'wan_tx_kbps': wan_tx,
            'wan_ping_ms': parse_ping_avg_ms(body('ping')),
            'halow_rx_kbps': halow_rx,
            'halow_tx_kbps': halow_tx,
            'halow_stations': stations if body('assoclist') or body('iwinfo_info') else None,
            'halow_rssi_dbm': rssi,
            'halow_noise_dbm': noise,
            'halow_tx_mbps': link_tx,
            'halow_rx_mbps': link_rx,
            'halow_temp_c': parse_radio_temp_c(body('radio_temp')),
            'dhcp_leases': parse_int(body('dhcp_leases')),
            'conntrack_count': parse_int(body('conntrack')),
        }


class FakeOpenwrtSensor:
    """Synthesize plausible router telemetry for pipeline testing off-LAN."""

    _BASELINES = {
        'load_1m': 0.3,
        'mem_used_pct': 42.0,
        'uptime_s': 120000.0,
        'wan_rx_kbps': 800.0,
        'wan_tx_kbps': 150.0,
        'wan_ping_ms': 45.0,
        'halow_rx_kbps': 120.0,
        'halow_tx_kbps': 300.0,
        'halow_rssi_dbm': -54.0,
        'halow_noise_dbm': -96.0,
        'halow_tx_mbps': 19.5,
        'halow_rx_mbps': 32.5,
        'halow_temp_c': 59.0,
        'dhcp_leases': 9.0,
        'conntrack_count': 150.0,
    }

    def __init__(self) -> None:
        self._state = dict(self._BASELINES)

    def read(self) -> Reading | None:
        """Return one synthetic snapshot, advancing the random walk.

        Returns:
            A dict of metric → value; ``wan_up`` is always 1, one station.
        """
        snapshot: dict[str, float | int | None] = dict(
            bounded_walk(self._state, self._BASELINES, scale=0.02, round_to=1)
        )
        snapshot['wan_up'] = 1
        snapshot['halow_stations'] = 1
        snapshot['dhcp_leases'] = int(self._state['dhcp_leases'])
        snapshot['conntrack_count'] = int(self._state['conntrack_count'])
        return snapshot


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list, or None to read from ``sys.argv``.

    Returns:
        Parsed arguments (host/user/ifaces + the shared publisher args).
    """
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    add_publisher_args(
        parser,
        node_default='van-edge',
        fake_help='Publish synthetic readings instead of SSH-polling a router.',
        once_help='Publish a single reading and exit (for testing).',
    )
    parser.add_argument('--host', default=DEFAULT_HOST, help=f'router address ({DEFAULT_HOST})')
    parser.add_argument('--user', default=DEFAULT_USER, help=f'SSH user ({DEFAULT_USER})')
    parser.add_argument(
        '--wan-iface', default=DEFAULT_WAN_IFACE, help=f'WAN device ({DEFAULT_WAN_IFACE})'
    )
    parser.add_argument(
        '--halow-iface',
        default=DEFAULT_HALOW_IFACE,
        help=f'HaLow radio interface ({DEFAULT_HALOW_IFACE})',
    )
    parser.add_argument(
        '--interval',
        type=float,
        default=READ_INTERVAL_SECONDS,
        help=f'Seconds between polls (default {READ_INTERVAL_SECONDS}).',
    )
    return parser.parse_args(argv)


def main() -> int:
    """Run the reader loop, publishing readings until interrupted.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    args = parse_args()
    sensor: SimpleSensor
    if args.fake:
        sensor = FakeOpenwrtSensor()
    else:
        sensor = OpenwrtSensor(
            host=args.host,
            user=args.user,
            wan_iface=args.wan_iface,
            halow_iface=args.halow_iface,
        )
    run_simple_publisher(
        sensor,
        node=args.node,
        sensor_type=SENSOR_TYPE,
        interval=args.interval,
        once=args.once,
        started_msg=(
            f'openwrt reader started (node={args.node}, host={args.host}, fake={args.fake})'
        ),
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
