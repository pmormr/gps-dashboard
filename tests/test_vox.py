"""VOX gate unit tests — block energy math and the open/close state machine."""

from __future__ import annotations

import math
from array import array

import pytest

from radio.vox import SILENCE_DBFS, GateEvent, VoxGate, amplitude_dbfs, block_energy, rms_dbfs


def s16(samples: list[int]) -> bytes:
    """Pack samples as raw S16_LE mono bytes."""
    return array('h', samples).tobytes()


class TestBlockEnergy:
    def test_empty_block(self):
        assert block_energy(b'') == (0, 0, 0)

    def test_silence(self):
        assert block_energy(s16([0, 0, 0])) == (0, 0, 3)

    def test_full_scale_negative_peak(self):
        sq, peak, n = block_energy(s16([-32768]))
        assert (sq, peak, n) == (32768**2, 32768, 1)

    def test_mixed_amplitudes(self):
        sq, peak, n = block_energy(s16([100, -200, 50]))
        assert (sq, peak, n) == (100**2 + 200**2 + 50**2, 200, 3)

    def test_trailing_odd_byte_ignored(self):
        assert block_energy(s16([100]) + b'\x01') == (100**2, 100, 1)


class TestDbfs:
    def test_full_scale_is_zero_dbfs(self):
        assert amplitude_dbfs(32768.0) == pytest.approx(0.0)

    def test_zero_amplitude_floors(self):
        assert amplitude_dbfs(0.0) == SILENCE_DBFS

    def test_half_scale(self):
        assert amplitude_dbfs(16384.0) == pytest.approx(20 * math.log10(0.5))

    def test_rms_of_constant_amplitude(self):
        # ±A alternating has RMS exactly A.
        sq, _, n = block_energy(s16([8192, -8192] * 10))
        assert rms_dbfs(sq, n) == pytest.approx(amplitude_dbfs(8192.0))

    def test_rms_of_nothing_is_silence(self):
        assert rms_dbfs(0, 0) == SILENCE_DBFS


class TestVoxGate:
    def make(self, hang: int = 3, cap: int = 100) -> VoxGate:
        return VoxGate(open_dbfs=-40.0, close_dbfs=-45.0, hang_blocks=hang, max_blocks=cap)

    def test_hysteresis_must_hold(self):
        with pytest.raises(ValueError):
            VoxGate(open_dbfs=-45.0, close_dbfs=-40.0, hang_blocks=1, max_blocks=1)

    def test_stays_closed_below_threshold(self):
        g = self.make()
        assert [g.feed(-50.0), g.feed(-41.0), g.feed(-44.0)] == [None, None, None]
        assert not g.is_open

    def test_opens_at_threshold_inclusive(self):
        g = self.make()
        assert g.feed(-40.0) is GateEvent.OPEN
        assert g.is_open

    def test_hysteresis_band_holds_gate_open(self):
        # Between close (−45) and open (−40): never re-opens, never counts as quiet.
        g = self.make(hang=2)
        g.feed(-30.0)
        assert [g.feed(-44.0), g.feed(-44.9), g.feed(-45.0)] == [None, None, None]
        assert g.is_open

    def test_closes_after_hang_blocks(self):
        g = self.make(hang=3)
        g.feed(-30.0)
        assert g.feed(-46.0) is None
        assert g.feed(-46.0) is None
        assert g.feed(-46.0) is GateEvent.CLOSE
        assert not g.is_open

    def test_loud_block_resets_hang(self):
        g = self.make(hang=2)
        g.feed(-30.0)
        g.feed(-46.0)
        assert g.feed(-30.0) is None  # resets the quiet run
        g.feed(-46.0)
        assert g.feed(-46.0) is GateEvent.CLOSE

    def test_max_blocks_force_closes_held_carrier(self):
        g = self.make(cap=5)
        assert g.feed(-30.0) is GateEvent.OPEN  # block 1
        assert [g.feed(-30.0) for _ in range(3)] == [None, None, None]  # blocks 2-4
        assert g.feed(-30.0) is GateEvent.CLOSE  # block 5 hits the cap
        assert not g.is_open

    def test_reopens_after_close(self):
        g = self.make(hang=1)
        g.feed(-30.0)
        assert g.feed(-50.0) is GateEvent.CLOSE
        assert g.feed(-39.0) is GateEvent.OPEN


class TestCommitRule:
    """The 2e commit rule: a closing capture keeps only with enough loud blocks."""

    def make(self, min_loud: int, hang: int = 2, cap: int = 100) -> VoxGate:
        return VoxGate(
            open_dbfs=-40.0,
            close_dbfs=-45.0,
            hang_blocks=hang,
            max_blocks=cap,
            min_loud_blocks=min_loud,
        )

    def test_single_transient_discards(self):
        # The observed blip shape: one loud burst, then floor to hang-close.
        g = self.make(min_loud=6)
        assert g.feed(-30.0) is GateEvent.OPEN
        assert g.feed(-30.0) is None
        assert g.feed(-50.0) is None
        assert g.feed(-50.0) is GateEvent.DISCARD
        assert not g.is_open

    def test_voice_like_activity_commits(self):
        # Scattered syllable bursts accumulate loud blocks across the capture.
        g = self.make(min_loud=6, hang=3)
        g.feed(-30.0)
        for _ in range(3):
            g.feed(-50.0)  # inter-syllable quieting, hang never expires...
            g.feed(-50.0)
            g.feed(-30.0)  # ...because the next burst re-arms it
            g.feed(-30.0)
        g.feed(-50.0)
        g.feed(-50.0)
        assert g.feed(-50.0) is GateEvent.CLOSE  # 7 loud blocks >= 6

    def test_hysteresis_band_blocks_are_not_loud(self):
        # −42 holds the gate open (>= close) but counts toward the rule nothing.
        g = self.make(min_loud=2, hang=2)
        g.feed(-30.0)
        for _ in range(20):
            assert g.feed(-42.0) is None
        g.feed(-50.0)
        assert g.feed(-50.0) is GateEvent.DISCARD  # 1 loud block < 2

    def test_max_blocks_force_close_below_rule_discards(self):
        g = self.make(min_loud=3, cap=4)
        g.feed(-30.0)
        g.feed(-42.0)
        g.feed(-42.0)
        assert g.feed(-42.0) is GateEvent.DISCARD  # cap hit with 1 loud block

    def test_loud_count_resets_per_capture(self):
        g = self.make(min_loud=2, hang=1)
        g.feed(-30.0)
        g.feed(-30.0)
        assert g.feed(-50.0) is GateEvent.CLOSE
        g.feed(-30.0)
        assert g.loud_blocks == 1
        assert g.feed(-50.0) is GateEvent.DISCARD

    def test_default_min_commits_everything(self):
        g = VoxGate(open_dbfs=-40.0, close_dbfs=-45.0, hang_blocks=1, max_blocks=100)
        g.feed(-30.0)
        assert g.feed(-50.0) is GateEvent.CLOSE

    def test_reset_returns_to_closed_without_event(self):
        g = VoxGate(
            open_dbfs=-40.0, close_dbfs=-45.0, hang_blocks=3, max_blocks=100, min_loud_blocks=2
        )
        g.feed(-30.0)
        g.feed(-30.0)
        assert g.is_open and g.loud_blocks == 2
        g.reset()
        assert not g.is_open and g.loud_blocks == 0
        # After reset the next loud block opens a fresh capture (no lingering state).
        assert g.feed(-30.0) is GateEvent.OPEN
        assert g.loud_blocks == 1
