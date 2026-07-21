#!/usr/bin/env python3
"""Radio capture-path noise-floor probe — an objective number per hardware config.

Captures N seconds off the Digirig codec and reports the broadband RMS/peak floor
in dBFS, the per-block spread, and a cheap low/high energy split, so the
ground-isolator experiment (the 2f tail in ``plans/radio-platform-plan.md``) gets
a repeatable, comparable measurement per config instead of an ear judgement.

Measure a *muted* channel (squelch closed) to read the capture path's own floor —
the number the VOX thresholds in ``radio/recorder.py`` are set against. An
open-squelch capture on a dead channel reads rig/RF static instead; the tool
prints the rig's DCD so you can tell which you got.

Interpreting the split: a ground-loop hum injected through the audio or USB ground
is low-frequency and pushes ``low-frac`` up; broadband codec self-noise (which the
isolators cannot fix) spreads evenly. It is a coarse hint, not a spectrum — the
capture WAV is kept (``--wav``) so an offline FFT can settle an ambiguous case.

Runs on the Pi. ``hw:CARD=Digirig`` is single-open, so stop ``radio-recorder`` and
``radio-stream`` first, or pass ``--device digirig_shared`` to sample through the
dsnoop PCM alongside them. Pins the same codec mixer state the recorder pins (AGC
off + fixed gain) so gain is identical across configs; ``--no-pin`` leaves it
untouched. ``--analyze PATH`` re-reads a kept WAV with no capture.
"""

from __future__ import annotations

import argparse
import array
import json
import math
import statistics
import subprocess
import sys
import tempfile
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

from api.rigctld import Rigctld, RigctldError
from common.cli import run_cli
from common.proc import run
from radio.vox import amplitude_dbfs, rms_dbfs

SAMPLE_RATE = 48000
BLOCK_FRAMES = 4800  # 0.1 s — matches the recorder's block cadence
DEFAULT_DEVICE = 'hw:CARD=Digirig'
DEFAULT_MIXER_CARD = 'Digirig'
DEFAULT_MIXER_SETS = 'Auto Gain Control:off,Mic:10'
#: Single-pole split point: hum/rumble below, hiss/whine above.
LOWPASS_HZ = 300.0


@dataclass
class FloorStats:
    """Aggregate floor statistics over one capture.

    Attributes:
        seconds: Analyzed capture length.
        rms_dbfs: Broadband RMS level over the whole capture.
        peak_dbfs: Largest single-sample amplitude.
        block_min_dbfs: Quietest 100 ms block — the truest floor (transient-free).
        block_median_dbfs: Typical 100 ms block level.
        block_max_dbfs: Loudest 100 ms block.
        low_band_dbfs: RMS of the sub-``LOWPASS_HZ`` component (hum/rumble).
        high_band_dbfs: RMS of the above-``LOWPASS_HZ`` component (hiss/whine).
        low_fraction: Low-band share of total energy (0..1); high ⇒ hum-dominated.
    """

    seconds: float
    rms_dbfs: float
    peak_dbfs: float
    block_min_dbfs: float
    block_median_dbfs: float
    block_max_dbfs: float
    low_band_dbfs: float
    high_band_dbfs: float
    low_fraction: float


def lowpass_alpha(fc: float, rate: int) -> float:
    """Single-pole low-pass smoothing coefficient for cutoff ``fc`` at ``rate``.

    Args:
        fc: Cutoff frequency in Hz.
        rate: Sample rate in Hz.

    Returns:
        The RC smoothing factor ``dt / (RC + dt)`` in 0..1.
    """
    dt = 1.0 / rate
    rc = 1.0 / (2.0 * math.pi * fc)
    return dt / (rc + dt)


