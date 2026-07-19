"""Backtest pass prediction against held-out real observations.

For each satellite with enough logged az/el, fit its orbit on the earlier part of
the record and predict az/el at every held-out *later* observation, then report
the angular-error distribution per constellation. This is how the prediction
earns confidence offline: with no TLE or internet reference, the receiver's own
later sightings are the ground truth. A small median error means the orbit fit
plus two-body propagation reproduce where each satellite actually reappeared.

Run on the Pi against real data::

    uv run tools/passes_validate.py
    uv run tools/passes_validate.py --hours 72 --hold-frac 0.3

See .claude/modules/observatory.md.
"""

from __future__ import annotations

import argparse
from collections import defaultdict

import api.db
from api.db import get_connection
from api.observatory import sat_name
from common.cli import run_cli
from common.orbits import azel_at, fit_orbit
from common.satgeo import angular_separation_deg, ecef_to_azel
from tools.backtest_common import (
    load_observation_tracks,
    percentile,
    print_error_table,
    split_holdout,
)


def run(args: argparse.Namespace) -> int:
    """Backtest every eligible satellite and print the error report."""
    conn = get_connection()
    loaded = load_observation_tracks(conn, args.hours)
    if loaded is None:
        print('No GPS fix available to anchor the observer.')
        return 1
    lat, lon, origin, tracks = loaded

    errors: dict[int, list[float]] = defaultdict(list)
    n_validated = 0
    n_skipped = 0
    for (gnssid, svid), samples in sorted(tracks.items()):
        fit_samples, hold = split_holdout(samples, args.hold_frac, args.min_fit)
        if not fit_samples:
            n_skipped += 1
            continue
        orbit = fit_orbit([(s.unix, s.ecef_m) for s in fit_samples], gnssid)
        if orbit is None:
            n_skipped += 1
            continue
        n_validated += 1
        sv_errs = []
        for h in hold:
            paz, pel, _ = azel_at(orbit, h.unix, lat, lon, origin)
            taz, tel, _ = ecef_to_azel(lat, lon, origin, h.ecef_m)
            sv_errs.append(angular_separation_deg(paz, pel, taz, tel))
        errors[gnssid].extend(sv_errs)
        if args.verbose and sv_errs:
            print(
                f'  {sat_name(gnssid, svid):<4} fit {len(fit_samples):>4}  hold {len(hold):>4}'
                f'  median {percentile(sv_errs, 0.5):6.2f}°  p90 {percentile(sv_errs, 0.9):6.2f}°'
            )

    print(f'\nObserver {lat:.4f}, {lon:.4f} · window {args.hours}h · hold {args.hold_frac:.0%}')
    print(
        f'Validated {n_validated} satellites, skipped {n_skipped} (too few points or unfittable)\n'
    )
    if not any(errors.values()):
        print('No satellite had enough observations to backtest. Let the logger run longer.')
        return 1

    print_error_table(errors, unit='°')
    return 0


def parse_args() -> argparse.Namespace:
    """Parse the backtest CLI arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--db', help='SQLite DB path (default: GPS_DB_PATH / ~/gps_history.db)')
    parser.add_argument(
        '--hours', type=float, default=168.0, help='Trailing observation window (default 168 = 7d)'
    )
    parser.add_argument(
        '--hold-frac', type=float, default=0.3, help='Fraction of each track held out (default 0.3)'
    )
    parser.add_argument(
        '--min-fit', type=int, default=20, help='Minimum fit samples per satellite (default 20)'
    )
    parser.add_argument('-v', '--verbose', action='store_true', help='Print a per-satellite line')
    return parser.parse_args()


def main() -> int:
    """Entry point: apply the optional DB override and run the backtest."""
    args = parse_args()
    api.db.apply_path_overrides(args.db)
    return run(args)


if __name__ == '__main__':
    run_cli(main)
