"""Backtest our derived GNSS orbits against external TLE truth (CelesTrak).

``passes_validate.py`` checks the prediction against the receiver's *own* later
sightings — self-consistency, which can't see a bias shared by reconstruction and
the orbit fit. This tool checks against an **absolute** reference: CelesTrak GNSS
two-line elements propagated with SGP4 (good to ~1 km, far tighter than our
display-grade reconstruction). CelesTrak is what Spacebook/COMSPOC re-serve
upstream for GNSS, so it is the same truth without the extra plumbing.

SGP4 emits TEME (a GMST-rotated inertial frame), which is exactly the frame our
own propagation lives in, so the truth pipeline reuses ``gmst_rad`` /
``eci_to_ecef`` / ``ecef_to_azel`` with no new frame code. TEME→ECEF here ignores
the equation of equinoxes (<~1 arcsec) and polar motion (mas) — both far below
the errors we are measuring.

Satellites are matched **geometrically**, not via a NORAD/PRN table that would
rot: each logged observation is assigned to the nearest-in-sky TLE of its own
constellation, and the modal assignment across a track locks the identity. Three
numbers come out, per constellation:

* **Input geometry** — logged az/el vs the matched TLE's truth az/el at the same
  instant. Validates the receiver's reported sky and our ENU/frame math; no fit.
* **Orbit-model prediction** — our orbit fit on the earlier track, propagated to
  the held-out later times, vs TLE truth. The absolute analog of
  ``passes_validate`` (same early/late split), and the headline.
* **Reconstruction 3D error** — our nominal-radius ECEF vs the TLE truth ECEF,
  in km. Puts a number on the radius approximation the globe admits to.

Needs internet to fetch TLEs (a dev-time step, like the tile/terrain builders);
the fetch is cached to a snapshot so the backtest re-runs offline against a
frozen reference. Run against a snapshot of the Pi DB::

    uv run tools/tle_validate.py --db /mnt/nvme/data/gps_history.db -v
    uv run tools/tle_validate.py --db ./snap.db --no-fetch  # reuse cached TLEs

See .claude/modules/observatory.md.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import requests
from sgp4.api import Satrec, jday

import api.db
from api.db import canonical_timestamp, get_connection, now_canonical
from api.observatory import Sample, anchor_observer, reconstruct_tracks, sat_name
from common.cli import run_cli
from common.gpsd import GNSS_NAMES
from common.orbits import azel_at, fit_orbit
from common.satcat import SatMeta, load_satcat
from common.satgeo import (
    Vec3,
    angular_separation_deg,
    ecef_to_azel,
    eci_to_ecef,
    gmst_rad,
    observer_ecef,
)

CELESTRAK_GP = 'https://celestrak.org/NORAD/elements/gp.php'

# gnssid -> the CelesTrak GROUP that is exactly that constellation. QZSS has no
# standalone group, so it is filtered out of the combined 'gnss' group by name.
_GROUPS: dict[int, str] = {0: 'gps-ops', 6: 'glo-ops', 2: 'galileo', 3: 'beidou', 1: 'sbas'}
_QZSS_GNSSID = 5
_USER_AGENT = 'gps-dashboard orbit backtest (https://github.com/pmormr/gps-dashboard)'

_PRN_RE = re.compile(r'PRN\s*([A-Z]?\d+)', re.IGNORECASE)


class TleSat(NamedTuple):
    """One catalogued satellite ready for SGP4 propagation.

    Attributes:
        name: The CelesTrak name line (carries the PRN for most constellations).
        prn: Parsed PRN label (e.g. ``22``, ``E11``, ``194``) or None.
        satrec: The SGP4 record built from the two element lines.
    """

    name: str
    prn: str | None
    satrec: Satrec


def _parse_tle_text(text: str) -> list[tuple[str, str, str]]:
    """Split CelesTrak 3-line TLE text into ``(name, line1, line2)`` triples."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    triples: list[tuple[str, str, str]] = []
    i = 0
    while i + 2 < len(lines):
        name, l1, l2 = lines[i], lines[i + 1], lines[i + 2]
        if l1.startswith('1 ') and l2.startswith('2 '):
            triples.append((name.strip(), l1, l2))
            i += 3
        else:
            i += 1
    return triples


