"""Fetch and cache CelesTrak SATCAT metadata for the GNSS constellations.

A dev-time step (needs internet, like the tile/terrain builders): pulls the
satellite catalogue — owner-country, launch, orbit geometry, size, object type,
status — and caches it to JSON so it is available offline thereafter. The TLE
backtest (``tools/tle_validate.py``) joins it to each fitted orbit by NORAD id.

    uv run tools/fetch_satcat.py
    uv run tools/fetch_satcat.py --force          # ignore cache age, refetch
    uv run tools/fetch_satcat.py --cache ./x.json

See .claude/modules/observatory.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common.cli import run_cli
from common.satcat import DEFAULT_CACHE_PATH, SatMeta, load_satcat


def _constellation(name: str) -> str:
    """Best-effort constellation label from a catalogue name (display only)."""
    u = name.upper()
    if u.startswith(('GPS', 'NAVSTAR')):
        return 'GPS'
    if 'GLONASS' in u or 'COSMOS' in u:
        return 'GLONASS'
    if 'GSAT0' in u or 'GALILEO' in u:
        return 'Galileo'
    if 'BEIDOU' in u or 'BDS' in u:
        return 'BeiDou'
    if 'QZS' in u:
        return 'QZSS'
    if 'IRNSS' in u or 'NAVIC' in u or u.startswith('NVS'):
        return 'NavIC'
    return 'Other/SBAS'


def run(args: argparse.Namespace) -> int:
    """Fetch (or reuse) the SATCAT cache and print a per-constellation summary."""
    cache = Path(args.cache)
    meta = load_satcat(cache, max_age_h=0.0 if args.force else args.max_age, fetch=True)
    print(f'{len(meta)} satellites cached to {cache}\n')

    by_con: dict[str, list[SatMeta]] = {}
    for m in meta.values():
        by_con.setdefault(_constellation(m.name), []).append(m)

    print(f'{"Constellation":<14}{"total":>7}{"operational":>13}')
    for con in sorted(by_con):
        rows = by_con[con]
        op = sum(1 for m in rows if m.ops_status == '+')
        print(f'{con:<14}{len(rows):>7}{op:>13}')

    if args.verbose:
        print('\nSample records:')
        for m in sorted(meta.values(), key=lambda m: m.name)[:8]:
            prn = f'PRN {m.prn}' if m.prn else '—'
            print(
                f'  {m.norad:>5}  {m.name:<26} {prn:<8} {m.owner:<4} '
                f'launched {m.launch_date or "?":<10} incl {m.inclination_deg or 0:.1f}°'
            )
    return 0


def parse_args() -> argparse.Namespace:
    """Parse the SATCAT-fetch CLI arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--cache', default=str(DEFAULT_CACHE_PATH), help='SATCAT snapshot path')
    parser.add_argument(
        '--max-age',
        type=float,
        default=168.0,
        help='Reuse cache younger than N hours (default 168)',
    )
    parser.add_argument('--force', action='store_true', help='Ignore cache age and refetch')
    parser.add_argument('-v', '--verbose', action='store_true', help='Print sample records')
    return parser.parse_args()


def main() -> int:
    """Entry point: fetch/refresh the SATCAT cache and summarise it."""
    return run(parse_args())


if __name__ == '__main__':
    run_cli(main)
