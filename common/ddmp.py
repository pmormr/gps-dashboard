"""Dometic DDMP protocol core — framing, topic registry, codecs, and a session client.

DDMP is the CFX3 fridge's app protocol (community-reverse-engineered; wire-format
reference: keshavdv/dometic-cfx3): a TCP server on port 13142 speaking CR-delimited
``{"ddmp": [...]}`` JSON frames of ints — ``[action, p0, p1, p2, p3, *value_bytes]``
— with a subscribe→publish→ack flow. This module is shared by the poll reader
(``sensors/fridge_reader.py``) and the control routes (``api/routes/fridge.py``);
it lives in ``common`` because ``api`` must not import the ``sensors`` package.

Protocol behaviors pinned by probing the real fridge (``tools/cfx3_probe.py``,
transcript in ``reference/cfx3-ddmp.md``): the fridge interleaves NOP frames
(ignored), PINGs the client (must be ACKed), retries unACKed publishes and then
drops the client, and serves **one** app client at a time — every session must be
a short connect→work→disconnect so the phone app keeps working between sessions.
"""

from __future__ import annotations

import json
import math
import os
import socket
import time
import types

DDMP_PORT = 13142

CONNECT_TIMEOUT_S = 5.0
# Per-recv socket timeout while collecting; the fridge answers within tens of ms
# on the LAN, so a quiet this-long means it has nothing more to say.
RECV_TIMEOUT_S = 2.0
# Whole-session budget: a wedged exchange abandons the session rather than
# stalling the caller (the next session reconnects fresh).
SESSION_DEADLINE_S = 15.0

# DDMP action codes (frame byte 0).
PUBLISH, SUBSCRIBE, PING, HELLO, ACK, NAK, NOP = 0, 1, 2, 3, 4, 5, 6

#: column → (topic param bytes, wire type). The param is DDMP's 4-byte topic id;
#: the wire type picks the ``decode_value`` branch. Column order is also the
#: reader's snapshot/display order, pinned to the shared ``fridge`` schema by a test.
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

#: topic param (as a tuple) → column, for routing incoming publishes.
PARAM_TO_COLUMN: dict[tuple[int, ...], str] = {
    tuple(param): column for column, (param, _kind) in TOPICS.items()
}

#: span → DC power-usage history topic param (7 buckets + tail byte per publish).
HISTORY_PARAMS: dict[str, list[int]] = {
    'hour': [0, 64, 3, 1],
    'day': [0, 65, 3, 1],
    'week': [0, 66, 3, 1],
}

#: span → bucket width in seconds, pinned by probing (``reference/cfx3-ddmp.md``):
#: each span publishes 7 buckets, newest (in-progress) first, rolling on the wall
#: boundary. Shared by the reader's flattening and the history read route.
HISTORY_BUCKET_S: dict[str, int] = {'hour': 3600, 'day': 86400, 'week': 604800}

#: presented temperature unit (u8: 0 = °C, 1 = °F) — how the fridge itself displays.
PRESENTED_UNIT_PARAM: list[int] = [0, 0, 2, 1]


def setpoint_param(zone: int) -> list[int]:
    """Return the set-temperature topic param for a compartment (zone 0 or 1)."""
    return [16 * zone, 2, 1, 1]


def zone_power_param(zone: int) -> list[int]:
    """Return the compartment-power topic param for a compartment (zone 0 or 1)."""
    return [16 * zone, 0, 1, 1]


def temp_range_param(zone: int) -> list[int]:
    """Return the allowed-setpoint-range topic param ([min, max] ddegC int16-LE ×2)."""
    return [16 * zone, 128, 1, 1]


def recommended_range_param(zone: int) -> list[int]:
    """Return the recommended-setpoint-range topic param (same layout as the allowed range)."""
    return [16 * zone, 129, 1, 1]


def fridge_host() -> str:
    """Return the fridge IP from ``CFX_HOST`` (default its van-edge DHCP lease)."""
    return os.environ.get('CFX_HOST', '192.168.42.185')


def fridge_port() -> int:
    """Return the fridge DDMP port from ``CFX_PORT`` (default 13142)."""
    return int(os.environ.get('CFX_PORT', str(DDMP_PORT)))


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
        skipped rather than raised: one garbled frame shouldn't abort a session.
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


