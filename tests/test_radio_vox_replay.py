"""Tests for the stored-capture rescoring tool's pure helper."""

from __future__ import annotations

import wave
from array import array

from radio.recorder import BLOCK_FRAMES, SAMPLE_RATE
from tools.radio_vox_replay import loud_blocks


def _write_wav(path, blocks: list[int]) -> None:
    """Write one WAV of constant-amplitude 100 ms blocks (±amp alternating)."""
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for amp in blocks:
            w.writeframes(array('h', [amp, -amp] * (BLOCK_FRAMES // 2)).tobytes())


def test_counts_only_loud_blocks(tmp_path):
    # ±8192 ≈ −12 dBFS (loud); ±100 ≈ −50 dBFS (floor).
    path = tmp_path / 'x.wav'
    _write_wav(path, [100, 8192, 100, 8192, 8192, 100])
    assert loud_blocks(path, open_dbfs=-40.0) == 3


def test_all_quiet_is_zero(tmp_path):
    path = tmp_path / 'x.wav'
    _write_wav(path, [100, 100])
    assert loud_blocks(path, open_dbfs=-40.0) == 0
