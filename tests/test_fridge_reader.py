"""Tests for the fridge reader's poll policy (``sensors/fridge_reader.py``).

The load-bearing contract: the reader's snapshot columns stay in lockstep with the
shared ``fridge`` schema, a poll session fills the snapshot from whatever the
fridge published (NULLing unanswered topics), and the poll→publish loop owns the
retained status flag. Wire framing/codec tests live in ``tests/test_ddmp.py``.
"""

from __future__ import annotations

import json
import socket
from typing import Any

import pytest

from api.sensor_schema import READING_TABLES
from common.ddmp import ACK, PUBLISH, TOPICS, encode_frame
from sensors.fridge_reader import (
    FRIDGE_COLUMNS,
    FakeFridge,
    FridgeSensor,
    build_snapshot,
    publish_loop,
)


class FakeSocket:
    """Scripted socket: each ``recv`` pops one canned chunk, then times out.

    Duplicated from ``tests/test_ddmp.py`` — test modules stay import-independent
    (a cross-import maps the module under two names and breaks mypy).
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = list(chunks)
        self.sent: list[bytes] = []

    def settimeout(self, timeout: float) -> None:
        """Accept the per-recv timeout (unused by the fake)."""

    def sendall(self, data: bytes) -> None:
        """Record one send."""
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        """Pop the next canned chunk; raise like a quiet socket when empty."""
        if not self.chunks:
            raise TimeoutError
        return self.chunks.pop(0)

    def close(self) -> None:
        """Accept the close."""


class StubClient:
    """Captures publishes so ``publish_loop`` can be driven without a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        """Record one publish."""
        self.published.append((topic, payload))


def test_columns_match_schema() -> None:
    """The snapshot column set is exactly the shared ``fridge`` schema's metrics."""
    assert FRIDGE_COLUMNS == READING_TABLES['fridge']['metrics']


def test_columns_unique() -> None:
    """No topic param appears twice (two columns would race on one publish)."""
    params = [tuple(param) for param, _kind in TOPICS.values()]
    assert len(params) == len(set(params))


def _session_chunk(published: dict[str, list[int]]) -> bytes:
    """Script one poll session: publishes for ``published``, then ACKs for every send.

    The reader sends 1 PING + one SUBSCRIBE per column; every incoming frame is
    handled wherever it lands in the stream, so a single chunk answering
    everything up front is a valid fridge.
    """
    chunk = b''
    for column, value_bytes in published.items():
        chunk += encode_frame([PUBLISH, *TOPICS[column][0], *value_bytes])
    chunk += encode_frame([ACK]) * (1 + len(FRIDGE_COLUMNS))
    return chunk


def test_poll_fills_answered_and_nulls_unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Published topics decode into the snapshot; silent ones stay None."""
    chunk = _session_chunk({'comp0_temp_c': [20, 0], 'comp1_set_c': [0x6A, 0xFF]})
    fake = FakeSocket([chunk])
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: fake)

    reading = FridgeSensor('fridge', 1).read()

    assert reading is not None
    assert reading['comp0_temp_c'] == 2.0
    assert reading['comp1_set_c'] == -15.0
    assert reading['comp0_door_open'] is None


def test_read_returns_none_when_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A connect failure is a None reading (the loop flips the stream offline)."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError('connection refused')

    monkeypatch.setattr(socket, 'create_connection', refuse)
    assert FridgeSensor('fridge', 1).read() is None


def test_read_returns_none_when_nothing_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fridge that ACKs but publishes nothing is as offline as a dead TCP port."""
    fake = FakeSocket([_session_chunk({})])
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: fake)
    assert FridgeSensor('fridge', 1).read() is None


def test_fake_fridge_yields_every_column() -> None:
    """The synthetic source returns a numeric value for every column."""
    reading = FakeFridge().read()
    assert reading is not None
    assert set(reading) == set(FRIDGE_COLUMNS)
    assert all(isinstance(value, int | float) for value in reading.values())


def test_build_snapshot_has_ts_and_all_columns() -> None:
    """The payload carries a timestamp plus every column (None when unanswered)."""
    snapshot = build_snapshot({'comp0_temp_c': 2.0})
    assert set(snapshot) == {'ts', *FRIDGE_COLUMNS}
    assert snapshot['comp0_temp_c'] == 2.0
    assert snapshot['comp1_temp_c'] is None


def test_publish_loop_emits_full_snapshot() -> None:
    """One reachable poll publishes a complete snapshot: ts + every column."""
    client = StubClient()
    publish_loop(
        FakeFridge(),
        client,
        'sensors/van/fridge',
        'sensors/van/fridge/status',
        once=True,
    )

    readings = [payload for topic, payload in client.published if topic == 'sensors/van/fridge']
    assert len(readings) == 1
    payload = json.loads(readings[0])
    assert set(payload) == {'ts', *FRIDGE_COLUMNS}
    assert all(isinstance(payload[col], int | float) for col in FRIDGE_COLUMNS)


def test_publish_loop_flips_status_online_when_reachable() -> None:
    """A successful poll transitions the retained status from offline to online."""
    client = StubClient()
    publish_loop(
        FakeFridge(),
        client,
        'sensors/van/fridge',
        'sensors/van/fridge/status',
        once=True,
    )

    statuses = [payload for topic, payload in client.published if topic.endswith('/status')]
    assert statuses == ['offline', 'online']


class UnreachableFridge:
    """A source whose polls always fail (fridge off the LAN)."""

    def read(self) -> None:
        """Report the fridge unreachable."""
        return None


def test_publish_loop_stays_offline_when_unreachable() -> None:
    """A failed poll publishes no reading and leaves the status offline."""
    client = StubClient()
    publish_loop(
        UnreachableFridge(),
        client,
        'sensors/van/fridge',
        'sensors/van/fridge/status',
        once=True,
    )

    assert [p for t, p in client.published if t == 'sensors/van/fridge'] == []
    assert [p for t, p in client.published if t.endswith('/status')] == ['offline']
