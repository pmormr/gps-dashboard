"""Waveform envelope tests — resample + absolute dBFS encode (``radio/waveform.py``)."""

from __future__ import annotations

from radio.waveform import build_envelope


class TestBuildEnvelope:
    def test_empty_is_empty(self):
        assert build_envelope([], 96) == []

    def test_full_scale_hits_ceiling(self):
        # 0 dBFS → the top of the 0..255 window.
        assert build_envelope([32768], 96) == [255]

    def test_silence_hits_floor(self):
        assert build_envelope([0], 96) == [0]

    def test_short_capture_yields_one_bar_per_block(self):
        # Fewer blocks than buckets → no upsample/pad; one bar each.
        assert len(build_envelope([100, 200, 300], 96)) == 3

    def test_resample_is_max_in_bin(self):
        # buckets=2 over 4 blocks: bins [0,0] and [32768,0]; max keeps the transient.
        assert build_envelope([0, 0, 32768, 0], 2) == [0, 255]

    def test_max_not_mean(self):
        # One bucket over a loud+silent pair: max → 255 (a mean would sit lower).
        assert build_envelope([32768, 0], 1) == [255]

    def test_encode_is_monotonic(self):
        bars = build_envelope([0, 1024, 8192, 32768], 4)
        assert bars == sorted(bars)
        assert bars[0] == 0 and bars[-1] == 255

    def test_quarter_scale_is_mid_high(self):
        # ±8192 (quarter scale) ≈ −12 dBFS → ~81% of a −64 dBFS window.
        assert build_envelope([8192], 1) == [207]
