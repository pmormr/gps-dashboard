"""Tests for the shared DDMP protocol core (``common/ddmp.py``).

The load-bearing contracts: framing round-trips, value decoding handles the wire
types (including negative decidegree temperatures — a freezer zone lives below
zero), the write encoders match the vendor app's observed byte layout, and the
session client keeps the ACK discipline (unACKed publishes get the client
dropped by the real fridge) while mapping NAK/silence onto ``DdmpError.refused``.
"""

from __future__ import annotations

import socket
import time
from typing import Any

import pytest

from common.ddmp import (
    ACK,
    NAK,
    NOP,
    PING,
    PUBLISH,
    SUBSCRIBE,
    TOPICS,
    DdmpClient,
    DdmpError,
    decode_history,
    decode_int16_array,
    decode_value,
    encode_bool,
    encode_ddegc,
    encode_frame,
    setpoint_param,
    split_frames,
    zone_power_param,
)


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


def test_decode_int16_array_ranges() -> None:
    """The range topics decode as [min, max] °C — the probed −22..10 allowed range."""
    assert decode_int16_array([36, 255, 100, 0]) == [-22.0, 10.0]
    assert decode_int16_array([106, 255, 40, 0]) == [-15.0, 4.0]
    assert decode_int16_array([]) == []


def test_decode_history_probed_frame() -> None:
    """A real probed 15-byte history frame: 7 deci-amp buckets + the tail byte."""
    raw = [15, 0, 0, 0, 11, 0, 10, 0, 0, 0, 10, 0, 9, 0, 25]
    values, tail = decode_history(raw)
    assert values == [1.5, 0.0, 1.1, 1.0, 0.0, 1.0, 0.9]
    assert tail == 25


def test_decode_history_even_and_short_frames() -> None:
    """An even-length frame has no tail; short frames decode what's there."""
    assert decode_history([20, 0, 30, 0]) == ([2.0, 3.0], None)
    assert decode_history([7]) == ([], 7)
    assert decode_history([]) == ([], None)


def test_encode_ddegc_roundtrip_and_ceil() -> None:
    """Setpoint encoding is int16-LE ceil(deci): matches the vendor app's rounding."""
    assert encode_ddegc(4.0) == [40, 0]
    assert encode_ddegc(-15.0) == [0x6A, 0xFF]
    # ceil on the deci-value: -15.55 °C → ceil(-155.5) = -155 = 0xFF65.
    assert encode_ddegc(-15.55) == [0x65, 0xFF]
    for temp in (4.0, -15.0, 0.0, -22.0, 10.0):
        assert decode_value('ddegC', encode_ddegc(temp)) == temp


def test_encode_bool() -> None:
    """Compartment power writes are a single 0/1 byte."""
    assert encode_bool(True) == [1]
    assert encode_bool(False) == [0]


def test_zone_param_helpers() -> None:
    """Zone 1 params are zone 0's with p0=16; zone 0 matches the TOPICS registry."""
    assert setpoint_param(0) == TOPICS['comp0_set_c'][0]
    assert setpoint_param(1) == TOPICS['comp1_set_c'][0]
    assert zone_power_param(0) == TOPICS['comp0_power'][0]
    assert zone_power_param(1) == TOPICS['comp1_power'][0]


class FakeSocket:
    """Scripted socket: each ``recv`` pops one canned chunk, then times out."""

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


def client_with(
    monkeypatch: pytest.MonkeyPatch, chunks: list[bytes]
) -> tuple[DdmpClient, FakeSocket]:
    """Build an entered DdmpClient wired to a scripted FakeSocket."""
    fake = FakeSocket(chunks)
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: fake)
    client = DdmpClient('fridge', 1).__enter__()
    return client, fake


def test_client_connect_failure_is_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead TCP port surfaces as DdmpError with refused=False (route → 503)."""

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise OSError('connection refused')

    monkeypatch.setattr(socket, 'create_connection', refuse)
    with pytest.raises(DdmpError) as exc_info:
        DdmpClient('fridge', 1).__enter__()
    assert exc_info.value.refused is False


def test_client_subscribe_returns_frames_and_acks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Subscribe collects the topic's publish (ACKing it back) whether it lands
    before or after the fridge's subscribe-ACK; NOPs are ignored silently."""
    param = TOPICS['comp0_temp_c'][0]
    client, fake = client_with(
        monkeypatch,
        [encode_frame([NOP]) + encode_frame([PUBLISH, *param, 20, 0]) + encode_frame([ACK])],
    )
    frames = client.subscribe(param)
    assert frames == [[20, 0]]
    assert fake.sent == [encode_frame([SUBSCRIBE, *param]), encode_frame([ACK])]


def test_client_subscribe_drains_for_late_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    """A publish arriving after the subscribe-ACK is picked up by the drain."""
    param = TOPICS['comp0_temp_c'][0]
    client, _fake = client_with(
        monkeypatch,
        [encode_frame([ACK]), encode_frame([PUBLISH, *param, 20, 0])],
    )
    assert client.subscribe(param) == [[20, 0]]


def test_client_subscribe_nak_and_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A NAKed subscribe raises refused=True; silence raises refused=False."""
    param = TOPICS['comp0_temp_c'][0]
    client, _fake = client_with(monkeypatch, [encode_frame([NAK])])
    with pytest.raises(DdmpError) as exc_info:
        client.subscribe(param)
    assert exc_info.value.refused is True

    client, _fake = client_with(monkeypatch, [])
    with pytest.raises(DdmpError) as exc_info:
        client.subscribe(param)
    assert exc_info.value.refused is False


def test_client_write_ack_nak_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    """A write requires an ACK: NAK → refused=True, silence → refused=False."""
    param = setpoint_param(0)
    client, fake = client_with(monkeypatch, [encode_frame([ACK])])
    client.write(param, encode_ddegc(4.0))
    assert fake.sent == [encode_frame([PUBLISH, *param, 40, 0])]

    client, _fake = client_with(monkeypatch, [encode_frame([NAK])])
    with pytest.raises(DdmpError) as exc_info:
        client.write(param, encode_ddegc(4.0))
    assert exc_info.value.refused is True

    client, _fake = client_with(monkeypatch, [])
    with pytest.raises(DdmpError) as exc_info:
        client.write(param, encode_ddegc(4.0))
    assert exc_info.value.refused is False


def test_client_acks_pings_and_attributes_by_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """PINGs are ACKed; interleaved publishes for other topics still accumulate."""
    want = TOPICS['comp0_temp_c'][0]
    other = TOPICS['comp1_temp_c'][0]
    client, fake = client_with(
        monkeypatch,
        [
            encode_frame([PING])
            + encode_frame([PUBLISH, *other, 0x6A, 0xFF])
            + encode_frame([PUBLISH, *want, 20, 0])
            + encode_frame([ACK])
        ],
    )
    assert client.subscribe(want) == [[20, 0]]
    assert client.frames_for(other) == [[0x6A, 0xFF]]
    # Three ACKs went back: the PING and both publishes.
    assert fake.sent.count(encode_frame([ACK])) == 3


def test_client_expired_after_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deadline property flips once the session budget is spent."""
    client, _fake = client_with(monkeypatch, [])
    assert client.expired is False
    monkeypatch.setattr(time, 'monotonic', lambda: 1e12)
    assert client.expired is True
