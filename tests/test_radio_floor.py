"""Radio floor-probe unit tests — the pure analysis math (analyze/lowpass_alpha)."""

from __future__ import annotations

import math
from array import array

import pytest

from tools.radio_floor import analyze, lowpass_alpha


def tone(freq: float, seconds: float = 1.0, rate: int = 48000, amp: int = 6000) -> array:
    """A pure sine at ``freq`` as S16 mono samples."""
    n = int(rate * seconds)
    return array('h', [int(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)])


def lcg_noise(seconds: float = 1.0, rate: int = 48000, amp: int = 6000) -> array:
    """Deterministic white-ish noise via an LCG (flat-ish spectrum)."""
    out = array('h')
    state = 12345
    for _ in range(int(rate * seconds)):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        out.append(int(amp * (state / 0x7FFFFFFF - 0.5)))
    return out


class TestLowpassAlpha:
    def test_in_unit_range(self):
        assert 0.0 < lowpass_alpha(300.0, 48000) < 1.0

    def test_higher_cutoff_smooths_less(self):
        # A higher cutoff → larger smoothing coefficient (passes more).
        assert lowpass_alpha(1000.0, 48000) > lowpass_alpha(100.0, 48000)


class TestAnalyze:
    def test_empty_raises(self):
        with pytest.raises(RuntimeError):
            analyze(array('h'))

    def test_low_tone_reads_low_band(self):
        # 60 Hz sits well below the 300 Hz split → low-band dominated.
        assert analyze(tone(60.0)).low_fraction > 0.8

    def test_high_tone_reads_high_band(self):
        # 6 kHz is far above the split → almost all energy in the high band.
        assert analyze(tone(6000.0)).low_fraction < 0.1

    def test_white_noise_is_high_weighted(self):
        # A flat spectrum puts most energy above the 300 Hz single-pole split.
        assert analyze(lcg_noise()).low_fraction < 0.2

    def test_levels_are_sane(self):
        # amp 6000 → peak ≈ 6000/32768 ≈ −14.7 dBFS, sine RMS ≈ 3 dB below that.
        stats = analyze(tone(1000.0, amp=6000))
        assert -22 < stats.rms_dbfs < -16
        assert stats.peak_dbfs > stats.rms_dbfs
        assert stats.block_min_dbfs <= stats.block_median_dbfs <= stats.block_max_dbfs
