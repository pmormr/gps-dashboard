"""Hamlib ``rigctld`` client — speak the daemon's TCP text protocol from stdlib.

A long-lived ``rigctld`` (model 3071, the Icom ID-5100) owns the CI-V serial port
and exposes its line protocol on ``127.0.0.1:4532``; the radio routes talk to it
through this module rather than a Python Hamlib binding (see R2 in
``.claude/modules/radio.md``). Every command is sent in rigctld's *extended*
mode — prefixed ``+\\`` and using the long command name — so the reply is a
self-delimiting block terminated by ``RPRT <code>`` (``0`` = ok, negative = Hamlib
errno) with labelled field values. The exact wire format was captured against a
dummy backend and is pinned in ``tests/test_rigctld.py``.
"""

from __future__ import annotations

import re
import socket
import types
from dataclasses import dataclass, field


class RigctldError(RuntimeError):
    """A rigctld transaction failed: unreachable daemon, bad reply, or ``RPRT != 0``.

    Attributes:
        rprt: The Hamlib return code when the failure was a non-zero ``RPRT`` (the
            rig refused the command); ``None`` for transport/parse failures (the
            daemon was unreachable or spoke garbage).
    """

    def __init__(self, message: str, rprt: int | None = None) -> None:
        super().__init__(message)
        self.rprt = rprt


@dataclass(frozen=True)
class Reply:
    """A parsed extended-mode rigctld reply.

    Attributes:
        rprt: The Hamlib return code (``0`` = success, negative = errno).
        fields: Labelled ``"Key: value"`` body lines, keyed by their label.
        values: Bare body lines with no label (single-value gets, e.g. ``RAWSTR``).
    """

    rprt: int
    fields: dict[str, str] = field(default_factory=dict)
    values: list[str] = field(default_factory=list)


def parse_reply(raw: str) -> Reply:
    """Parse one extended-mode rigctld reply block.

    The first line echoes the command (``get_freq:`` / ``get_level: RAWSTR``) and is
    dropped. Each subsequent line up to the ``RPRT`` terminator is either
    ``"Key: value"`` (→ :attr:`Reply.fields`) or a bare value (→ :attr:`Reply.values`).

    Args:
        raw: The decoded bytes of one reply, including the ``RPRT`` terminator line.

    Returns:
        The parsed :class:`Reply`.

    Raises:
        RigctldError: If no ``RPRT`` terminator line is present.
    """
    fields: dict[str, str] = {}
    values: list[str] = []
    for i, line in enumerate(raw.split('\n')):
        if line.startswith('RPRT '):
            return Reply(int(line[5:].strip()), fields, values)
        if i == 0:
            continue  # command echo, e.g. "get_freq:" or "get_level: RAWSTR"
        if ': ' in line:
            key, val = line.split(': ', 1)
            fields[key] = val
        elif line.strip():
            values.append(line.strip())
    raise RigctldError('no RPRT terminator in rigctld reply')


#: 0xFE wakeup-preamble bytes prepended to a power-ON CI-V frame at 19200 baud
#: (the deployed rigctld link speed). The ID-5100's CI-V circuit sleeps when the
#: rig is powered off; the preamble wakes it before it will read ``18 01``
#: (manual §13-17: 19200→25, 9600→13, 4800→7). Validated on the rig 2026-07-21.
POWER_ON_PREAMBLE = 25