def _fetch_group(group: str) -> str:
    """Fetch one CelesTrak GROUP as 3-line TLE text (raises on HTTP error)."""
    resp = requests.get(
        CELESTRAK_GP,
        params={'GROUP': group, 'FORMAT': 'tle'},
        headers={'User-Agent': _USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    if 'Invalid query' in resp.text[:64]:
        raise RuntimeError(f'CelesTrak rejected GROUP={group}: {resp.text[:80]!r}')
    return resp.text


def _fetch_all() -> dict[int, list[tuple[str, str, str]]]:
    """Fetch every modelled constellation's TLEs, keyed by gnssid."""
    groups: dict[int, list[tuple[str, str, str]]] = {}
    for gnssid, group in _GROUPS.items():
        groups[gnssid] = _parse_tle_text(_fetch_group(group))
        print(f'  fetched {group:<9} {len(groups[gnssid]):>3} sats')
    gnss = _parse_tle_text(_fetch_group('gnss'))
    groups[_QZSS_GNSSID] = [t for t in gnss if 'QZSS' in t[0].upper()]
    print(f'  fetched qzss      {len(groups[_QZSS_GNSSID]):>3} sats (from gnss group)')
    return groups


def load_tles(cache: Path, max_age_h: float, fetch: bool) -> dict[int, list[TleSat]]:
    """Load TLEs from the cache, fetching from CelesTrak when stale or forced.

    Args:
        cache: Snapshot JSON path (written after a successful fetch).
        max_age_h: Reuse the cache when younger than this; refetch otherwise.
        fetch: When False, never hit the network — fail if the cache is missing.

    Returns:
        ``{gnssid: [TleSat, ...]}`` with SGP4 records built.

    Raises:
        FileNotFoundError: ``fetch`` is False and no cache exists.
    """
    raw: dict[int, list[tuple[str, str, str]]] | None = None
    if cache.exists():
        blob = json.loads(cache.read_text())
        age_h = (time.time() - blob['fetched_unix']) / 3600.0
        if not fetch or age_h <= max_age_h:
            print(f'Using cached TLEs ({age_h:.1f} h old): {cache}')
            raw = {int(k): [tuple(t) for t in v] for k, v in blob['groups'].items()}  # type: ignore[misc]
    if raw is None:
        if not fetch:
            raise FileNotFoundError(f'No TLE cache at {cache} and --no-fetch given')
        print('Fetching TLEs from CelesTrak...')
        raw = _fetch_all()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({'fetched_unix': time.time(), 'groups': {str(k): v for k, v in raw.items()}})
        )
        print(f'Cached TLEs to {cache}')

    sats: dict[int, list[TleSat]] = {}
    for gnssid, triples in raw.items():
        built: list[TleSat] = []
        for name, l1, l2 in triples:
            m = _PRN_RE.search(name)
            built.append(TleSat(name, m.group(1) if m else None, Satrec.twoline2rv(l1, l2)))
        sats[gnssid] = built
    return sats


def _truth_ecef(sat: TleSat, unix_s: float) -> Vec3 | None:
    """SGP4-propagated ECEF position (metres) of a TLE at ``unix_s``, or None."""
    dt = datetime.fromtimestamp(unix_s, UTC)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond * 1e-6)
    err, r, _ = sat.satrec.sgp4(jd, fr)
    if err != 0:
        return None
    teme_m = (r[0] * 1000.0, r[1] * 1000.0, r[2] * 1000.0)
    return eci_to_ecef(teme_m, gmst_rad(unix_s))


def _dist3_km(a: Vec3, b: Vec3) -> float:
    """Euclidean distance between two ECEF points, in km."""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True))) / 1000.0


def _match_identity(
    samples: list[Sample],
    cands: list[TleSat],
    lat: float,
    lon: float,
    origin: Vec3,
    gate_deg: float,
) -> TleSat | None:
    """Lock a track's identity to the modal nearest-in-sky candidate TLE.

    Each sample votes for the catalogued satellite whose SGP4 az/el is closest to
    the logged az/el, provided the miss is within ``gate_deg``. The winner of the
    vote is the physical satellite this ``(gnssid, svid)`` track corresponds to.
    """
    votes: Counter[int] = Counter()
    for s in samples:
        oaz, oel, _ = ecef_to_azel(lat, lon, origin, s.ecef_m)
        best_i, best_d = -1, gate_deg
        for i, c in enumerate(cands):
            te = _truth_ecef(c, s.unix)
            if te is None:
                continue
            taz, tel, _ = ecef_to_azel(lat, lon, origin, te)
            d = angular_separation_deg(oaz, oel, taz, tel)
            if d <= best_d:
                best_d, best_i = d, i
        if best_i >= 0:
            votes[best_i] += 1
    if not votes:
        return None
    return cands[votes.most_common(1)[0][0]]


def _percentile(values: list[float], frac: float) -> float:
    """Linear-index percentile of a non-empty list (frac in [0, 1])."""
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


def _table(title: str, errors: dict[int, list[float]], unit: str) -> None:
    """Print a per-constellation median/p90/max error table with an ALL row."""
    print(f'\n{title}')
    print(f'{"System":<10}{"SVs/obs":>10}{"median":>10}{"p90":>10}{"max":>10}')
    allv: list[float] = []
    for gnssid in sorted(errors):
        errs = errors[gnssid]
        allv.extend(errs)
        name = GNSS_NAMES.get(gnssid, f'gnss{gnssid}')
        print(
            f'{name:<10}{len(errs):>10}{_percentile(errs, 0.5):>9.2f}{unit}'
            f'{_percentile(errs, 0.9):>9.2f}{unit}{max(errs):>9.2f}{unit}'
        )
    if allv:
        print(
            f'{"ALL":<10}{len(allv):>10}{_percentile(allv, 0.5):>9.2f}{unit}'
            f'{_percentile(allv, 0.9):>9.2f}{unit}{max(allv):>9.2f}{unit}'
        )


