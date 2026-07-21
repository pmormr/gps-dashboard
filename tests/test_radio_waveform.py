"""Waveform envelope tests — sub-block sample + resample + absolute dBFS encode."""

from __future__ import annotations

from array import array

from radio.waveform import block_subpeaks, build_envelope


class TestBlockSubpeaks:
    def test_empty_block_yields_k_zeros(self):
        assert block_subpeaks(b'', 5) == [0, 0, 0, 0, 0]

    def test_constant_block_peaks_each_window(self):
        block = array('h', [1000, -1000] * 480).tobytes()  # 960 samples
        assert block_subpeaks(block, 4) == [1000, 1000, 1000, 1000]

    def test_isolates_a_spike_to_its_window(self):
        block = array('h', [0] * 400 + [5000] * 400 + [0] * 400).tobytes()
        # 1200 samples / 3 windows = 400 each; the spike sits wholly in window 1.
        assert block_subpeaks(block, 3) == [0, 5000, 0]

    def test_negative_extreme_counts_as_peak(self):
        block = array('h', [-8000] * 100).tobytes()
        assert block_subpeaks(block, 2) == [8000, 8000]


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