class Rigctld:
    """A short-lived connection to rigctld; one per request is fine on localhost.

    Use as a context manager — the socket is opened on ``__enter__`` and closed on
    ``__exit__``. The getters degrade to ``None`` for capabilities the backend does
    not expose (they pass ``check=False`` and read the ``RPRT``); the setters raise.
    """

    def __init__(self, host: str = '127.0.0.1', port: int = 4532, timeout: float = 2.0) -> None:
        """Configure (but do not open) a rigctld connection.

        Args:
            host: rigctld bind host.
            port: rigctld bind port.
            timeout: Per-operation socket timeout in seconds.
        """
        self._host = host
        self._port = port
        self._timeout = timeout
        self._sock: socket.socket | None = None

    def __enter__(self) -> Rigctld:
        try:
            self._sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        except OSError as exc:
            raise RigctldError(f'cannot reach rigctld at {self._host}:{self._port}: {exc}') from exc
        self._sock.settimeout(self._timeout)
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

    def _read_reply(self) -> str:
        """Read from the socket until a complete ``RPRT <n>`` terminator line arrives."""
        assert self._sock is not None
        buf = b''
        while True:
            idx = buf.find(b'RPRT ')
            if idx != -1 and buf.find(b'\n', idx) != -1:
                break
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.decode(errors='replace')

    def _read_line(self) -> str:
        """Read one newline-terminated line (non-extended replies have no ``RPRT``)."""
        assert self._sock is not None
        buf = b''
        while b'\n' not in buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        return buf.decode(errors='replace')

    def command(self, cmd: str, check: bool = True) -> Reply:
        """Send one long-form command in extended mode and parse the reply.

        Args:
            cmd: The rigctld long command and its args, e.g. ``'set_freq 146520000'``.
            check: Raise on a non-zero ``RPRT`` (default). Pass ``False`` to inspect
                the code yourself — used by getters whose capability may be absent.

        Returns:
            The parsed reply.

        Raises:
            RigctldError: On a transport error, a malformed reply, or (when ``check``
                is true) a non-zero ``RPRT``.
        """
        if self._sock is None:
            raise RigctldError('command() called outside a "with Rigctld()" block')
        try:
            self._sock.sendall(f'+\\{cmd}\n'.encode())
            reply = parse_reply(self._read_reply())
        except OSError as exc:
            raise RigctldError(f'rigctld I/O error on {cmd!r}: {exc}') from exc
        if check and reply.rprt != 0:
            raise RigctldError(f'rigctld {cmd!r} failed (RPRT {reply.rprt})', rprt=reply.rprt)
        return reply

    def _send_cmd_bytes(self, data: bytes) -> None:
        """Put arbitrary bytes on the CI-V bus via rigctld's ``send_cmd``; raise on RPRT != 0.

        The caller supplies the exact wire bytes (full envelope, plus any
        wakeup preamble). ``send_cmd`` replies differ from every other command:
        the ``RPRT`` terminator rides on the ``Reply:`` line (captured live
        2026-07-02), so this parses the code directly instead of via
        :func:`parse_reply`.

        Args:
            data: The exact bytes to send.

        Raises:
            RigctldError: On a transport error or a non-zero ``RPRT``.
        """
        if self._sock is None:
            raise RigctldError('send_cmd called outside a "with Rigctld()" block')
        arg = ''.join(f'\\0x{b:02x}' for b in data)
        try:
            self._sock.sendall(f'+\\send_cmd {arg}\n'.encode())
            raw = self._read_reply()
        except OSError as exc:
            raise RigctldError(f'rigctld I/O error on send_cmd: {exc}') from exc
        idx = raw.find('RPRT ')
        if idx == -1:
            raise RigctldError('no RPRT terminator in rigctld reply')
        rprt = int(raw[idx + 5 :].split(None, 1)[0])
        if rprt != 0:
            raise RigctldError(f'rigctld send_cmd failed (RPRT {rprt})', rprt=rprt)

    def send_civ(self, payload: bytes, addr: int = 0x8C, ctrl: int = 0xE0) -> None:
        """Send a raw CI-V frame through rigctld's ``send_cmd`` passthrough.

        For commands the Hamlib backend doesn't model (R7 in
        ``.claude/modules/radio.md``) — the payload is the command/sub-command/
        data bytes from the manual's CI-V table, wrapped here in the
        ``FE FE <addr> <ctrl> ... FD`` envelope.

        Args:
            payload: Command, sub-command, and data bytes (no envelope).
            addr: The rig's CI-V address.
            ctrl: The controller's CI-V address.

        Raises:
            RigctldError: On a transport error or a non-zero ``RPRT``.
        """
        self._send_cmd_bytes(bytes((0xFE, 0xFE, addr, ctrl)) + payload + b'\xfd')

    def set_powerstat(self, on: bool, addr: int = 0x8C, ctrl: int = 0xE0) -> None:
        """Power the rig on or off via raw CI-V command 18 (ID-5100 manual §13-17).

        Driven directly rather than through Hamlib's ``set_powerstat`` (an
        unreliable no-op on the model-3071 backend) and **fire-and-forget** — the
        rig exposes no readable power state (``get_powerstat``/``get_freq`` return
        cached values). Power-ON prepends a wakeup preamble of
        :data:`POWER_ON_PREAMBLE` × ``0xFE``: the rig's CI-V circuit sleeps when
        powered off and won't read ``18 01`` without it. Validated on the rig
        2026-07-21 (off = bare ``18 00``; on = 25× ``FE`` + ``18 01`` at 19200 baud).

        Args:
            on: ``True`` to power on (with preamble), ``False`` to power off.
            addr: The rig's CI-V address.
            ctrl: The controller's CI-V address.

        Raises:
            RigctldError: On a transport error or a non-zero ``RPRT``.
        """
        frame = bytes((0xFE, 0xFE, addr, ctrl)) + (b'\x18\x01' if on else b'\x18\x00') + b'\xfd'
        if on:
            frame = b'\xfe' * POWER_ON_PREAMBLE + frame
        self._send_cmd_bytes(frame)

    def read_civ(
        self, payload: bytes, data_len: int = 1, addr: int = 0x8C, ctrl: int = 0xE0
    ) -> bytes | None:
        """Send a raw CI-V *read* and return the reply frame's data bytes.

        Uses rigctld's **non-extended** ``send_cmd_rx`` — the extended (``+\\``)
        form returns no reply bytes at all (captured live 2026-07-18). The
        single-wire CI-V bus echoes the sent frame back first, so the expected
        read length is echo + reply frame; the reply line is the hex-escaped
        bytes plus a trailing count, no ``RPRT``. Degrades to ``None`` when the
        reply is short or malformed (rig silent, NAK) — like the other getters.

        Args:
            payload: Command/sub-command bytes from the manual's CI-V table.
            data_len: Data bytes expected in the reply frame.
            addr: The rig's CI-V address.
            ctrl: The controller's CI-V address.

        Returns:
            The reply's data bytes, or ``None`` when unreadable.
        """
        if self._sock is None:
            raise RigctldError('read_civ() called outside a "with Rigctld()" block')
        frame = bytes((0xFE, 0xFE, addr, ctrl)) + payload + b'\xfd'
        expect = len(frame) + 4 + len(payload) + data_len + 1
        arg = ''.join(f'\\0x{b:02x}' for b in frame)
        try:
            self._sock.sendall(f'\\send_cmd_rx {arg} {expect}\n'.encode())
            raw = self._read_line()
        except OSError as exc:
            raise RigctldError(f'rigctld I/O error on send_cmd_rx: {exc}') from exc
        received = bytes(int(t, 16) for t in re.findall(r'\\0x([0-9A-Fa-f]{2})', raw))
        reply = received[len(frame) :]
        prefix = bytes((0xFE, 0xFE, ctrl, addr)) + payload
        wellformed = (
            reply.startswith(prefix)
            and reply.endswith(b'\xfd')
            and len(reply) == len(prefix) + data_len + 1
        )
        return reply[len(prefix) : -1] if wellformed else None

    # --- reads (degrade to None for capabilities the ID-5100 backend lacks) ---

    def get_dualwatch(self) -> bool | None:
        """Dualwatch (True) / single watch (False) via raw CI-V ``16 59`` read.

        Not modeled by the Hamlib backend; ``None`` when the rig doesn't answer.
        """
        data = self.read_civ(b'\x16\x59')
        if data is None or data[0] not in (0, 1):
            return None
        return data[0] == 1

    def get_freq(self) -> int:
        """Active VFO frequency in Hz."""
        return int(self.command('get_freq').fields['Frequency'])

    def get_mode(self) -> tuple[str, int]:
        """Active VFO ``(mode, passband_hz)`` (passband ``0`` when the rig reports none)."""
        r = self.command('get_mode')
        return r.fields['Mode'], int(r.fields['Passband'])

    def get_level(self, name: str) -> float | None:
        """A numeric level (e.g. ``RAWSTR``, ``STRENGTH``), or ``None`` if unsupported."""
        r = self.command(f'get_level {name}', check=False)
        if r.rprt != 0 or not r.values:
            return None
        return float(r.values[0])

    def get_func(self, name: str) -> bool | None:
        """A boolean function flag (e.g. ``TONE``, ``TSQL``), or ``None`` if unsupported."""
        r = self.command(f'get_func {name}', check=False)
        if r.rprt != 0 or not r.values:
            return None
        return r.values[0] == '1'

    def get_ctcss_tone(self) -> int | None:
        """CTCSS tone in tenths of Hz (``1000`` = 100.0 Hz), or ``None`` if unsupported."""
        r = self.command('get_ctcss_tone', check=False)
        return int(r.fields['CTCSS Tone']) if r.rprt == 0 and 'CTCSS Tone' in r.fields else None

    def get_rptr_shift(self) -> str | None:
        """Repeater shift (``'+'`` / ``'-'`` / ``'None'``), or ``None`` if unsupported."""
        r = self.command('get_rptr_shift', check=False)
        return r.fields.get('Rptr Shift') if r.rprt == 0 else None

    def get_rptr_offs(self) -> int | None:
        """Repeater offset in Hz, or ``None`` if unsupported."""
        r = self.command('get_rptr_offs', check=False)
        return int(r.fields['Rptr Offset']) if r.rprt == 0 and 'Rptr Offset' in r.fields else None

    def get_dcd(self) -> bool | None:
        """Data-carrier-detect (squelch open) flag, or ``None`` if unsupported."""
        r = self.command('get_dcd', check=False)
        return (r.fields.get('DCD') == '1') if r.rprt == 0 else None

    def get_ptt(self) -> bool | None:
        """Transmit (PTT) flag, or ``None`` if unsupported."""
        r = self.command('get_ptt', check=False)
        return (r.fields.get('PTT') == '1') if r.rprt == 0 else None

    # --- writes (raise on any non-zero RPRT) ---

    def set_level(self, name: str, value: float) -> None:
        """Set a numeric level (e.g. ``AF``, ``SQL``, ``RFPOWER``), normalized 0..1."""
        self.command(f'set_level {name} {value:g}')

    def set_freq(self, hz: int) -> None:
        """Tune the active VFO to ``hz``."""
        self.command(f'set_freq {hz}')

    def set_ptt(self, on: bool) -> None:
        """Key (``on=True``) or unkey the transmitter via CI-V PTT.

        The transmit primitive for the announce path (R11 in
        ``.claude/modules/radio.md``). Keying puts the rig on the air, so
        no code calls this directly: the only intended caller is the guarded
        ``keyed_tx`` context manager in :mod:`radio.transmit`, which guarantees
        a matching unkey in a ``finally`` plus a watchdog. A stuck key is
        illegal, jams the channel, and cooks the finals.
        """
        self.command(f'set_ptt {1 if on else 0}')

    def set_mode(self, mode: str, passband: int = 0) -> None:
        """Set the active VFO mode; ``passband=0`` lets the backend pick its default."""
        self.command(f'set_mode {mode} {passband}')

    def set_ctcss_tone(self, tenths: int) -> None:
        """Set the CTCSS tone in tenths of Hz (``1000`` = 100.0 Hz)."""
        self.command(f'set_ctcss_tone {tenths}')

    def set_func(self, name: str, on: bool) -> None:
        """Enable or disable a function flag (e.g. ``TONE``, ``TSQL``)."""
        self.command(f'set_func {name} {1 if on else 0}')

    def set_rptr_shift(self, shift: str) -> None:
        """Set the repeater shift direction (``'+'`` / ``'-'`` / ``'None'``)."""
        self.command(f'set_rptr_shift {shift}')

    def set_rptr_offs(self, hz: int) -> None:
        """Set the repeater offset in Hz."""
        self.command(f'set_rptr_offs {hz}')
