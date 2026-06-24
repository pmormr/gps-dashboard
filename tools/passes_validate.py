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
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import api.db
from api.db import canonical_timestamp, get_connection, now_canonical
from api.observatory import Sample, anchor_observer, reconstruct_tracks, sat_name
from common.cli import run_cli
from common.gpsd import GNSS_NAMES
from common.orbits import azel_at, fit_orbit
from common.satgeo import ecef_to_azel, observer_ecef


def angular_separation_deg(az1: float, el1: float, az2: float, el2: float) -> float:
    """Angle between two az/el pointing directions, in degrees.

    Builds the local East-North-Up unit vector for each direction and returns the
    angle between them — the on-sky miss distance, which folds azimuth and
    elevation error into one honest number (and stays well-behaved near zenith,
    where azimuth alone blows up).

    Args:
        az1: First azimuth, degrees.
        el1: First elevation, degrees.
        az2: Second azimuth, degrees.
        el2: Second elevation, degrees.

    Returns:
        The separation angle in degrees, in ``[0, 180]``.
    """

    def enu(az: float, el: float) -> tuple[float, float, float]:
        a, e = math.radians(az), math.radians(el)
        return (math.cos(e) * math.sin(a), math.cos(e) * math.cos(a), math.sin(e))

    v1, v2 = enu(az1, el1), enu(az2, el2)
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(v1, v2, strict=True))))
    return math.degrees(math.acos(dot))


def _percentile(values: list[float], frac: float) -> float:
    """Linear-index percentile of a non-empty sorted-able list (frac in [0, 1])."""
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(frac * (len(ordered) - 1) + 0.5))]


def _split(
    samples: list[Sample], hold_frac: float, min_fit: int
) -> tuple[list[Sample], list[Sample]]:
    """Earlier-fit / later-holdout split; empty fit when the record is too short."""
    k = int(len(samples) * (1.0 - hold_frac))
    if k < min_fit or len(samples) - k < 1:
        return [], []
    return samples[:k], samples[k:]


def run(args: argparse.Namespace) -> int:
    """Backtest every eligible satellite and print the error report."""
    conn = get_connection()
    obs = anchor_observer(conn, now_canonical())
    if obs is None:
        print('No GPS fix available to anchor the observer.')
        return 1
    lat, lon = obs['lat'], obs['lon']
    origin = observer_ecef(lat, lon, obs['altitude'] or 0.0)

    start_ts = canonical_timestamp((datetime.now(UTC) - timedelta(hours=args.hours)).isoformat())
    rows = conn.execute(
        'SELECT timestamp, gnssid, svid, az, el, snr, used FROM sat_observations '
        'WHERE timestamp BETWEEN ? AND ? AND az IS NOT NULL AND el IS NOT NULL '
        'ORDER BY gnssid, svid, timestamp',
        [start_ts, now_canonical()],
    ).fetchall()
    tracks = reconstruct_tracks(rows, lat, lon, origin)

    errors: dict[int, list[float]] = defaultdict(list)
    n_validated = 0
    n_skipped = 0
    for (gnssid, svid), samples in sorted(tracks.items()):
        fit_samples, hold = _split(samples, args.hold_frac, args.min_fit)
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
                f'  median {_percentile(sv_errs, 0.5):6.2f}°  p90 {_percentile(sv_errs, 0.9):6.2f}°'
            )

    print(f'\nObserver {lat:.4f}, {lon:.4f} · window {args.hours}h · hold {args.hold_frac:.0%}')
    print(
        f'Validated {n_validated} satellites, skipped {n_skipped} (too few points or unfittable)\n'
    )
    if not any(errors.values()):
        print('No satellite had enough observations to backtest. Let the logger run longer.')
        return 1

    print(f'{"System":<10}{"SVs/obs":>10}{"median":>10}{"p90":>10}{"max":>10}')
    all_errs: list[float] = []
    for gnssid in sorted(errors):
        errs = errors[gnssid]
        all_errs.extend(errs)
        name = GNSS_NAMES.get(gnssid, f'gnss{gnssid}')
        print(
            f'{name:<10}{len(errs):>10}{_percentile(errs, 0.5):>9.2f}°'
            f'{_percentile(errs, 0.9):>9.2f}°{max(errs):>9.2f}°'
        )
    print(
        f'{"ALL":<10}{len(all_errs):>10}{_percentile(all_errs, 0.5):>9.2f}°'
        f'{_percentile(all_errs, 0.9):>9.2f}°{max(all_errs):>9.2f}°'
    )
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
    if args.db:
        api.db.DB_PATH = Path(args.db)
    return run(args)


if __name__ == '__main__':
    run_cli(main)
