"""Pi-side fridge reader: Dometic CFX3 DDMP over WiFi → MQTT publisher.

Bridges the van's Dometic CFX3 75DZ fridge into the sensor platform. The fridge's
WiFi module speaks **DDMP** — Dometic's app protocol, reverse engineered by the
community (keshavdv/dometic-cfx3 is the wire-format reference) — as a TCP server on
port 13142: CR-delimited JSON frames of integer arrays, ``[action, p0, p1, p2, p3,
*value_bytes]``, with a subscribe→publish→ack flow. This reader polls a snapshot and
publishes it to ``sensors/<node>/fridge`` on the Pi broker, where ``mqtt-ingest``
writes it into ``fridge_readings`` — see ``mqttbus/ingest.py`` and
``api/sensor_schema.py``.

Poll, don't hold: the fridge serves one app client at a time, so each cycle is a
short connect→subscribe→collect→disconnect and the phone app keeps working between
polls. Like the Victron reader, this one owns its retained ``status`` flag
(``announce_online=False``): a failed poll (fridge WiFi off, out of range) flips the
stream ``offline`` rather than leaving it online with no data.

The topic table below carries only the subscriptions this reader stores. The DDMP
error topics (NTC/compressor/fan) are deliberately absent: the reference repo's
params for them are self-described mock-broker values, so subscribing would be
guesswork. The four alert topics are real.

Run::

    uv run sensors/fridge_reader.py --fake --node van   # synthetic fridge, real sink
    uv run sensors/fridge_reader.py --node van          # real fridge (CFX_HOST)
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from typing import Protocol

from common.timefmt import now_canonical
from sensors.runner import (
    Heartbeat,
    Reading,
    SimpleSensor,
    add_publisher_args,
    bounded_walk,
    publisher_session,
)

SENSOR_TYPE = 'fridge'

# Fridge temps are slow-moving and every poll briefly claims the fridge's single
# app slot, so poll sparsely.
PUBLISH_INTERVAL_S = 60.0
CONNECT_TIMEOUT_S = 5.0
# Per-recv socket timeout while collecting publishes; the fridge answers a
# subscribe within tens of ms on the LAN, so a quiet second means it is done.
RECV_TIMEOUT_S = 2.0
# Whole-poll budget: a wedged exchange abandons the cycle rather than stalling
# the loop (the next cycle reconnects fresh).
POLL_DEADLINE_S = 15.0

DDMP_PORT = 13142

# DDMP action codes (frame byte 0).
PUBLISH, SUBSCRIBE, PING, HELLO, ACK, NAK, NOP = 0, 1, 2, 3, 4, 5, 6

#: column → (topic param bytes, wire type). The param is DDMP's 4-byte topic id;
#: the wire type picks the ``decode_value`` branch. Column order is also the
#: snapshot/display order, pinned to the shared ``fridge`` schema by a test.
TOPICS: dict[str, tuple[list[int], str]] = {
    'comp0_temp_c': ([0, 1, 1, 1], 'ddegC'),
    'comp1_temp_c': ([16, 1, 1, 1], 'ddegC'),
    'comp0_set_c': ([0, 2, 1, 1], 'ddegC'),
    'comp1_set_c': ([16, 2, 1, 1], 'ddegC'),
    'comp0_door_open': ([0, 8, 1, 1], 'bool'),
    'comp1_door_open': ([16, 8, 1, 1], 'bool'),
    'comp0_power': ([0, 0, 1, 1], 'bool'),
    'comp1_power': ([16, 0, 1, 1], 'bool'),
    'cooler_power': ([0, 0, 3, 1], 'bool'),
    'power_source': ([0, 5, 3, 1], 'u8'),
    'input_voltage_v': ([0, 1, 3, 1], 'dV'),
    'battery_protection': ([0, 2, 3, 1], 'u8'),
    'temp_alert_cc': ([0, 0, 5, 1], 'bool'),
    'temp_alert_dcm': ([0, 3, 5, 1], 'bool'),
    'door_alert': ([0, 1, 5, 1], 'bool'),
    'voltage_alert': ([0, 2, 5, 1], 'bool'),
}

FRIDGE_COLUMNS: list[str] = list(TOPICS)

#: topic param (as a tuple) → column, for routing incoming publishes.
_PARAM_TO_COLUMN: dict[tuple[int, ...], str] = {
    tuple(param): column for column, (param, _kind) in TOPICS.items()
}


def fridge_host() -> str:
    """Return the fridge IP from ``CFX_HOST`` (default its van-edge DHCP lease)."""
    return os.environ.get('CFX_HOST', '192.168.42.185')


def fridge_port() -> int:
    """Return the fridge DDMP port from ``CFX_PORT`` (default 13142)."""
    return int(os.environ.get('CFX_PORT', str(DDMP_PORT)))


class _Sender(Protocol):
    """The slice of a socket the frame handler sends ACKs through (stubbed in tests)."""

    def sendall(self, data: bytes, /) -> None:
        """Send all of ``data``."""


def encode_frame(data: list[int]) -> bytes:
    """Encode one DDMP frame: the JSON envelope plus the CR delimiter.

    Args:
        data: The frame ints — ``[action, *topic_param, *value_bytes]``.

    Returns:
        The wire bytes.
    """
    return (json.dumps({'ddmp': data}) + '\r').encode()


def split_frames(buf: bytes) -> tuple[list[list[int]], bytes]:
    """Split a receive buffer into complete DDMP frames plus the unconsumed tail.

    Args:
        buf: Bytes received so far (may end mid-frame).

    Returns:
        ``(frames, rest)`` — each frame as its ``ddmp`` int list; ``rest`` is the
        trailing partial frame to prepend to the next recv. A malformed line is
        skipped rather than raised: one garbled frame shouldn't abort a poll.
    """
    frames: list[list[int]] = []
    while b'\r' in buf:
        line, buf = buf.split(b'\r', 1)
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            frame = payload['ddmp']
        except (ValueError, KeyError, TypeError):
            continue
        if isinstance(frame, list) and frame and all(isinstance(b, int) for b in frame):
            frames.append(frame)
    return frames, buf


def decode_value(kind: str, data: list[int]) -> float | int | None:
    """Decode a published value's bytes per its topic's wire type.

    Args:
        kind: The wire type from :data:`TOPICS` — ``ddegC`` (int16-LE decidegrees
            Celsius), ``dV`` (uint16-LE decivolts), ``bool`` (stored as 0/1 int),
            or ``u8``.
        data: The value bytes (JSON ints) after the 4-byte topic param.

    Returns:
        The decoded number, or None if the bytes are too short for the type.
    """
    try:
        if kind == 'ddegC':
            raw = (data[1] << 8) | data[0]
            if raw >= 0x8000:
                raw -= 0x10000
            return raw / 10
        if kind == 'dV':
            return ((data[1] << 8) | data[0]) / 10
        if kind in ('bool', 'u8'):
            return int(data[0])
    except IndexError:
        return None
    return None


class FridgeSensor:
    """Polls one snapshot from the fridge's DDMP server per :meth:`read` call.

    Each poll is a fresh TCP session: PING, then per column a SUBSCRIBE
    acknowledged by the fridge, collecting the PUBLISH frames (each ACKed back)
    that carry the values. Unanswered topics come back None — their columns store
    NULL for that cycle rather than failing the poll.
    """

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port

    def read(self) -> Reading | None:
        """Return one snapshot (column → value), or None if the fridge is unreachable.

        Returns:
            Every :data:`FRIDGE_COLUMNS` key (None for topics the fridge didn't
            answer), or None when the connection failed or no topic answered —
            a fridge that ACKs but publishes nothing is as offline as a dead TCP port.
        """
        try:
            values = self._poll()
        except OSError:
            return None
        if all(value is None for value in values.values()):
            return None
        return values

    def _poll(self) -> dict[str, float | int | None]:
        """Run one connect→subscribe→collect→disconnect DDMP session."""
        values: dict[str, float | int | None] = {column: None for column in FRIDGE_COLUMNS}
        deadline = time.monotonic() + POLL_DEADLINE_S
        with socket.create_connection((self._host, self._port), timeout=CONNECT_TIMEOUT_S) as sock:
            sock.settimeout(RECV_TIMEOUT_S)
            buf = b''
            sock.sendall(encode_frame([PING]))
            for column in FRIDGE_COLUMNS:
                if time.monotonic() > deadline:
                    break
                param, _kind = TOPICS[column]
                sock.sendall(encode_frame([SUBSCRIBE, *param]))
                buf = self._collect(sock, buf, values, deadline, until_ack=True)
            # Drain briefly: publishes for the last subscribes may still be in flight.
            self._collect(sock, buf, values, min(deadline, time.monotonic() + RECV_TIMEOUT_S))
        return values

    def _collect(
        self,
        sock: socket.socket,
        buf: bytes,
        values: dict[str, float | int | None],
        deadline: float,
        *,
        until_ack: bool = False,
    ) -> bytes:
        """Receive and handle frames until ACK/NAK (if asked), timeout, or deadline.

        Args:
            sock: The connected DDMP socket.
            buf: Unconsumed tail bytes from the previous collect.
            values: The snapshot being filled; publishes decode into it.
            deadline: Monotonic cutoff for this collect.
            until_ack: Return as soon as an ACK or NAK arrives (the fridge's answer
                to our just-sent SUBSCRIBE; publishes interleave and are handled).

        Returns:
            The new unconsumed tail.
        """
        while time.monotonic() < deadline:
            try:
                chunk = sock.recv(4096)
            except TimeoutError:
                return buf
            if not chunk:
                return buf
            buf += chunk
            frames, buf = split_frames(buf)
            for frame in frames:
                acked = self._handle(sock, frame, values)
                if until_ack and acked:
                    return buf
        return buf

    def _handle(
        self, sock: _Sender, frame: list[int], values: dict[str, float | int | None]
    ) -> bool:
        """Handle one incoming frame; return whether it was an ACK/NAK.

        PINGs and PUBLISHes are ACKed back (the fridge retries unACKed publishes and
        eventually drops the client). A publish for an unknown topic param is ACKed
        and ignored.
        """
        action = frame[0]
        if action == PING:
            sock.sendall(encode_frame([ACK]))
            return False
        if action == PUBLISH:
            sock.sendall(encode_frame([ACK]))
            column = _PARAM_TO_COLUMN.get(tuple(frame[1:5]))
            if column is not None and len(frame) > 5:
                values[column] = decode_value(TOPICS[column][1], frame[5:])
            return False
        return action in (ACK, NAK)


class FakeFridge:
    """Synthetic fridge for desk-testing the MQTT → ``fridge_readings`` path.

    Drifts the continuous channels on a bounded random walk and holds the
    enum/bool channels at plausible fixed values, so the pipeline can be exercised
    with no fridge on the LAN (the real sink still publishes).
    """

    _WALKING: dict[str, float] = {
        'comp0_temp_c': 2.0,
        'comp1_temp_c': -14.0,
        'input_voltage_v': 26.6,
    }
    _FIXED: dict[str, float | int] = {
        'comp0_set_c': 1.0,
        'comp1_set_c': -15.0,
        'comp0_door_open': 0,
        'comp1_door_open': 0,
        'comp0_power': 1,
        'comp1_power': 1,
        'cooler_power': 1,
        'power_source': 1,
        'battery_protection': 1,
        'temp_alert_cc': 0,
        'temp_alert_dcm': 0,
        'door_alert': 0,
        'voltage_alert': 0,
    }

    def __init__(self) -> None:
        self._values = dict(self._WALKING)

    def read(self) -> Reading | None:
        """Return a full synthetic snapshot (always fresh)."""
        snapshot: dict[str, float | int | None] = dict(self._FIXED)
        snapshot.update(bounded_walk(self._values, self._WALKING, scale=0.02))
        return snapshot


def build_snapshot(reading: Reading) -> dict[str, object]:
    """Build the publish payload: a timestamp plus every column from the reading.

    Args:
        reading: The polled values (columns the fridge didn't answer may be absent).

    Returns:
        ``{'ts': <ms-UTC>, <column>: <value-or-None>, …}`` over all columns.
    """
    return {'ts': now_canonical(), **{col: reading.get(col) for col in FRIDGE_COLUMNS}}


def publish_loop(
    sensor: SimpleSensor,
    client: object,
    reading_topic: str,
    status_topic: str,
    once: bool,
) -> None:
    """Poll→publish one snapshot per ``PUBLISH_INTERVAL_S``.

    Owns the retained ``status`` flag: a successful poll publishes the snapshot and
    flips ``online``; a failed one flips ``offline`` (the fridge's WiFi is off or out
    of range) rather than republishing stale values.

    Args:
        sensor: The fridge (or fake) source.
        client: The connected paho sink client (Pi broker).
        reading_topic: ``sensors/<node>/fridge`` to publish snapshots to.
        status_topic: ``sensors/<node>/fridge/status`` for the retained online flag.
        once: Poll and publish a single cycle, then return — for testing.
    """
    hb = Heartbeat()
    online = False
    client.publish(status_topic, 'offline', qos=1, retain=True)  # type: ignore[attr-defined]

    while True:
        reading = sensor.read()
        fresh = reading is not None
        if fresh != online:
            payload = 'online' if fresh else 'offline'
            client.publish(status_topic, payload, qos=1, retain=True)  # type: ignore[attr-defined]
            online = fresh

        if reading is not None:
            snapshot = json.dumps(build_snapshot(reading))
            client.publish(reading_topic, snapshot, qos=1, retain=True)  # type: ignore[attr-defined]
            hb.bump('published')
        else:
            hb.bump('unreachable')

        hb.maybe_emit(online=online)
        if once:
            return
        time.sleep(PUBLISH_INTERVAL_S)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list, or None to read from ``sys.argv``.

    Returns:
        The parsed argument namespace.
    """
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    add_publisher_args(
        parser,
        node_default='van',
        fake_help='Publish synthetic readings instead of polling a real fridge.',
        once_help='Poll and publish a single snapshot, then exit (for testing).',
    )
    return parser.parse_args(argv)


def main() -> int:
    """Connect the sink broker and run the poll→publish loop.

    Returns:
        Process exit code: 0 on graceful shutdown.
    """
    args = parse_args()
    sensor: FridgeSensor | FakeFridge = (
        FakeFridge() if args.fake else FridgeSensor(fridge_host(), fridge_port())
    )
    started = f'Fridge reader started (node={args.node}, fake={args.fake})'
    with publisher_session(args.node, SENSOR_TYPE, started_msg=started, announce_online=False) as (
        client,
        reading_topic,
        status_topic,
    ):
        try:
            publish_loop(sensor, client, reading_topic, status_topic, args.once)
        except KeyboardInterrupt:
            print('Fridge reader stopped', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
