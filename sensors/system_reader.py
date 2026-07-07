"""Raspberry Pi host-metrics reader: /proc + /sys + vcgencmd → MQTT publisher.

Reads the Pi's own system health on a fixed interval and publishes a JSON snapshot
to ``sensors/<node>/system``, registering a retained LWT on ``.../status`` so the
broker flips the stream to ``offline`` if this process dies ungracefully.

The Pi is the one part of the platform that wasn't reporting on itself. Unlike the
other readers there is no external source, driver library, or secret: every metric
is a stdlib read of ``/proc``/``/sys``, ``os.statvfs``, ``os.getloadavg``, or the
Pi's ``vcgencmd``. Each read is defensive — a source that is absent (running on a
dev laptop, no NVMe mount, no ``vcgencmd``) yields ``None`` for that one metric
rather than failing the whole snapshot, so the reader degrades cleanly off-Pi.

Not gated: the Pi is always on, so this runs continuously like the Victron reader.

A ``--fake`` mode synthesizes plausible readings so the MQTT → SQLite pipeline can
be exercised without a Pi.

Run::

    uv run sensors/system_reader.py --node pi
    uv run sensors/system_reader.py --fake --node pi
"""

from __future__ import annotations

import argparse
import os
import sys

from common.proc import run
from sensors.runner import (
    Reading,
    SimpleSensor,
    add_publisher_args,
    bounded_walk,
    run_simple_publisher,
)

SENSOR_TYPE = 'system'
READ_INTERVAL_SECONDS = 30.0

#: Filesystems reported. Root is fixed; the NVMe mount (DB + tiles live here) is
#: env-overridable in case it ever moves. The partition set is fixed on this host,
#: so named columns beat a generic disk_1/disk_2 (see api/sensor_schema.py).
ROOT_PATH = '/'
NVME_PATH = os.environ.get('GPS_SYSTEM_NVME_PATH', '/mnt/nvme')

#: SoC thermal zone (thermal_zone0 is the CPU on the Pi); millidegrees Celsius.
CPU_TEMP_PATH = '/sys/class/thermal/thermal_zone0/temp'