def _load_meta(cache: Path, fetch: bool) -> dict[int, SatMeta]:
    """Load the SATCAT cache, or an empty map if it is unavailable."""
    try:
        return load_satcat(cache, fetch=fetch)
    except (FileNotFoundError, OSError):
        return {}


def run(args: argparse.Namespace) -> int:
    """Backtest every matched satellite against TLE truth and print the report."""
    sats = load_tles(Path(args.tle_cache), args.max_age, not args.no_fetch)
    meta = _load_meta(Path(args.satcat_cache), not args.no_fetch)

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

    geom: dict[int, list[float]] = defaultdict(list)
    pos3d: dict[int, list[float]] = defaultdict(list)
    model: dict[int, list[float]] = defaultdict(list)
    n_matched = n_unmatched = 0
    for (gnssid, svid), samples in sorted(tracks.items()):
        cands = sats.get(gnssid)
        if not cands:
            continue
        ident = _match_identity(samples, cands, lat, lon, origin, args.gate)
        if ident is None:
            n_unmatched += 1
            continue
        n_matched += 1

        sv_geom: list[float] = []
        for s in samples:
            te = _truth_ecef(ident, s.unix)
            if te is None:
                continue
            oaz, oel, _ = ecef_to_azel(lat, lon, origin, s.ecef_m)
            taz, tel, _ = ecef_to_azel(lat, lon, origin, te)
            sv_geom.append(angular_separation_deg(oaz, oel, taz, tel))
            pos3d[gnssid].append(_dist3_km(s.ecef_m, te))
        geom[gnssid].extend(sv_geom)

        fit_samples, hold = _split(samples, args.hold_frac, args.min_fit)
        sv_model: list[float] = []
        if fit_samples:
            orbit = fit_orbit([(s.unix, s.ecef_m) for s in fit_samples], gnssid)
            if orbit is not None:
                for h in hold:
                    te = _truth_ecef(ident, h.unix)
                    if te is None:
                        continue
                    paz, pel, _ = azel_at(orbit, h.unix, lat, lon, origin)
                    taz, tel, _ = ecef_to_azel(lat, lon, origin, te)
                    sv_model.append(angular_separation_deg(paz, pel, taz, tel))
                model[gnssid].extend(sv_model)

        if args.verbose:
            gm = _percentile(sv_geom, 0.5) if sv_geom else float('nan')
            md = _percentile(sv_model, 0.5) if sv_model else float('nan')
            m = meta.get(ident.satrec.satnum)
            tag = f'{m.name} [{m.owner} {m.launch_date[:4]}]' if m else ident.name
            print(
                f'  {sat_name(gnssid, svid):<4} obs {len(sv_geom):>4}'
                f'  geom {gm:6.2f}°  model {md:6.2f}° ({len(sv_model):>3} held)  {tag}'
            )

    print(
        f'\nObserver {lat:.4f}, {lon:.4f} · window {args.hours:g}h · '
        f'matched {n_matched} SVs, unmatched {n_unmatched} (no TLE within {args.gate:g}°)'
    )
    if not any(geom.values()):
        print('No satellite matched a TLE. Check the window overlaps fresh observations.')
        return 1

    _table('Input geometry — logged az/el vs TLE truth (no fit):', geom, '°')
    _table('Orbit-model prediction — our fit at held-out times vs TLE truth:', model, '°')
    _table('Reconstruction 3D error — nominal-radius ECEF vs TLE truth:', pos3d, 'km')
    return 0


def parse_args() -> argparse.Namespace:
    """Parse the TLE-backtest CLI arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('--db', help='SQLite DB path (default: GPS_DB_PATH / ~/gps_history.db)')
    parser.add_argument(
        '--hours', type=float, default=36.0, help='Trailing observation window (default 36)'
    )
    parser.add_argument(
        '--tle-cache',
        default=str(Path.home() / '.cache' / 'gps-dashboard' / 'gnss-tle.json'),
        help='TLE snapshot path (fetched/reused here)',
    )
    parser.add_argument(
        '--max-age', type=float, default=24.0, help='Reuse cached TLEs younger than N hours'
    )
    parser.add_argument(
        '--satcat-cache',
        default=str(Path.home() / '.cache' / 'gps-dashboard' / 'gnss-satcat.json'),
        help='SATCAT metadata snapshot path (for the -v identity labels)',
    )
    parser.add_argument(
        '--no-fetch', action='store_true', help='Never hit the network; require the cache'
    )
    parser.add_argument(
        '--gate', type=float, default=8.0, help='Max az/el miss to accept a TLE match (deg)'
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
