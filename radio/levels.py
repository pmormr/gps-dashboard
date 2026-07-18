"""Squelch keeper — the recorder's level-management state machine.

Pure and clockless like the VOX gate: time is measured in heartbeats (~60 s)
and audio blocks (~100 ms), so every rule is table-testable; all rigctld I/O
stays in the recorder. AF is *not* managed here — it is the record-level
calibration, a config constant the recorder re-asserts on every heartbeat
(the ID-5100 forgets CI-V-set levels across a power cycle).

Squelch, by contrast, is an operator control (it tracks the local RF noise
floor), so the rig dial always wins live. Three rules bound it:

- **Operator memory.** Each heartbeat's SQL reading is adopted as the
  operator's setting; after an offline gap (rig power cycle) a changed
  reading is restored to the remembered value — the keeper enforces the
  operator's own choice, never a config value.
- **Deaf clamp.** Readings above ``sane_max`` are presumed accidents (the
  radio records nothing up there, and silence carries no evidence), never
  adopted, and clamped back to the remembered value once they persist two
  heartbeats.
- **Guard raises.** The two evidence-backed too-low signatures each raise
  squelch one bounded step: a *flap storm* (discards flooding the rolling
  window — the marginal-squelch crackle signature) and *stuck-open static*
  (an unbroken minutes-long above-threshold run at near-constant block RMS —
  voice breathes, static does not; the ladder stops as soon as the gate
  closes, roughly the minimum squelch that holds).

Raises never reverse automatically — "band is quiet" and "squelch too tight"
are indistinguishable from here, so lowering stays the operator's call.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

#: Rigctld levels are 0..255 quantized (~0.004 steps); differences under this
#: are the same setting, not an operator change.
SQL_EPSILON = 0.01
#: Rolling discard window and the flap-storm re-raise spacing, in heartbeats.
DISCARD_WINDOW_HEARTBEATS = 10
STORM_COOLDOWN_HEARTBEATS = 10
#: Stuck-open static re-raises faster — confidence is high once detected.
STATIC_COOLDOWN_HEARTBEATS = 2
#: An unbroken above-threshold run this long (~3 min at 100 ms blocks) with
#: block-RMS spread under the stddev bound reads as static, not speech.
STATIC_RUN_BLOCKS = 1800
STATIC_STDDEV_DB = 2.5
#: Consecutive out-of-band heartbeats before the deaf clamp fires.
CLAMP_HEARTBEATS = 2


@dataclass
class LevelKeeper:
    """Clockless squelch state machine; the recorder feeds it and applies its actions.

    Attributes:
        open_dbfs: The VOX open threshold — blocks above it extend the
            static-run tracker, blocks below reset it.
        sane_max: Deaf-clamp bound; readings above it are never adopted.
            ``None`` disables the clamp.
        guard_discards: Flap-storm trigger — discards per rolling window.
            ``0`` disables the storm guard.
        guard_max_sql: Ceiling no guard raise may exceed.
        guard_step: Size of one guard raise.
        known_sql: The operator's last in-band setting (the restore target).
        online: Whether the last heartbeat reached the rig.
    """

    open_dbfs: float
    sane_max: float | None = 0.5
    guard_discards: int = 15
    guard_max_sql: float = 0.35
    guard_step: float = 0.03

    known_sql: float | None = None
    online: bool = False
    _discards_now: int = 0
    _discard_window: deque[int] = field(
        default_factory=lambda: deque(maxlen=DISCARD_WINDOW_HEARTBEATS)
    )
    _high_heartbeats: int = 0
    _storm_cooldown: int = 0
    _static_cooldown: int = 0
    _run_blocks: int = 0
    _run_mean: float = 0.0
    _run_m2: float = 0.0

    def feed_block(self, rms: float) -> None:
        """Track the unbroken above-threshold run (Welford mean/variance).

        Args:
            rms: One 100 ms block's RMS in dBFS.
        """
        if rms < self.open_dbfs:
            self._run_blocks = 0
            self._run_mean = self._run_m2 = 0.0
            return
        self._run_blocks += 1
        delta = rms - self._run_mean
        self._run_mean += delta / self._run_blocks
        self._run_m2 += delta * (rms - self._run_mean)

    def note_discard(self) -> None:
        """Count one VOX discard toward the current heartbeat's bucket."""
        self._discards_now += 1

    @property
    def _static_stuck(self) -> bool:
        if self._run_blocks < STATIC_RUN_BLOCKS:
            return False
        stddev = math.sqrt(self._run_m2 / (self._run_blocks - 1))
        return stddev < STATIC_STDDEV_DB

    def heartbeat(self, sql: float | None) -> tuple[str, float] | None:
        """Advance one heartbeat and decide the squelch action, if any.

        Args:
            sql: The rig's current SQL reading, or ``None`` when the rig was
                unreachable (rigctld error — typically the rig is powered off).

        Returns:
            ``(reason, value)`` when the recorder should set SQL to ``value``
            (reason ∈ ``restore``/``clamp``/``flap storm``/``stuck-open
            static``), else ``None``. At most one action per heartbeat.
        """
        self._discard_window.append(self._discards_now)
        self._discards_now = 0
        self._storm_cooldown = max(0, self._storm_cooldown - 1)
        self._static_cooldown = max(0, self._static_cooldown - 1)

        if sql is None:
            self.online = False
            return None
        came_back = not self.online
        self.online = True

        if came_back and self.known_sql is not None and abs(sql - self.known_sql) > SQL_EPSILON:
            return self._set('restore', self.known_sql)

        if self.sane_max is not None and sql > self.sane_max + SQL_EPSILON:
            self._high_heartbeats += 1
            if self._high_heartbeats >= CLAMP_HEARTBEATS and self.known_sql is not None:
                return self._set('clamp', self.known_sql)
            return None
        self._high_heartbeats = 0
        self.known_sql = sql

        if (
            self.guard_discards > 0
            and self._storm_cooldown == 0
            and sum(self._discard_window) >= self.guard_discards
        ):
            target = min(sql + self.guard_step, self.guard_max_sql)
            if target > sql + SQL_EPSILON:
                self._storm_cooldown = STORM_COOLDOWN_HEARTBEATS
                return self._set('flap storm', target)

        if self._static_cooldown == 0 and self._static_stuck:
            target = min(sql + self.guard_step, self.guard_max_sql)
            if target > sql + SQL_EPSILON:
                self._static_cooldown = STATIC_COOLDOWN_HEARTBEATS
                return self._set('stuck-open static', target)

        return None

    def _set(self, reason: str, value: float) -> tuple[str, float]:
        """Record ``value`` as the new known setting and return the action."""
        self.known_sql = value
        return reason, value