def analyze(samples: array.array, rate: int = SAMPLE_RATE) -> FloorStats:
    """Compute floor stats: broadband RMS/peak, per-block spread, hum/hiss split.

    Args:
        samples: Signed-16-bit mono samples.
        rate: Sample rate in Hz.

    Returns:
        The aggregated :class:`FloorStats`.

    Raises:
        RuntimeError: If ``samples`` is empty.
    """
    n = len(samples)
    if n == 0:
        raise RuntimeError('empty capture')

    # Pass 1: per-block RMS distribution + global sum-of-squares + peak.
    block_rms: list[float] = []
    total_sq = 0
    peak = 0
    for i in range(0, n - BLOCK_FRAMES + 1, BLOCK_FRAMES):
        block = samples[i : i + BLOCK_FRAMES]
        bsq = sum(s * s for s in block)
        total_sq += bsq
        peak = max(peak, max(block), -min(block))
        block_rms.append(rms_dbfs(bsq, len(block)))
    counted = (n // BLOCK_FRAMES) * BLOCK_FRAMES or n

    # Pass 2: single-pole low/high split (hum/rumble vs hiss/whine).
    alpha = lowpass_alpha(LOWPASS_HZ, rate)
    y = 0.0
    lo_sq = 0.0
    hi_sq = 0.0
    for s in samples:
        y += alpha * (s - y)
        lo_sq += y * y
        hi_sq += (s - y) ** 2

    if not block_rms:  # capture shorter than one block
        block_rms = [rms_dbfs(total_sq, counted)]
    total_energy = lo_sq + hi_sq
    return FloorStats(
        seconds=n / rate,
        rms_dbfs=rms_dbfs(total_sq, counted),
        peak_dbfs=amplitude_dbfs(float(peak)),
        block_min_dbfs=min(block_rms),
        block_median_dbfs=statistics.median(block_rms),
        block_max_dbfs=max(block_rms),
        low_band_dbfs=rms_dbfs(lo_sq, n),
        high_band_dbfs=rms_dbfs(hi_sq, n),
        low_fraction=(lo_sq / total_energy) if total_energy > 0 else 0.0,
    )


def pin_mixer(card: str, sets: str) -> None:
    """Pin the codec capture chain (AGC off + fixed gain), as the recorder does.

    Args:
        card: ALSA card name (``amixer -c``).
        sets: Comma-separated ``control:value`` pairs.
    """
    for item in sets.split(','):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.rpartition(':')
        code, out, err = run(['amixer', '-c', card, 'sset', name, value])
        if code != 0:
            detail = err.strip() or out.strip()
            print(f'  mixer pin failed ({name}={value}): {detail}', file=sys.stderr)


def capture_wav(device: str, seconds: float, path: Path) -> None:
    """Record ``seconds`` of mono S16_LE 48 kHz off ``device`` into a WAV.

    Args:
        device: ALSA capture device.
        seconds: Duration to record (rounded to whole seconds for ``arecord``).
        path: Output WAV path.

    Raises:
        RuntimeError: If ``arecord`` exits non-zero (device busy, ALSA error).
    """
    cmd = [
        'arecord',
        '-D',
        device,
        '-f',
        'S16_LE',
        '-r',
        str(SAMPLE_RATE),
        '-c',
        '1',
        '-d',
        str(max(1, round(seconds))),
        '-t',
        'wav',
        '-q',
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or 'no output'
        raise RuntimeError(f'arecord failed (rc={proc.returncode}): {detail}')


def read_samples(path: Path) -> array.array:
    """Load a mono 16-bit WAV as signed shorts.

    Args:
        path: WAV file to read.

    Returns:
        The samples as an ``array('h')``.

    Raises:
        RuntimeError: If the WAV is not mono 16-bit.
    """
    with wave.open(str(path), 'rb') as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise RuntimeError(
                f'expected mono 16-bit, got {w.getnchannels()}ch {w.getsampwidth() * 8}-bit'
            )
        frames = w.readframes(w.getnframes())
    samples = array.array('h')
    samples.frombytes(frames[: len(frames) - (len(frames) % 2)])
    return samples


def rig_snapshot() -> dict[str, str]:
    """Best-effort rig state (freq/mode/squelch/strength) for the report header.

    Returns:
        A string→string dict; ``{'error': ...}`` when rigctld is unreachable.
    """
    info: dict[str, str] = {}
    try:
        with Rigctld(timeout=1.0) as rig:
            info['freq_hz'] = str(rig.get_freq())
            info['mode'] = rig.get_mode()[0]
            sql = rig.get_level('SQL')
            if sql is not None:
                info['sql'] = f'{sql:.3f}'
            strength = rig.get_level('STRENGTH')
            if strength is not None:
                info['strength_db'] = f'{strength:.0f}'
            dcd = rig.get_dcd()
            if dcd is not None:
                info['squelch'] = 'OPEN (signal/static present)' if dcd else 'closed (muted)'
    except RigctldError as exc:
        info['error'] = str(exc)
    return info


def pin_af(value: float) -> None:
    """Assert the rig AF (audio output) level — the record-level calibration.

    The recorder normally re-pins AF every heartbeat because a rig power cycle
    reverts CI-V-set levels; with the recorder stopped for a measurement run,
    the probe pins it so static levels stay comparable across configs.

    Args:
        value: Normalized AF level (0..1), e.g. ``0.25``.
    """
    try:
        with Rigctld(timeout=1.5) as rig:
            rig.set_level('AF', value)
    except RigctldError as exc:
        print(f'  could not pin AF: {exc}', file=sys.stderr)


def open_squelch() -> float | None:
    """Fully open the rig squelch (SQL 0) for a static reference; return the prior SQL.

    Returns:
        The SQL level in effect before opening, or ``None`` if rigctld was
        unreachable or does not report SQL (so there is nothing to restore).
    """
    try:
        with Rigctld(timeout=1.5) as rig:
            prior = rig.get_level('SQL')
            rig.set_level('SQL', 0.0)
    except RigctldError as exc:
        print(f'  could not open squelch: {exc}', file=sys.stderr)
        return None
    return prior


def restore_squelch(value: float) -> None:
    """Restore the rig squelch to ``value`` after a static-reference capture."""
    try:
        with Rigctld(timeout=1.5) as rig:
            rig.set_level('SQL', value)
        print(f'  restored SQL {value:.3f}', file=sys.stderr)
    except RigctldError as exc:
        print(f'  WARNING: failed to restore SQL {value:.3f}: {exc}', file=sys.stderr)


def _fmt_rig(rig: dict[str, str]) -> str:
    """Render the rig snapshot as a one-line header."""
    if 'error' in rig:
        return f'unavailable ({rig["error"]})'
    freq = rig.get('freq_hz')
    parts = [f'{int(freq) / 1e6:.3f} MHz' if freq else 'freq n/a', rig.get('mode', '')]
    if 'sql' in rig:
        parts.append(f'SQL {rig["sql"]}')
    if 'squelch' in rig:
        parts.append(rig['squelch'])
    if 'strength_db' in rig:
        parts.append(f'S {rig["strength_db"]} dB')
    return '  '.join(p for p in parts if p)


def _print_report(
    label: str | None, rig: dict[str, str], stats: FloorStats, wav_path: Path | None
) -> None:
    """Print the human-readable floor report."""
    title = f'radio floor: {label}' if label else 'radio floor'
    print(f'\n=== {title} ===')
    print(f'rig      {_fmt_rig(rig)}')
    print(f'capture  {stats.seconds:.1f} s')
    print()
    print(f'floor    RMS {stats.rms_dbfs:6.1f} dBFS     peak {stats.peak_dbfs:6.1f} dBFS')
    print(
        f'blocks   min {stats.block_min_dbfs:.1f}  median {stats.block_median_dbfs:.1f}  '
        f'max {stats.block_max_dbfs:.1f} dBFS'
    )
    print(
        f'split    low(<{int(LOWPASS_HZ)}Hz) {stats.low_band_dbfs:.1f}  '
        f'high {stats.high_band_dbfs:.1f} dBFS  low-frac {stats.low_fraction:.2f}'
    )
    if rig.get('squelch', '').startswith('OPEN'):
        print('\nNOTE: squelch is OPEN — this reads RF/static, not the muted path floor.')
    if wav_path is not None:
        print(f'\nwav      {wav_path}')


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    p = argparse.ArgumentParser(description=(__doc__ or '').strip().splitlines()[0])
    p.add_argument('-t', '--seconds', type=float, default=30.0, help='capture length (default 30)')
    p.add_argument('-D', '--device', default=DEFAULT_DEVICE, help='ALSA capture device')
    p.add_argument('--mixer-card', default=DEFAULT_MIXER_CARD)
    p.add_argument('--mixer-sets', default=DEFAULT_MIXER_SETS, help='control:value pairs to pin')
    p.add_argument('--no-pin', action='store_true', help='leave the codec mixer untouched')
    p.add_argument(
        '--pin-af',
        type=float,
        default=None,
        help='assert the rig AF level before capture (record-level calibration, e.g. 0.25)',
    )
    p.add_argument(
        '--open-squelch',
        action='store_true',
        help='open the rig squelch (SQL 0) for the capture then restore it — measures '
        'receiver static (the signal-path reference), not the muted floor',
    )
    p.add_argument('--wav', type=Path, default=None, help='keep the capture WAV at this path')
    p.add_argument('--analyze', type=Path, default=None, help='analyze an existing WAV, no capture')
    p.add_argument('--label', default=None, help='label echoed in the report')
    p.add_argument('--json', action='store_true', help='emit JSON instead of a text report')
    return p


def main() -> int:
    """Capture (or re-analyze) and print the floor report."""
    args = build_parser().parse_args()

    if args.analyze is not None:
        stats = analyze(read_samples(args.analyze))
        rig: dict[str, str] = {}
        wav_path: Path | None = args.analyze
    else:
        if not args.no_pin:
            pin_mixer(args.mixer_card, args.mixer_sets)
        if args.pin_af is not None:
            pin_af(args.pin_af)
        saved_sql = open_squelch() if args.open_squelch else None
        try:
            rig = rig_snapshot()
            keep = args.wav is not None
            if keep:
                wav_path = args.wav
            else:
                tmp = tempfile.NamedTemporaryFile(
                    prefix='radio-floor-', suffix='.wav', delete=False
                )
                tmp.close()
                wav_path = Path(tmp.name)
            print(f'capturing {args.seconds:g}s from {args.device} ...', file=sys.stderr)
            capture_wav(args.device, args.seconds, wav_path)
            stats = analyze(read_samples(wav_path))
            if not keep:
                wav_path.unlink(missing_ok=True)
                wav_path = None
        finally:
            if saved_sql is not None:
                restore_squelch(saved_sql)

    if args.json:
        print(
            json.dumps(
                {
                    'label': args.label,
                    'rig': rig,
                    'stats': asdict(stats),
                    'wav': str(wav_path) if wav_path else None,
                },
                indent=2,
            )
        )
    else:
        _print_report(args.label, rig, stats, wav_path)
    return 0


if __name__ == '__main__':
    run_cli(main)
