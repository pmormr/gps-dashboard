"""Tests for the fridge reader's pure logic (``sensors/fridge_reader.py``).

The load-bearing contract: the reader's snapshot columns stay in lockstep with the
shared ``fridge`` schema, DDMP framing round-trips, and value decoding handles the
wire types (including negative decidegree temperatures — a freezer zone lives below
zero). Also covers the synthetic source and the poll→publish loop's status ownership.
"""

from __future__ import annotations

import json

from api.sensor_schema import READING_TABLES
from sensors.fridge_reader import (
    ACK,
    FRIDGE_COLUMNS,
    NAK,
    PING,
    PUBLISH,
    SUBSCRIBE,
    TOPICS,
    FakeFridge,
    FridgeSensor,
    build_snapshot,
    decode_value,
    encode_frame,
    publish_loop,
    split_frames,
)


class StubClient:
    """Captures publishes so ``publish_loop`` can be driven without a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        """Record one publish."""
        self.published.append((topic, payload))


class StubSocket:
    """Captures ``sendall`` frames so ``_handle`` can be driven without a socket."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        """Record one send."""
        self.sent.append(data)


def test_columns_match_schema() -> None:
    """The snapshot column set is exactly the shared ``fridge`` schema's metrics."""
    assert FRIDGE_COLUMNS == READING_TABLES['fridge']['metrics']


def test_columns_unique() -> None:
    """No topic param appears twice (two columns would race on one publish)."""
    params = [tuple(param) for param, _kind in TOPICS.values()]
    assert len(params) == len(set(params))


def test_encode_frame_wire_format() -> None:
    """A frame is the JSON envelope + CR: what the fridge's parser expects."""
    assert encode_frame([PING]) == b'{"ddmp": [2]}\r'
    assert encode_frame([SUBSCRIBE, 0, 1, 1, 1]) == b'{"ddmp": [1, 0, 1, 1, 1]}\r'


def test_split_frames_roundtrip() -> None:
    """Encoded frames split back out; a trailing partial frame is kept as the tail."""
    buf = encode_frame([ACK]) + encode_frame([PUBLISH, 0, 1, 1, 1, 20, 0]) + b'{"ddm'
    frames, rest = split_frames(buf)
    assert frames == [[ACK], [PUBLISH, 0, 1, 1, 1, 20, 0]]
    assert rest == b'{"ddm'


def test_split_frames_skips_garbage() -> None:
    """A malformed line is dropped without losing the frames around it."""
    buf = b'not json\r' + encode_frame([ACK]) + b'{"other": 1}\r'
    frames, rest = split_frames(buf)
    assert frames == [[ACK]]
    assert rest == b''


def test_decode_value_temperatures() -> None:
    """Decidegree int16-LE decodes, including negative (freezer) values."""
    assert decode_value('ddegC', [20, 0]) == 2.0
    assert decode_value('ddegC', [10, 0]) == 1.0
    # -15.0 °C = -150 ddeg = 0xFF6A little-endian.
    assert decode_value('ddegC', [0x6A, 0xFF]) == -15.0


def test_decode_value_other_types() -> None:
    """Volts scale by ten; bools and u8 pass through as ints; short data is None."""
    assert decode_value('dV', [0x0A, 0x01]) == 26.6
    assert decode_value('bool', [1]) == 1
    assert decode_value('bool', [0]) == 0
    assert decode_value('u8', [2]) == 2
    assert decode_value('ddegC', [5]) is None
    assert decode_value('dV', []) is None


def test_handle_publish_decodes_and_acks() -> None:
    """A publish for a known topic decodes into the snapshot and is ACKed back."""
    sensor = FridgeSensor('unused', 0)
    sock = StubSocket()
    values: dict[str, float | int | None] = {col: None for col in FRIDGE_COLUMNS}

    acked = sensor._handle(sock, [PUBLISH, 0, 1, 1, 1, 20, 0], values)

    assert acked is False
    assert values['comp0_temp_c'] == 2.0
    assert sock.sent == [encode_frame([ACK])]


def test_handle_unknown_publish_acked_and_ignored() -> None:
    """A publish for an unmapped topic param is ACKed but stores nothing."""
    sensor = FridgeSensor('unused', 0)
    sock = StubSocket()
    values: dict[str, float | int | None] = {col: None for col in FRIDGE_COLUMNS}

    sensor._handle(sock, [PUBLISH, 9, 9, 9, 9, 1], values)

    assert all(value is None for value in values.values())
    assert sock.sent == [encode_frame([ACK])]


def test_handle_ping_acks_and_ack_nak_signal() -> None:
    """The fridge's PING gets an ACK; ACK/NAK report as the subscribe answer."""
    sensor = FridgeSensor('unused', 0)
    sock = StubSocket()
    values: dict[str, float | int | None] = {}

    assert sensor._handle(sock, [PING], values) is False
    assert sock.sent == [encode_frame([ACK])]
    assert sensor._handle(sock, [ACK], values) is True
    assert sensor._handle(sock, [NAK], values) is True


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
