"""RTS hardware PTT on the Digirig serial line — the non-CI-V key path.

The Digirig hardware-keys the rig's PTT whenever the CP2102N's RTS line is
asserted — the same line the guard stack normally keeps *deasserted* (see
:mod:`tools.digirig_clear_rts` and the RTS=PTT trap in ``CLAUDE.md``). Keying
over RTS is independent of the CI-V bus, so it still works when CI-V control is
locked out — most importantly while the rig is in cross-band **Repeater Mode**,
which NAKs every CI-V command (``RPRT -9``), PTT included.

This module owns the never-stuck-keyed invariant for the RTS path, mirroring
:func:`radio.transmit.keyed_tx` (the CI-V path): RTS is deasserted in a
``finally`` however the block exits, an independent watchdog force-clears it
after ``max_seconds``, and closing the fd (a tty hangup drops RTS) is the final
backstop. A stuck transmitter is illegal, jams the channel, and cooks the finals
— set the rig's own time-out timer (TOT) as the hardware last resort.

The low-level line control (``TIOCMBIS``/``TIOCMBIC`` on ``TIOCM_RTS``) is the
exact mechanism proven on this hardware by :mod:`tools.digirig_clear_rts`.
"""

from __future__ import annotations

import fcntl
import os
import struct
import sys
import termios
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Protocol

#: The udev-pinned Digirig serial device (``99-digirig.rules``). Overridable so a
#: bench run can target a raw ``/dev/ttyUSBn`` before the rule is in place.
DIGIRIG_SERIAL_DEVICE = os.environ.get('GPS_RADIO_DIGIRIG_DEVICE', '/dev/digirig')

#: Hard ceiling on how long RTS may stay asserted for one transmission — the same
#: env knob the CI-V path reads, so both keyers share one cap set in the unit.
MAX_TX_SECONDS = float(os.environ.get('GPS_RADIO_MAX_TX_SECONDS', '120'))


def _set_line(fd: int, bits: int, *, on: bool) -> None:
    """Assert (``on``) or deassert modem-control ``bits`` on ``fd`` via ioctl."""
    op = termios.TIOCMBIS if on else termios.TIOCMBIC
    fcntl.ioctl(fd, op, struct.pack('I', bits))


def _read_lines(fd: int) -> int:
    """Return the port's modem-control line state (``TIOCM_*`` bit set)."""
    raw = fcntl.ioctl(fd, termios.TIOCMGET, struct.pack('I', 0))
    return int(struct.unpack('I', raw)[0])


def _open_clear(device: str) -> int:
    """Open ``device`` and immediately clear RTS+DTR, returning the fd.

    The tty layer asserts RTS+DTR on open (the accepted ~ms blip, per
    ``digitrig_clear_rts``); the clear runs right after so the port never rests
    keyed. Closes the fd and re-raises if the clear ioctl fails.

    Args:
        device: Path to the Digirig serial device.

    Returns:
        An open file descriptor with RTS and DTR deasserted.

    Raises:
        OSError: If the open or the clearing ioctl fails.
    """
    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        _set_line(fd, termios.TIOCM_RTS | termios.TIOCM_DTR, on=False)
    except OSError:
        os.close(fd)
        raise
    return fd


class RtsPort:
    """A Digirig serial handle whose RTS line is the rig's PTT.

    Opens with RTS/DTR clear; :meth:`key`/:meth:`unkey` toggle only RTS (DTR is
    never a PTT line here and stays clear). Not a context manager itself — the
    keying invariant lives in :func:`keyed_tx_rts`, which owns the lifecycle.
    """

    def __init__(self, device: str = DIGIRIG_SERIAL_DEVICE) -> None:
        """Open ``device`` with RTS/DTR deasserted (see :func:`_open_clear`)."""
        self.device = device
        self._fd = _open_clear(device)

    def key(self) -> None:
        """Assert RTS — key the transmitter."""
        _set_line(self._fd, termios.TIOCM_RTS, on=True)

    def unkey(self) -> None:
        """Deassert RTS — unkey the transmitter."""
        _set_line(self._fd, termios.TIOCM_RTS, on=False)

    def rts_asserted(self) -> bool:
        """Whether RTS currently reads back asserted (the transmitter is keyed)."""
        return bool(_read_lines(self._fd) & termios.TIOCM_RTS)

    def close(self) -> None:
        """Clear RTS and close the fd (the close itself also hangs up the line)."""
        try:
            _set_line(self._fd, termios.TIOCM_RTS, on=False)
        finally:
            os.close(self._fd)


