"""OpenWrt router probe (Phase 0 of the network-telemetry plan).

Read-only source survey for the OpenWrt reader stream: SSH to a target router,
run one fixed remote snippet dumping every candidate telemetry source, and print
the results as sections with an availability summary. The output is what locks
the ``openwrt_readings`` column set — in particular whether the Morse HaLow
driver exposes per-station RSSI/MCS, and whether the mt7621 has a thermal zone.

Auth is key-based only (``BatchMode=yes``): a missing/unauthorized key fails
fast rather than hanging on a password prompt. Authorize the probing host's key
on the target first (``ssh-copy-id root@192.168.42.1``).

Run on the Pi (the production vantage point)::

    uv run tools/openwrt_probe.py                      # van-edge, all sections
    uv run tools/openwrt_probe.py --host 192.168.42.1 --user root
    uv run tools/openwrt_probe.py --section station_dump --section iwinfo
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from common.cli import run_cli
from sensors.openwrt_reader import build_remote_script, parse_marked_sections

#: Candidate telemetry sources, each a named BusyBox-sh snippet run on the target.
#: Order is the print order. Every snippet must terminate on its own (no
#: followers/daemons) — the whole batch runs in one SSH round-trip.
SECTIONS: dict[str, str] = {
    # Identity/context (not columns; confirms what box we probed).
    'board': 'cat /etc/openwrt_release; ubus call system board',
    # Load/memory/uptime — /proc vs the pre-parsed ubus JSON, to pick a parse source.
    'loadavg': 'cat /proc/loadavg',
    'uptime': 'cat /proc/uptime',
    'meminfo': 'grep -E "^(MemTotal|MemFree|MemAvailable|Buffers|Cached):" /proc/meminfo',
    'ubus_system_info': 'ubus call system info',
    # SoC temperature — mt7621 may expose neither; absence is a finding.
    'thermal': (
        'for z in /sys/class/thermal/thermal_zone*; do '
        '[ -e "$z" ] || continue; echo "$z $(cat "$z/type"): $(cat "$z/temp")"; done'
    ),
    'hwmon': (
        'for h in /sys/class/hwmon/hwmon*; do [ -e "$h" ] || continue; '
        'echo "$h $(cat "$h/name" 2>/dev/null)"; '
        'for t in "$h"/temp*_input; do [ -e "$t" ] || continue; '
        'echo "  $t: $(cat "$t")"; done; done'
    ),
    # Interface byte/packet counters — the throughput-delta source.
    'net_dev': 'cat /proc/net/dev',
    # WAN state: logical interface (device name, uptime, addresses) + default route.
    'wan_status': 'ubus call network.interface.wan status',
    'routes': 'ip route show',
    # Wireless: interface list, then per-interface station dumps via both APIs.
    # iw is the generic nl80211 path; iwinfo is OpenWrt's abstraction — the Morse
    # driver may serve either, neither, or a vendor CLI only.
    'iw_dev': 'iw dev',
    'station_dump': (
        'for d in $(iw dev 2>/dev/null | awk \'$1=="Interface"{print $2}\'); do '
        'echo "-- $d"; iw dev "$d" station dump; done'
    ),
    'iwinfo': (
        'for d in $(iw dev 2>/dev/null | awk \'$1=="Interface"{print $2}\'); do '
        'echo "-- $d info"; iwinfo "$d" info; '
        'echo "-- $d assoclist"; iwinfo "$d" assoclist; done'
    ),
    'morse_cli': (
        'ls /usr/bin /usr/sbin 2>/dev/null | grep -i morse; '
        'command -v morse_cli && morse_cli --help 2>&1 | head -40'
    ),
    # LAN occupancy + NAT table pressure.
    'dhcp_leases': 'cat /tmp/dhcp.leases',
    'conntrack': (
        'echo "count=$(cat /proc/sys/net/netfilter/nf_conntrack_count)"; '
        'echo "max=$(cat /proc/sys/net/netfilter/nf_conntrack_max)"'
    ),
    # WAN reachability + latency (the up/down + RTT columns). ~3 s of the run.
    'wan_ping': 'ping -c 3 -W 2 -q 8.8.8.8',
}


def run_remote(host: str, user: str, script: str, timeout: float) -> str:
    """Run the assembled script on the target over one SSH round-trip.

    Args:
        host: Target address.
        user: SSH user (root on OpenWrt).
        script: The marker-wrapped script from :func:`build_remote_script`.
        timeout: Overall subprocess timeout in seconds.

    Returns:
        The remote combined output.

    Raises:
        SystemExit: On SSH connect/auth failure or timeout, with a hint.
    """
    cmd = [
        'ssh',
        '-o',
        'BatchMode=yes',
        '-o',
        'ConnectTimeout=5',
        '-o',
        'StrictHostKeyChecking=accept-new',
        f'{user}@{host}',
        'sh -s',
    ]
    try:
        proc = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        sys.exit(f'ssh to {user}@{host} timed out after {timeout:.0f}s')
    if proc.returncode == 255:
        sys.exit(
            f'ssh to {user}@{host} failed: {proc.stderr.strip()}\n'
            f"(BatchMode is on — authorize this host's key first: ssh-copy-id {user}@{host})"
        )
    return proc.stdout


def print_report(names: list[str], results: dict[str, tuple[int, str]]) -> None:
    """Print each section's body and a final availability summary table.

    Args:
        names: Requested section names, in print order.
        results: Parsed per-section results from :func:`parse_sections`.
    """
    for name in names:
        print(f'\n=== {name} ===')
        if name not in results:
            print('  (no output — connection cut short?)')
            continue
        rc, body = results[name]
        print(body if body else '  (empty)')
        if rc != 0:
            print(f'  [exit {rc}]')

    print('\n=== availability summary ===')
    for name in names:
        if name not in results:
            verdict = 'MISSING'
        else:
            rc, body = results[name]
            verdict = 'ok' if rc == 0 and body else ('empty' if rc == 0 else f'FAILED rc={rc}')
        print(f'  {name:18s} {verdict}')


def main() -> int:
    """Parse args, run the remote survey, and print the report."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--host', default='192.168.42.1', help='target address (default van-edge)')
    parser.add_argument('--user', default='root', help='SSH user (default root)')
    parser.add_argument(
        '--timeout', type=float, default=60.0, help='overall SSH timeout seconds (default 60)'
    )
    parser.add_argument(
        '--section',
        action='append',
        choices=sorted(SECTIONS),
        help='probe only this section (repeatable; default all)',
    )
    args = parser.parse_args()

    names = args.section if args.section else list(SECTIONS)
    sections = {name: SECTIONS[name] for name in names}

    print(f'probing {args.user}@{args.host} ({len(sections)} sections, one SSH round-trip) ...')
    output = run_remote(args.host, args.user, build_remote_script(sections), args.timeout)
    results = parse_marked_sections(output)
    print_report(names, results)
    return 0


if __name__ == '__main__':
    run_cli(main)
