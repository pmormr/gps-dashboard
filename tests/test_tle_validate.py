"""Tests for the external-TLE backtest tool.

No network: a few real GPS TLEs are vendored as a fixture, observations are
synthesized from them with the tool's own truth pipeline, and the tool's matcher
and the orbit fit are checked to recover them. The round-trip proves the SGP4
truth path, the geometric identity match, and that our circular two-body fit
tracks real SGP4 motion to a fraction of a degree near epoch.
"""

from __future__ import annotations

import statistics

from sgp4.api import Satrec

from api.observatory import Sample
from common.orbits import azel_at, fit_orbit
from common.satgeo import (
    Vec3,
    angular_separation_deg,
    ecef_to_azel,
    observer_ecef,
    reconstruct,
)
from tools.tle_validate import TleSat, _match_identity, _parse_tle_text, _truth_ecef

# Three real GPS TLEs (CelesTrak, epoch day-of-year 26176 ≈ 2026-06-25).
_TLES = [
    (
        'GPS BIIR-5  (PRN 22)',
        '1 26407U 00040A   26176.27973259  .00000074  00000+0  00000+0 0  9998',
        '2 26407  54.8505 214.8407 0120931 302.5387  64.3497  2.00557831190114',
    ),
    (
        'GPS BIIR-8  (PRN 16)',
        '1 27663U 03005A   26176.08070955  .00000073  00000+0  00000+0 0  9996',
        '2 27663  54.8812 214.6551 0148002  53.0935 306.1631  2.00560235171460',
    ),
    (
        'GPS BIIR-11 (PRN 19)',
        '1 28190U 04009A   26175.31167499 -.00000071  00000+0  00000+0 0  9991',
        '2 28190  54.8236 275.3992 0116404 173.4104 143.5535  2.00554582163088',
    ),
]

_GPS_GNSSID = 0
_LAT, _LON, _ALT = 40.0, -75.0, 100.0


def _sat(triple: tuple[str, str, str]) -> TleSat:
    """Build a TleSat (identity unused by the geometry tests) from a TLE triple."""
    return TleSat(triple[0], None, Satrec.twoline2rv(triple[1], triple[2]))


def _epoch_unix(sat: TleSat) -> float:
    """Unix seconds of a satrec's element-set epoch."""
    jd = sat.satrec.jdsatepoch + sat.satrec.jdsatepochF
    return (jd - 2440587.5) * 86400.0


def _synth_track(sat: TleSat, origin: Vec3, n: int = 180, step_s: float = 60.0) -> list[Sample]:
    """Synthesize an above-horizon track from a TLE via the tool's truth path.

    Each kept sample carries the nominal-radius reconstruction of the truth az/el,
    exactly as the tool builds tracks from logged observations.
    """
    t0 = _epoch_unix(sat)
    out: list[Sample] = []
    for k in range(n):
        t = t0 + k * step_s
        truth = _truth_ecef(sat, t)
        assert truth is not None
        az, el, _ = ecef_to_azel(_LAT, _LON, origin, truth)
        if el < 10.0:
            continue
        ecef = reconstruct(_LAT, _LON, _ALT, az, el, _GPS_GNSSID)
        assert ecef is not None
        out.append(Sample(str(t), t, ecef, 40.0, True))
    return out


def test_parse_tle_text_skips_malformed() -> None:
    """A stray leading line is skipped; well-formed 3-line sets parse cleanly."""
    text = 'JUNK HEADER\n' + '\n'.join('\n'.join(t) for t in _TLES) + '\n'
    triples = _parse_tle_text(text)
    assert len(triples) == 3
    assert triples[0][0] == 'GPS BIIR-5  (PRN 22)'
    assert all(l1.startswith('1 ') and l2.startswith('2 ') for _, l1, l2 in triples)


def test_truth_ecef_is_gps_radius() -> None:
    """SGP4 places a GPS satellite at the constellation's ~26,560 km radius."""
    sat = _sat(_TLES[0])
    pos = _truth_ecef(sat, _epoch_unix(sat))
    assert pos is not None
    radius_km = (pos[0] ** 2 + pos[1] ** 2 + pos[2] ** 2) ** 0.5 / 1000.0
    assert 25_500.0 < radius_km < 27_600.0


def test_match_identity_locks_correct_satellite() -> None:
    """A track synthesized from one TLE is matched to that TLE among candidates."""
    cands = [_sat(t) for t in _TLES]
    origin = observer_ecef(_LAT, _LON, _ALT)
    target = cands[1]
    samples = _synth_track(target, origin)
    assert len(samples) >= 20
    matched = _match_identity(samples, cands, _LAT, _LON, origin, gate_deg=8.0)
    assert matched is target


def test_pipeline_recovers_orbit_near_zero_error() -> None:
    """Reconstruct + fit + propagate reproduces SGP4 az/el to sub-degree."""
    sat = _sat(_TLES[0])
    origin = observer_ecef(_LAT, _LON, _ALT)
    samples = _synth_track(sat, origin)
    assert len(samples) >= 40

    # Geometry: the nominal-radius reconstruction preserves the truth az/el exactly.
    for s in samples:
        truth = _truth_ecef(sat, s.unix)
        assert truth is not None
        oaz, oel, _ = ecef_to_azel(_LAT, _LON, origin, s.ecef_m)
        taz, tel, _ = ecef_to_azel(_LAT, _LON, origin, truth)
        assert angular_separation_deg(oaz, oel, taz, tel) < 1e-4

    # Model: fit the earlier arc, predict the held-out tail vs SGP4 truth.
    cut = int(len(samples) * 0.7)
    orbit = fit_orbit([(s.unix, s.ecef_m) for s in samples[:cut]], _GPS_GNSSID)
    assert orbit is not None
    errs = []
    for s in samples[cut:]:
        truth = _truth_ecef(sat, s.unix)
        assert truth is not None
        paz, pel, _ = azel_at(orbit, s.unix, _LAT, _LON, origin)
        taz, tel, _ = ecef_to_azel(_LAT, _LON, origin, truth)
        errs.append(angular_separation_deg(paz, pel, taz, tel))
    assert statistics.median(errs) < 1.0
