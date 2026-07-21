"""VOX gate — pure block-energy math + the open/close state machine.

Kept free of I/O so the recorder's gating behavior is table-testable: the
recorder feeds one RMS measurement per fixed-size audio block and acts on the
returned transitions. All levels are dBFS relative to S16 full scale (0 dBFS =
±32768); all times are counted in fed blocks, so the machine is clockless and
deterministic. The live threshold values and the measured floor they track are
the recorder's concern (``radio/recorder.py``).
"""

from __future__ import annotations

import array
import math
from enum import Enum

FULL_SCALE = 32768.0
#: Finite stand-in for log(0) so silent blocks stay comparable to thresholds.
SILENCE_DBFS = -120.0


def block_energy(block: bytes) -> tuple[int, int, int]:
    """Measure one S16_LE mono block.

    Returns raw integer sums rather than dB so a multi-block capture can
    accumulate them and compute one whole-file RMS/peak at close.

    Args:
        block: Raw little-endian signed-16-bit mono samples. A trailing odd
            byte (torn read) is ignored.

    Returns:
        ``(sum_of_squares, peak_amplitude, n_samples)``; all zeros for an
        empty block.
    """
    samples = array.array('h')
    usable = len(block) - (len(block) % 2)
    samples.frombytes(block[:usable])
    if not samples:
        return 0, 0, 0
    peak = max(max(samples), -min(samples))
    sq = sum(s * s for s in samples)
    return sq, peak, len(samples)


def amplitude_dbfs(amplitude: float) -> float:
    """dBFS of a linear S16 amplitude, floored at :data:`SILENCE_DBFS`.

    Args:
        amplitude: A linear amplitude on the S16 scale (0..32768).

    Returns:
        The level in dBFS (≤ 0 for in-range signals).
    """
    if amplitude <= 0:
        return SILENCE_DBFS
    return max(SILENCE_DBFS, 20.0 * math.log10(amplitude / FULL_SCALE))


def rms_dbfs(sq_sum: float, n_samples: int) -> float:
    """RMS level in dBFS from an accumulated sum of squares.

    Args:
        sq_sum: Sum of squared sample values.
        n_samples: Number of samples the sum covers.

    Returns:
        The RMS level in dBFS; :data:`SILENCE_DBFS` when ``n_samples`` is 0.
    """
    if n_samples <= 0:
        return SILENCE_DBFS
    return amplitude_dbfs(math.sqrt(sq_sum / n_samples))


class GateEvent(Enum):
    """A gate transition produced by :meth:`VoxGate.feed`."""

    OPEN = 'open'
    CLOSE = 'close'
    DISCARD = 'discard'


class VoxGate:
    """Hysteresis VOX gate over per-block RMS measurements.

    Opens when a block's RMS reaches ``open_dbfs``. While open, any block at or
    above ``close_dbfs`` (the lower hysteresis threshold) resets the hang
    counter; after ``hang_blocks`` consecutive quieter blocks the gate closes.
    ``max_blocks`` force-closes a stuck-open gate (a held carrier) so no capture
    grows unbounded — the very next loud block reopens, splitting the capture
    into consecutive files instead.

    A closing gate emits :attr:`GateEvent.CLOSE` (keep the capture) only when
    the capture accumulated at least ``min_loud_blocks`` blocks at or above
    ``open_dbfs``; otherwise :attr:`GateEvent.DISCARD`. This is the commit rule
    from the 2026-07-18 corpus analysis: false triggers (squelch crackle, rig
    beeps) are single 1–5-block transients at exactly voice level, so *level*
    cannot separate them — total above-threshold activity can (voice measured
    10–12 loud blocks). A capture that hovers between the thresholds without
    re-crossing ``open_dbfs`` is marginal noise and discards by the same rule.

    Attributes:
        is_open: Whether the gate is currently open.
        loud_blocks: Blocks at/above ``open_dbfs`` in the current capture — the
            caller watches this to materialize a pending capture the moment it
            crosses ``min_loud_blocks``.
    """

    def __init__(
        self,
        open_dbfs: float,
        close_dbfs: float,
        hang_blocks: int,
        max_blocks: int,
        min_loud_blocks: int = 1,
    ) -> None:
        """Configure the gate.

        Args:
            open_dbfs: RMS level at which a closed gate opens.
            close_dbfs: RMS level below which open-gate blocks count toward the
                hang timeout. Must not exceed ``open_dbfs`` (hysteresis).
            hang_blocks: Consecutive quiet blocks that close the gate.
            max_blocks: Hard cap on blocks fed while open before a forced close.
            min_loud_blocks: Loud blocks a capture needs to commit on close.
                The default (1) commits every capture — the opening block is
                always loud — i.e. no commit rule.

        Raises:
            ValueError: If ``close_dbfs`` exceeds ``open_dbfs``.
        """
        if close_dbfs > open_dbfs:
            raise ValueError('close_dbfs must not exceed open_dbfs (hysteresis)')
        self.open_dbfs = open_dbfs
        self.close_dbfs = close_dbfs
        self.hang_blocks = hang_blocks
        self.max_blocks = max_blocks
        self.min_loud_blocks = min_loud_blocks
        self.is_open = False
        self.loud_blocks = 0
        self._quiet_run = 0
        self._blocks_open = 0

    def feed(self, level_dbfs: float) -> GateEvent | None:
        """Advance the gate by one block.

        Args:
            level_dbfs: The block's RMS level in dBFS.

        Returns:
            The transition this block caused, or ``None``. On
            :attr:`GateEvent.OPEN` the caller starts a capture *including this
            block*; on :attr:`GateEvent.CLOSE` it finalizes after writing it;
            on :attr:`GateEvent.DISCARD` it drops the capture unrecorded.
        """
        if not self.is_open:
            if level_dbfs >= self.open_dbfs:
                self.is_open = True
                self._quiet_run = 0
                self._blocks_open = 1
                self.loud_blocks = 1
                return GateEvent.OPEN
            return None
        self._blocks_open += 1
        if level_dbfs >= self.open_dbfs:
            self.loud_blocks += 1
        if level_dbfs >= self.close_dbfs:
            self._quiet_run = 0
        else:
            self._quiet_run += 1
        if self._quiet_run >= self.hang_blocks or self._blocks_open >= self.max_blocks:
            self.is_open = False
            committed = self.loud_blocks >= self.min_loud_blocks
            return GateEvent.CLOSE if committed else GateEvent.DISCARD
        return None