def decode_int16_array(data: list[int]) -> list[float]:
    """Decode consecutive int16-LE pairs as signed deci-values (÷10).

    The layout of the range topics (``[min, max]``) and the history arrays.

    Args:
        data: Value bytes; a trailing odd byte is left to the caller.

    Returns:
        One float per complete pair.
    """
    out: list[float] = []
    for i in range(0, len(data) - 1, 2):
        raw = (data[i + 1] << 8) | data[i]
        if raw >= 0x8000:
            raw -= 0x10000
        out.append(raw / 10)
    return out


def decode_history(data: list[int]) -> tuple[list[float], int | None]:
    """Decode one history publish: bucket values plus the trailing position byte.

    Probed layout (``reference/cfx3-ddmp.md``): 15 bytes — 7 × int16-LE signed
    deci-values (deci-amps for the DC topics) followed by one tail byte that
    tracks the position inside the newest bucket (span-wide, not per-topic).

    Args:
        data: The value bytes after the 4-byte topic param.

    Returns:
        ``(values, tail)`` — ``tail`` is None when the frame has no odd trailing
        byte. Short/empty frames decode to as many complete pairs as present.
    """
    tail = data[-1] if len(data) % 2 == 1 else None
    body = data[:-1] if tail is not None else data
    return decode_int16_array(body), tail


def encode_ddegc(temp_c: float) -> list[int]:
    """Encode a °C temperature as int16-LE decidegrees (the setpoint write format).

    Uses ``ceil`` on the deci-value to match the vendor app's observed rounding.

    Args:
        temp_c: Temperature in °C (freezer setpoints are negative).

    Returns:
        Two value bytes, little-endian, two's-complement.
    """
    deci = math.ceil(temp_c * 10)
    return [deci & 0xFF, (deci >> 8) & 0xFF]


def encode_bool(on: bool) -> list[int]:
    """Encode an on/off write value (compartment power)."""
    return [1 if on else 0]


class DdmpError(RuntimeError):
    """A DDMP session failed: unreachable fridge, dead session, or a NAKed command.

    Attributes:
        refused: True when the fridge answered NAK (it heard us and said no);
            False for transport failures — connect refused/timeout, the session
            going quiet with no ACK. Mirrors ``RigctldError.rprt``: the control
            routes map ``refused=True`` to 502 and ``False`` to 503.
    """

    def __init__(self, message: str, refused: bool = False) -> None:
        super().__init__(message)
        self.refused = refused


