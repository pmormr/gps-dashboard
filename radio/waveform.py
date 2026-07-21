"""Transmission waveform envelope — pure record-time derivation.

The recorder already measures a per-block ``peak`` in :func:`radio.vox.block_energy`
and would otherwise throw the per-block detail away. This resamples that stream of
per-block peaks into a fixed-N compact envelope stored on the ``radio_transmissions``
row, so the log stays scannable and the player draws a waveform with zero WAV
re-decode — and the envelope survives the retention pruner that deletes WAVs but
keeps rows.

Encoding is **absolute** (cross-clip comparable), not per-clip normalized: each
bucket's peak maps through a fixed dBFS window to 0..255, so a quieter talker reads
shorter across the whole log. The window ceiling is 0 dBFS; the floor is
:data:`WAVEFORM_FLOOR_DBFS`, unit-tunable the way the gate's OPEN/CLOSE thresholds
are — the capture-path floor already moved once (USB ground isolator), so it isn't
frozen as a magic number.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from radio.vox import amplitude_dbfs

#: Buckets per stored envelope. Captures shorter than this many blocks (~9.6 s at
#: 100 ms/block) yield fewer buckets — the renderer handles variable length.
WAVEFORM_BUCKETS = 96

#: dBFS mapped to 0 (bar floor); 0 dBFS maps to 255 (bar ceiling). Default −64 tracks
#: the isolator-era capture floor; env-overridable so it isn't a frozen magic number.
WAVEFORM_FLOOR_DBFS = float(os.environ.get('GPS_RADIO_WAVEFORM_FLOOR_DBFS', '-64'))


def _encode(peak: int) -> int:
    """Map one bucket's peak amplitude to a 0..255 bar height (absolute dBFS window).

    Args:
        peak: Linear S16 peak amplitude for the bucket (0..32768).

    Returns:
        Bar height 0..255: 0 dBFS → 255, at/below the floor → 0.
    """
    span = -WAVEFORM_FLOOR_DBFS
    frac = (amplitude_dbfs(float(peak)) - WAVEFORM_FLOOR_DBFS) / span
    return max(0, min(255, round(frac * 255)))


def build_envelope(peaks: Sequence[int], buckets: int) -> list[int]:
    """Resample per-block peaks to a fixed-N absolute-encoded bar envelope.

    Max-in-bin resample (peaks accumulate energy, so a max keeps transients) to at
    most ``buckets`` bars, then absolute-encode each bar to 0..255. A capture shorter
    than ``buckets`` blocks yields one bar per block (``< buckets``); the renderer
    handles the variable length rather than upsampling or padding.

    Args:
        peaks: One linear S16 peak amplitude per audio block, in order.
        buckets: Target bar count (the fixed N; :data:`WAVEFORM_BUCKETS`).

    Returns:
        ``min(buckets, len(peaks))`` bar heights, each 0..255; ``[]`` when empty.
    """
    n = min(buckets, len(peaks))
    if n <= 0:
        return []
    m = len(peaks)
    return [_encode(max(peaks[i * m // n : (i + 1) * m // n])) for i in range(n)]
