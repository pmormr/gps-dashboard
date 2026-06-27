"""Tests for the shared sensor-reader framework (``sensors/runner.py``).

The reader loops (``poll_loop``/``publish_loop``) are covered by the per-reader
tests; this pins the pieces they all build on: the heartbeat gate, the fake random
walk, and the simple read→publish loop bme680 now runs through.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator

import pytest

from sensors import runner
from sensors.runner import Heartbeat, bounded_step, bounded_walk, run_simple_publisher


class StubClient:
    """Captures publishes so the loop can be driven without a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        """Record one publish."""
        self.published.append((topic, payload))


class OneSensor:
    """A sensor that returns a fixed reading (one numeric column, one None column)."""

    def read(self) -> dict[str, float | None]:
        """Return the fixed reading."""
        return {'temp_c': 21.5, 'gas_ohms': None}


def test_bounded_step_stays_within_bound() -> None:
    """A step never exceeds ``scale·(|baseline|+1)`` from the current value."""
    for _ in range(1000):
        stepped = bounded_step(10.0, 10.0, scale=0.05)
        assert abs(stepped - 10.0) <= 0.05 * (10.0 + 1.0) + 1e-9


def test_bounded_walk_returns_rounded_snapshot_and_advances_state() -> None:
    """Every column is returned rounded, and the running state is advanced in place."""
    state = {'a': 5.0, 'b': 100.0}
    snapshot = bounded_walk(state, {'a': 5.0, 'b': 100.0}, scale=0.02, round_to=2)
    assert set(snapshot) == {'a', 'b'}
    assert all(isinstance(value, float) for value in snapshot.values())
    assert all(round(value, 2) == value for value in snapshot.values())
    assert state['a'] != 5.0 or state['b'] != 100.0


def test_heartbeat_not_due_before_interval() -> None:
    """No emit (and counters retained) until the interval elapses."""
    hb = Heartbeat(interval_s=1000.0)
    hb.bump('published')
    assert hb.maybe_emit() is False


def test_heartbeat_emits_then_resets(capsys: pytest.CaptureFixture[str]) -> None:
    """A due heartbeat prints context + counters, then clears the window's counters."""
    hb = Heartbeat(interval_s=0.0)
    hb.bump('published')
    hb.bump('nulls', 3)
    assert hb.maybe_emit(state='running') is True
    first = capsys.readouterr().out
    assert 'state=running' in first
    assert 'published=1' in first
    assert 'nulls=3' in first

    assert hb.maybe_emit(state='running') is True
    second = capsys.readouterr().out
    assert 'published=' not in second  # counters reset after the previous emit


def test_run_simple_publisher_emits_one_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """``once`` publishes a single ``ts`` + reading snapshot through the session client."""
    stub = StubClient()

    @contextlib.contextmanager
    def fake_session(
        node: str, sensor_type: str, *, started_msg: str | None = None, announce_online: bool = True
    ) -> Iterator[tuple[StubClient, str, str]]:
        yield stub, f'sensors/{node}/{sensor_type}', f'sensors/{node}/{sensor_type}/status'

    monkeypatch.setattr(runner, 'publisher_session', fake_session)
    run_simple_publisher(OneSensor(), node='cabin', sensor_type='bme680', interval=0.0, once=True)

    assert len(stub.published) == 1
    topic, raw = stub.published[0]
    assert topic == 'sensors/cabin/bme680'
    payload = json.loads(raw)
    assert set(payload) == {'ts', 'temp_c', 'gas_ohms'}
    assert payload['temp_c'] == 21.5
    assert payload['gas_ohms'] is None
