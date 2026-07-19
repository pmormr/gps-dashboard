"""Tests for the shared sensor-reader framework (``sensors/runner.py``).

The reader loops (``poll_loop``, the readers' ``read_snapshot`` adapters) are covered
by the per-reader tests; this pins the pieces they all build on: the heartbeat gate,
the fake random walk, the simple read→publish loop bme680 runs through, and the
status-gated loop the Victron + fridge readers share.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator

import pytest

from sensors import runner
from sensors.runner import (
    Heartbeat,
    bounded_step,
    bounded_walk,
    run_fleet_publisher,
    run_gated_publisher,
    run_simple_publisher,
)


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


class FleetOfTwo:
    """A fleet source with one live node and one dropped node."""

    def read(self) -> dict[str, dict[str, float | None] | None]:
        """Return the fixed fleet reading."""
        return {'van-nvr': {'hdd_ok': 1}, 'van-cam-front': None}


def test_run_fleet_publisher_per_stream_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each stream gets its own session; a None reading drops only that node."""
    stubs: dict[str, StubClient] = {}

    @contextlib.contextmanager
    def fake_session(
        node: str, sensor_type: str, *, started_msg: str | None = None, announce_online: bool = True
    ) -> Iterator[tuple[StubClient, str, str]]:
        stub = stubs.setdefault(node, StubClient())
        yield stub, f'sensors/{node}/{sensor_type}', f'sensors/{node}/{sensor_type}/status'

    monkeypatch.setattr(runner, 'publisher_session', fake_session)
    run_fleet_publisher(
        FleetOfTwo(),
        streams={'van-nvr': 'nvr', 'van-cam-front': 'camera'},
        interval=0.0,
        once=True,
    )

    assert set(stubs) == {'van-nvr', 'van-cam-front'}  # a session per stream regardless
    assert len(stubs['van-cam-front'].published) == 0  # dropped node published nothing
    topic, raw = stubs['van-nvr'].published[0]
    assert topic == 'sensors/van-nvr/nvr'
    payload = json.loads(raw)
    assert set(payload) == {'ts', 'hdd_ok'}
    assert payload['hdd_ok'] == 1


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


def test_run_gated_publisher_flips_status_on_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freshness owns the flag: seed offline, up on a fresh read, back down when it dries.

    The shared status-gated loop the Victron + fridge readers drive through their
    ``read_snapshot`` adapters; each reader's freshness→snapshot mapping is pinned in
    its own test.
    """
    stub = StubClient()

    @contextlib.contextmanager
    def fake_session(
        node: str, sensor_type: str, *, started_msg: str | None = None, announce_online: bool = True
    ) -> Iterator[tuple[StubClient, str, str]]:
        yield stub, f'sensors/{node}/{sensor_type}', f'sensors/{node}/{sensor_type}/status'

    monkeypatch.setattr(runner, 'publisher_session', fake_session)

    reads = iter(['{"ts": 1, "v": 2}', None])

    def read() -> str | None:
        try:
            return next(reads)
        except StopIteration:
            raise KeyboardInterrupt from None  # break the loop after the scripted cycles

    run_gated_publisher(
        node='house',
        sensor_type='victron',
        interval=0.0,
        once=False,
        stale_label='stale',
        read=read,
    )

    statuses = [payload for topic, payload in stub.published if topic.endswith('/status')]
    assert statuses == ['offline', 'online', 'offline']  # seed, up on fresh, down on the dry read
    readings = [payload for topic, payload in stub.published if topic == 'sensors/house/victron']
    assert readings == ['{"ts": 1, "v": 2}']  # only the fresh cycle published