class DdmpClient:
    """One short DDMP TCP session, used as a context manager.

    Owns the receive buffer and the ACK discipline: every incoming PING and
    PUBLISH is ACKed back (the fridge retries unACKed publishes and eventually
    drops the client), NOP/HELLO are ignored, and every PUBLISH's value bytes
    accumulate in :attr:`published` keyed by topic param for the caller to pick
    up — publishes for one subscription can arrive before or after another's
    ACK, so attribution is by param, never by request order.

    All receive loops stop at the session deadline, so a wedged fridge bounds
    the caller's latency; keep sessions short — the fridge serves one client.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        deadline_s: float = SESSION_DEADLINE_S,
    ) -> None:
        """Configure (but do not open) a session.

        Args:
            host: Fridge IP; defaults to :func:`fridge_host`.
            port: DDMP port; defaults to :func:`fridge_port`.
            deadline_s: Whole-session receive budget from ``__enter__``.
        """
        self._host = host if host is not None else fridge_host()
        self._port = port if port is not None else fridge_port()
        self._deadline_s = deadline_s
        self._deadline = 0.0
        self._sock: socket.socket | None = None
        self._buf = b''
        #: topic param → every published value-byte list seen this session.
        self.published: dict[tuple[int, ...], list[list[int]]] = {}

    def __enter__(self) -> DdmpClient:
        try:
            self._sock = socket.create_connection(
                (self._host, self._port), timeout=CONNECT_TIMEOUT_S
            )
        except OSError as exc:
            raise DdmpError(f'cannot reach the fridge at {self._host}:{self._port}: {exc}') from exc
        self._sock.settimeout(RECV_TIMEOUT_S)
        self._deadline = time.monotonic() + self._deadline_s
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    @property
    def expired(self) -> bool:
        """Whether the session deadline has passed (callers stop issuing requests)."""
        return time.monotonic() > self._deadline

    def _send(self, data: list[int]) -> None:
        assert self._sock is not None
        try:
            self._sock.sendall(encode_frame(data))
        except OSError as exc:
            raise DdmpError(f'send failed: {exc}') from exc

    def _handle(self, frame: list[int]) -> int:
        """Handle one incoming frame; return its action code."""
        action = frame[0]
        if action == PING:
            self._send([ACK])
        elif action == PUBLISH:
            self._send([ACK])
            self.published.setdefault(tuple(frame[1:5]), []).append(frame[5:])
        return action

    def _collect(self, *, until_answer: bool) -> int | None:
        """Receive and handle frames until an ACK/NAK (if asked), quiet, or deadline.

        Args:
            until_answer: Return as soon as an ACK or NAK arrives (the fridge's
                answer to our just-sent command; publishes interleave and are
                handled either way).

        Returns:
            The answering action (ACK/NAK) when one arrived, else None (quiet
            recv timeout, peer close, or session deadline).
        """
        assert self._sock is not None
        while time.monotonic() < self._deadline:
            try:
                chunk = self._sock.recv(4096)
            except TimeoutError:
                return None
            except OSError as exc:
                raise DdmpError(f'receive failed: {exc}') from exc
            if not chunk:
                return None
            self._buf += chunk
            frames, self._buf = split_frames(self._buf)
            answer: int | None = None
            for frame in frames:
                action = self._handle(frame)
                if action in (ACK, NAK):
                    answer = action
            if until_answer and answer is not None:
                return answer
        return None

    def ping(self) -> None:
        """Send a PING and consume the fridge's answer (keeps the session warm)."""
        self._send([PING])
        self._collect(until_answer=True)

    def request(self, param: list[int]) -> None:
        """Send one SUBSCRIBE and wait for its ACK/NAK; publishes accumulate.

        Fire-and-continue flavor for bulk polls: an unanswered or NAKed topic is
        simply absent from :attr:`published` afterwards, which the poll reader
        treats as a NULL column rather than a failure.
        """
        self._send([SUBSCRIBE, *param])
        self._collect(until_answer=True)

    def drain(self) -> None:
        """Collect until quiet: publishes for recent subscribes may still be in flight."""
        self._collect(until_answer=False)

    def frames_for(self, param: list[int]) -> list[list[int]]:
        """Return every published value-byte list seen for a topic this session."""
        return self.published.get(tuple(param), [])

    def subscribe(self, param: list[int]) -> list[list[int]]:
        """Subscribe one topic and return its publish frames, draining if needed.

        Args:
            param: The 4-byte topic param.

        Returns:
            The value-byte lists of every publish for ``param`` (usually one;
            list-shaped so a chunked topic would still round-trip).

        Raises:
            DdmpError: ``refused=True`` if the fridge NAKed the subscribe;
                ``refused=False`` if the session died or stayed silent.
        """
        self._send([SUBSCRIBE, *param])
        answer = self._collect(until_answer=True)
        if answer == NAK:
            raise DdmpError(f'fridge refused subscribe for {param}', refused=True)
        if answer is None:
            raise DdmpError(f'no answer to subscribe for {param}')
        if not self.frames_for(param):
            self.drain()
        return self.frames_for(param)

    def write(self, param: list[int], value_bytes: list[int]) -> None:
        """Publish a value to a topic (the DDMP write path) and require an ACK.

        Args:
            param: The 4-byte topic param (a setpoint or compartment-power topic).
            value_bytes: The encoded value (:func:`encode_ddegc` / :func:`encode_bool`).

        Raises:
            DdmpError: ``refused=True`` on NAK; ``refused=False`` when no ACK came.
        """
        self._send([PUBLISH, *param, *value_bytes])
        answer = self._collect(until_answer=True)
        if answer == NAK:
            raise DdmpError(f'fridge refused write to {param}', refused=True)
        if answer is None:
            raise DdmpError(f'no ACK for write to {param}')