def read_cpu_temp_c(path: str = CPU_TEMP_PATH) -> float | None:
    """Return the CPU temperature in °C, or None if the thermal zone is absent.

    Args:
        path: The sysfs thermal-zone temperature file (millidegrees Celsius).

    Returns:
        Temperature in °C rounded to 0.1, or None off-Pi / on a read error.
    """
    try:
        with open(path) as fh:
            return round(int(fh.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def read_load_1m() -> float | None:
    """Return the 1-minute load average, or None where it isn't available."""
    try:
        return round(os.getloadavg()[0], 2)
    except OSError:
        return None


def parse_mem_used_pct(meminfo: str) -> float | None:
    """Compute memory-used percent from ``/proc/meminfo`` text.

    Uses ``MemAvailable`` (the kernel's estimate of reclaimable + free) rather than
    ``MemFree``, so cache/buffers don't read as "used".

    Args:
        meminfo: The contents of ``/proc/meminfo``.

    Returns:
        Percent of RAM in use rounded to 0.1, or None if the fields are missing.
    """
    fields: dict[str, float] = {}
    for line in meminfo.splitlines():
        key, _, rest = line.partition(':')
        parts = rest.split()
        if parts:
            fields[key] = float(parts[0])
    total = fields.get('MemTotal')
    available = fields.get('MemAvailable')
    if not total or available is None:
        return None
    return round((1 - available / total) * 100, 1)


def read_mem_used_pct(path: str = '/proc/meminfo') -> float | None:
    """Return memory-used percent from ``/proc/meminfo``, or None off-Linux."""
    try:
        with open(path) as fh:
            return parse_mem_used_pct(fh.read())
    except OSError:
        return None


def disk_usage(path: str) -> tuple[float | None, float | None]:
    """Return ``(used_percent, free_gb)`` for the filesystem containing ``path``.

    Percent matches ``df`` (computed against the user-available space, so reserved
    blocks aren't counted as free); free space is the non-root-available bytes.

    Args:
        path: A path on the target filesystem (its mount point).

    Returns:
        ``(used_percent, free_gb)``, each rounded, or ``(None, None)`` if the path
        isn't mounted / can't be stat'd.
    """
    try:
        st = os.statvfs(path)
    except OSError:
        return None, None
    used_blocks = st.f_blocks - st.f_bfree
    denom = used_blocks + st.f_bavail
    if denom <= 0:
        return None, None
    used_pct = round(used_blocks / denom * 100, 1)
    free_gb = round(st.f_bavail * st.f_frsize / 1e9, 1)
    return used_pct, free_gb


def read_uptime_s(path: str = '/proc/uptime') -> float | None:
    """Return seconds since boot from ``/proc/uptime``, or None off-Linux."""
    try:
        with open(path) as fh:
            return round(float(fh.read().split()[0]), 0)
    except (OSError, ValueError, IndexError):
        return None


def parse_throttled(output: str) -> int | None:
    """Parse the ``vcgencmd get_throttled`` bitmask from its output.

    The output is ``throttled=0x<hex>``; the returned int is the raw bitmask
    (0 = healthy). Bits flag under-voltage / frequency-cap / throttling, both
    currently-active (low bits) and since-boot (bits 16+).

    Args:
        output: A line like ``throttled=0x50005`` (the ``=`` prefix is optional).

    Returns:
        The bitmask as an int, or None if the value can't be parsed.
    """
    value = output.strip()
    if '=' in value:
        value = value.split('=', 1)[1].strip()
    try:
        return int(value, 0) if value else None
    except ValueError:
        return None


def read_throttled() -> int | None:
    """Return the Pi's throttled bitmask via ``vcgencmd``, or None off-Pi."""
    rc, out, _ = run(['vcgencmd', 'get_throttled'], timeout=5)
    if rc != 0:
        return None
    return parse_throttled(out)


#: Live (currently-active) bits of the throttled bitmask, published as their own 0/1
#: channels so "when was it throttled/overheated/under-volt" is answerable from the
#: reading history. The sticky since-boot bits (16+) are deliberately not split: they
#: only clear on reboot, so they carry no time information beyond the raw bitmask.
LIVE_THROTTLE_CHANNELS: dict[str, int] = {
    'undervolt_now': 0x1,
    'freq_capped_now': 0x2,
    'throttled_now': 0x4,
    'temp_limit_now': 0x8,
}


def split_live_throttle(mask: int | None) -> dict[str, int | None]:
    """Split the live throttle bits into per-condition 0/1 channels.

    Sampled at poll time, so a condition shorter than the read interval can fall
    between snapshots — an accepted trade-off; the sticky bits still latch those.

    Args:
        mask: The raw ``get_throttled`` bitmask, or None off-Pi.

    Returns:
        ``{channel: 0|1}`` per :data:`LIVE_THROTTLE_CHANNELS`, all None when the
        bitmask itself is unavailable.
    """
    if mask is None:
        return dict.fromkeys(LIVE_THROTTLE_CHANNELS)
    return {name: 1 if mask & bit else 0 for name, bit in LIVE_THROTTLE_CHANNELS.items()}


class SystemSensor:
    """The Pi's own host metrics, gathered from stdlib sources on each read."""

    def __init__(self, *, nvme_path: str = NVME_PATH) -> None:
        """Args:
        nvme_path: Mount point of the NVMe filesystem to report.
        """
        self._nvme_path = nvme_path

    def read(self) -> Reading | None:
        """Return one host-metrics snapshot.

        Never None: an absent source yields None for that metric, keeping the rest
        of the snapshot useful.

        Returns:
            A dict of metric → value (or None per metric).
        """
        root_pct, _ = disk_usage(ROOT_PATH)
        nvme_pct, nvme_free_gb = disk_usage(self._nvme_path)
        throttled = read_throttled()
        return {
            'cpu_temp_c': read_cpu_temp_c(),
            'load_1m': read_load_1m(),
            'mem_used_pct': read_mem_used_pct(),
            'disk_root_pct': root_pct,
            'disk_nvme_pct': nvme_pct,
            'disk_nvme_free_gb': nvme_free_gb,
            'uptime_s': read_uptime_s(),
            'throttled': throttled,
            **split_live_throttle(throttled),
        }


class FakeSystemSensor:
    """Synthesize plausible host metrics for pipeline testing without a Pi."""

    _BASELINES = {
        'cpu_temp_c': 45.0,
        'load_1m': 0.6,
        'mem_used_pct': 42.0,
        'disk_root_pct': 31.0,
        'disk_nvme_pct': 58.0,
        'disk_nvme_free_gb': 380.0,
        'uptime_s': 123456.0,
    }

    def __init__(self) -> None:
        self._state = dict(self._BASELINES)

    def read(self) -> Reading | None:
        """Return one synthetic snapshot, advancing the random walk.

        Returns:
            A dict of metric → value; ``throttled`` is always 0 (healthy).
        """
        snapshot: dict[str, float | int | None] = dict(
            bounded_walk(self._state, self._BASELINES, scale=0.02, round_to=1)
        )
        snapshot['throttled'] = 0
        snapshot.update(split_live_throttle(0))
        return snapshot


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list, or None to read from ``sys.argv``.

    Returns:
        Parsed arguments with ``node``, ``fake``, ``interval``, and ``once``.
    """
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    add_publisher_args(
        parser,
        node_default='pi',
        fake_help='Publish synthetic readings instead of reading host metrics.',
        once_help='Publish a single reading and exit (for testing).',
    )
    parser.add_argument(
        '--interval',
        type=float,
        default=READ_INTERVAL_SECONDS,
        help=f'Seconds between readings (default {READ_INTERVAL_SECONDS}).',
    )
    return parser.parse_args(argv)


def main() -> int:
    """Run the reader loop, publishing readings until interrupted.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    args = parse_args()
    sensor: SimpleSensor = FakeSystemSensor() if args.fake else SystemSensor()
    run_simple_publisher(
        sensor,
        node=args.node,
        sensor_type=SENSOR_TYPE,
        interval=args.interval,
        once=args.once,
        started_msg=f'system reader started (node={args.node}, fake={args.fake})',
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
