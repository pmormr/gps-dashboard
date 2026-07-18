"""Table-tests for the LevelKeeper squelch state machine (radio/levels.py)."""

from radio.levels import (
    DISCARD_WINDOW_HEARTBEATS,
    STATIC_RUN_BLOCKS,
    LevelKeeper,
)

OPEN = -40.0


def make(**kw: object) -> LevelKeeper:
    """A keeper with test-friendly defaults."""
    return LevelKeeper(open_dbfs=OPEN, **kw)  # type: ignore[arg-type]


def feed_static(k: LevelKeeper, blocks: int, rms: float = -17.0) -> None:
    """Feed an unbroken constant-RMS run (the open-squelch static signature)."""
    for _ in range(blocks):
        k.feed_block(rms)


class TestOperatorMemory:
    def test_reading_adopted_as_known(self) -> None:
        k = make()
        assert k.heartbeat(0.2) is None
        assert k.known_sql == 0.2

    def test_live_change_adopted_without_action(self) -> None:
        k = make()
        k.heartbeat(0.2)
        assert k.heartbeat(0.3) is None
        assert k.known_sql == 0.3

    def test_restore_after_offline_gap(self) -> None:
        k = make()
        k.heartbeat(0.2)
        k.heartbeat(None)
        assert k.heartbeat(0.173) == ('restore', 0.2)

    def test_no_restore_when_unchanged_across_gap(self) -> None:
        k = make()
        k.heartbeat(0.2)
        k.heartbeat(None)
        assert k.heartbeat(0.2) is None

    def test_quantization_jitter_is_not_a_change(self) -> None:
        k = make()
        k.heartbeat(0.2)
        k.heartbeat(None)
        assert k.heartbeat(0.196) is None

    def test_first_reading_after_offline_start_adopted(self) -> None:
        k = make()
        k.heartbeat(None)
        assert k.heartbeat(0.25) is None
        assert k.known_sql == 0.25


class TestDeafClamp:
    def test_single_high_blip_no_action(self) -> None:
        k = make()
        k.heartbeat(0.2)
        assert k.heartbeat(0.9) is None
        assert k.known_sql == 0.2

    def test_persistent_high_clamps_to_known(self) -> None:
        k = make()
        k.heartbeat(0.2)
        k.heartbeat(0.9)
        assert k.heartbeat(0.9) == ('clamp', 0.2)

    def test_recovery_resets_the_counter(self) -> None:
        k = make()
        k.heartbeat(0.2)
        k.heartbeat(0.9)
        k.heartbeat(0.2)
        assert k.heartbeat(0.9) is None

    def test_no_known_value_no_clamp(self) -> None:
        k = make()
        k.heartbeat(0.9)
        assert k.heartbeat(0.9) is None

    def test_disabled_adopts_anything(self) -> None:
        k = make(sane_max=None)
        k.heartbeat(0.2)
        assert k.heartbeat(0.9) is None
        assert k.known_sql == 0.9


class TestFlapStorm:
    def test_discard_flood_raises_one_step(self) -> None:
        k = make()
        k.heartbeat(0.2)
        for _ in range(15):
            k.note_discard()
        reason, value = k.heartbeat(0.2) or ('', 0.0)
        assert reason == 'flap storm'
        assert abs(value - 0.23) < 1e-9

    def test_cooldown_blocks_immediate_reraise(self) -> None:
        k = make()
        k.heartbeat(0.2)
        for _ in range(15):
            k.note_discard()
        k.heartbeat(0.2)
        for _ in range(15):
            k.note_discard()
        assert k.heartbeat(0.23) is None

    def test_raise_never_exceeds_cap(self) -> None:
        k = make()
        k.heartbeat(0.35)
        for _ in range(15):
            k.note_discard()
        assert k.heartbeat(0.35) is None

    def test_disabled_when_zero(self) -> None:
        k = make(guard_discards=0)
        k.heartbeat(0.2)
        for _ in range(50):
            k.note_discard()
        assert k.heartbeat(0.2) is None

    def test_slow_trickle_rolls_off_the_window(self) -> None:
        k = make()
        k.heartbeat(0.2)
        for _ in range(DISCARD_WINDOW_HEARTBEATS + 2):
            k.note_discard()
            assert k.heartbeat(0.2) is None


class TestStuckOpenStatic:
    def test_low_variance_run_raises(self) -> None:
        k = make()
        k.heartbeat(0.1)
        feed_static(k, STATIC_RUN_BLOCKS)
        reason, value = k.heartbeat(0.1) or ('', 0.0)
        assert reason == 'stuck-open static'
        assert abs(value - 0.13) < 1e-9

    def test_voice_like_variance_never_triggers(self) -> None:
        k = make()
        k.heartbeat(0.1)
        for i in range(STATIC_RUN_BLOCKS):
            k.feed_block(-20.0 if i % 2 else -38.0)
        assert k.heartbeat(0.1) is None

    def test_run_resets_when_gate_worthy_silence_returns(self) -> None:
        k = make()
        k.heartbeat(0.1)
        feed_static(k, STATIC_RUN_BLOCKS - 1)
        k.feed_block(-50.0)
        feed_static(k, 10)
        assert k.heartbeat(0.1) is None

    def test_reraises_while_static_persists(self) -> None:
        k = make()
        k.heartbeat(0.1)
        feed_static(k, STATIC_RUN_BLOCKS)
        assert k.heartbeat(0.1) is not None
        feed_static(k, 600)
        assert k.heartbeat(0.13) is None
        feed_static(k, 600)
        reason, value = k.heartbeat(0.13) or ('', 0.0)
        assert reason == 'stuck-open static'
        assert abs(value - 0.16) < 1e-9

    def test_ladder_clamps_to_cap_then_stops(self) -> None:
        k = make()
        k.heartbeat(0.33)
        feed_static(k, STATIC_RUN_BLOCKS)
        reason, value = k.heartbeat(0.33) or ('', 0.0)
        assert abs(value - 0.35) < 1e-9
        feed_static(k, 600)
        k.heartbeat(0.35)
        feed_static(k, 600)
        assert k.heartbeat(0.35) is None


class TestPriority:
    def test_restore_wins_over_storm_in_one_heartbeat(self) -> None:
        k = make()
        k.heartbeat(0.2)
        k.heartbeat(None)
        for _ in range(15):
            k.note_discard()
        assert k.heartbeat(0.1) == ('restore', 0.2)

    def test_guard_raise_becomes_the_restore_target(self) -> None:
        k = make()
        k.heartbeat(0.2)
        for _ in range(15):
            k.note_discard()
        k.heartbeat(0.2)
        assert abs((k.known_sql or 0.0) - 0.23) < 1e-9
        k.heartbeat(None)
        assert k.heartbeat(0.173) == ('restore', k.known_sql)
