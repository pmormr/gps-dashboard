"""Tests for the fridge reader's poll policy (``sensors/fridge_reader.py``).

The load-bearing contract: the reader's snapshot columns stay in lockstep with the
shared ``fridge`` schema, a poll session fills the snapshot from whatever the
fridge published (NULLing unanswered topics), history arrays flatten onto a stable
bucket grid, and the poll→publish loop owns the retained status flag. Wire
framing/codec tests live in ``tests/test_ddmp.py``.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime
from typing import Any

import pytest

from api.sensor_schema import READING_TABLES
from common.ddmp import ACK, HISTORY_PARAMS, PUBLISH, TOPICS, encode_frame
from sensors.fridge_reader import (
    FRIDGE_COLUMNS,
    FakeFridge,
    FridgePoll,
    FridgeSensor,
    build_snapshot,
    flatten_history,
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


def _session_chunk(
    published: dict[str, list[int]], history: dict[str, list[int]] | None = None
) -> bytes:
    """Script one poll session: publishes up front, then ACKs for every send.

    The reader sends 1 PING + one SUBSCRIBE per scalar column + one per due
    history span; every incoming frame is handled wherever it lands in the
    stream, so a single chunk answering everything up front is a valid fridge.
    """
    chunk = b''
    for column, value_bytes in published.items():
        chunk += encode_frame([PUBLISH, *TOPICS[column][0], *value_bytes])
    for span, value_bytes in (history or {}).items():
        chunk += encode_frame([PUBLISH, *HISTORY_PARAMS[span], *value_bytes])
    chunk += encode_frame([ACK]) * (1 + len(TOPICS) + len(HISTORY_PARAMS))
    return chunk


def test_poll_fills_answered_and_nulls_unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Published topics decode into the snapshot; silent ones stay None."""
    chunk = _session_chunk({'comp0_temp_c': [20, 0], 'comp1_set_c': [0x6A, 0xFF]})
    fake = FakeSocket([chunk])
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: fake)

    poll = FridgeSensor('fridge', 1).read()

    assert poll is not None
    assert poll.values['comp0_temp_c'] == 2.0
    assert poll.values['comp1_set_c'] == -15.0
    assert poll.values['comp0_door_open'] is None
    assert poll.history == []


def test_poll_flattens_history_and_lifts_dc_draw(monkeypatch: pytest.MonkeyPatch) -> None:
    """A history publish becomes bucket rows; the hour front bucket → dc_current_a."""
    hour_frame = [15, 0, 0, 0, 11, 0, 10, 0, 0, 0, 10, 0, 9, 0, 25]
    chunk = _session_chunk({'comp0_temp_c': [20, 0]}, {'hour': hour_frame})
    fake = FakeSocket([chunk])
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: fake)

    poll = FridgeSensor('fridge', 1).read()

    assert poll is not None
    assert poll.values['dc_current_a'] == 1.5
    hour_rows = [row for row in poll.history if row['span'] == 'hour']
    assert len(hour_rows) == 7
    assert hour_rows[0]['dc_current_a'] == 1.5
    assert hour_rows[6]['dc_current_a'] == 0.9


def _starts(rows: list[dict[str, object]]) -> list[float]:
    return [
        datetime.fromisoformat(str(row['bucket_ts']).replace('Z', '+00:00')).timestamp()
        for row in rows
    ]


def test_flatten_history_anchors_on_tail() -> None:
    """Bucket starts derive from the tick counter: newest first, one width apart,
    snapped to the quarter-width grid."""
    poll_epoch = 1_784_134_836.0
    # tail 128 = half a bucket elapsed → the newest bucket started ~300 s ago.
    rows = flatten_history('hour', [1.5, 0.0, 1.1], 128, poll_epoch)

    starts = _starts(rows)
    assert starts[0] % 150 == 0  # width/4 grid
    assert abs(poll_epoch - 300 - starts[0]) <= 75  # within half a grid cell
    assert [starts[0] - s for s in starts] == [0, 600, 1200]
    assert [row['dc_current_a'] for row in rows] == [1.5, 0.0, 1.1]
    assert all(row['span'] == 'hour' for row in rows)


def test_flatten_history_is_stable_within_a_bucket() -> None:
    """Two polls inside one fridge bucket key the same rows (UPSERT convergence).

    120 s later the tick counter has advanced ~51 ticks, so poll_time − tail·tick
    stays put and the snap keeps the key identical while the value converges.
    """
    early = flatten_history('hour', [1.0], 100, 1_784_134_836.0)
    late = flatten_history('hour', [1.2], 151, 1_784_134_956.0)
    assert early[0]['bucket_ts'] == late[0]['bucket_ts']


def test_flatten_history_keeps_keys_across_a_roll() -> None:
    """After a roll, the finished bucket (now index 1) keeps its pre-roll key."""
    pre = flatten_history('hour', [1.0, 0.5], 250, 1_784_134_836.0)
    # 30 s later the bucket rolled: tail wrapped, values shifted right.
    post = flatten_history('hour', [0.2, 1.0, 0.5], 4, 1_784_134_866.0)
    assert post[1]['bucket_ts'] == pre[0]['bucket_ts']
    assert _starts(post)[0] - _starts(pre)[0] == 600


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


def test_fake_fridge_yields_every_column_and_history() -> None:
    """The synthetic source returns numbers for every column plus history rows."""
    poll = FakeFridge().read()
    assert poll is not None
    assert set(poll.values) == set(FRIDGE_COLUMNS)
    assert all(isinstance(value, int | float) for value in poll.values.values())
    assert {row['span'] for row in poll.history} == {'hour', 'day', 'week'}


def test_build_snapshot_has_ts_and_all_columns() -> None:
    """The payload carries a timestamp plus every column (None when unanswered)."""
    snapshot = build_snapshot(FridgePoll({'comp0_temp_c': 2.0}))
    assert set(snapshot) == {'ts', *FRIDGE_COLUMNS}
    assert snapshot['comp0_temp_c'] == 2.0
    assert snapshot['comp1_temp_c'] is None


def test_build_snapshot_carries_history_when_present() -> None:
    """History rows ride the payload only when the poll produced any."""
    rows = flatten_history('hour', [1.0], 40, 1_784_134_836.0)
    snapshot = build_snapshot(FridgePoll({}, rows))
    assert snapshot['history'] == rows


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
    assert set(payload) == {'ts', 'history', *FRIDGE_COLUMNS}
    assert all(isinstance(payload[col], int | float) for col in FRIDGE_COLUMNS)
    assert all(row.keys() == {'span', 'bucket_ts', 'dc_current_a'} for row in payload['history'])


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