class _Keyer(Protocol):
    """Structural type for the object :func:`keyed_tx_rts` drives."""

    def key(self) -> None: ...
    def unkey(self) -> None: ...
    def close(self) -> None: ...


class _Watchdog(Protocol):
    """Structural type for the timer :func:`keyed_tx_rts` arms (``threading.Timer``)."""

    def start(self) -> None: ...
    def cancel(self) -> None: ...


def _force_unkey_fresh(device: str) -> None:
    """Watchdog backstop: clear RTS over a *fresh* open, best-effort.

    Independent of the keyer's own handle (which may be tied up in playback), so
    a wedged transmit still gets unkeyed. :func:`_open_clear` clears RTS as part
    of opening, so an open→close is a full deassert. Swallows its own errors —
    the rig's TOT is the last resort.
    """
    try:
        os.close(_open_clear(device))
        print(
            'RTS PTT watchdog: force-unkeyed (transmission exceeded the cap)',
            file=sys.stderr,
            flush=True,
        )
    except OSError as exc:
        print(f'RTS PTT watchdog: force-unkey FAILED: {exc}', file=sys.stderr, flush=True)


@contextmanager
def keyed_tx_rts(
    device: str = DIGIRIG_SERIAL_DEVICE,
    max_seconds: float = MAX_TX_SECONDS,
    *,
    port_factory: Callable[[str], _Keyer] = RtsPort,
    timer_factory: Callable[[float, Callable[[], None]], _Watchdog] = threading.Timer,
) -> Iterator[None]:
    """Hold the transmitter keyed over RTS for the ``with`` block; unkeyed after.

    The RTS analogue of :func:`radio.transmit.keyed_tx`. RTS is asserted on entry
    and deasserted in a ``finally`` however the block exits; an independent
    watchdog force-clears RTS if the block runs past ``max_seconds``. If the
    primary unkey fails, a fresh-open :func:`_force_unkey_fresh` runs before the
    watchdog is cancelled — the safety net is never dropped on a failed release.
    The port is always closed, and a tty close drops RTS as the final backstop.

    Use this to transmit while CI-V PTT is unavailable (e.g. Repeater Mode). It
    does **not** touch the CI-V bus or the ``radio-control`` service.

    Args:
        device: The Digirig serial device to key.
        max_seconds: Watchdog ceiling — RTS is force-released past this.
        port_factory: Builds the keyer for ``device`` (injectable for tests).
        timer_factory: Builds the watchdog timer (injectable for tests).

    Yields:
        Control to the caller with the transmitter on the air.

    Raises:
        OSError: If *keying* fails (nothing is left asserted in that case); a
            failed *unkey* is handled internally, not raised.
    """
    port = port_factory(device)

    def _watchdog_fire() -> None:
        try:
            port.unkey()
        except OSError:
            _force_unkey_fresh(device)

    watchdog = timer_factory(max_seconds, _watchdog_fire)
    try:
        port.key()
        watchdog.start()
        try:
            yield
        finally:
            try:
                port.unkey()
            except OSError as exc:
                print(
                    f'RTS unkey failed on the primary handle: {exc}; retrying fresh',
                    file=sys.stderr,
                    flush=True,
                )
                _force_unkey_fresh(device)
            finally:
                watchdog.cancel()
    finally:
        port.close()
