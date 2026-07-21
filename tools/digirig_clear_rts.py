"""Clear RTS/DTR on the Digirig serial port so an enumeration can never hold PTT.

The Digirig hardware-keys the radio's PTT whenever the CP2102N's RTS line is
asserted, and the port's virgin modem-control state on a fresh boot/replug is
untrusted (the 2026-07-15 dead-key incident held a live carrier). This tool
opens the port, explicitly deasserts RTS and DTR, and verifies the result. It
runs from a udev-triggered oneshot (``deploy/digirig-rts-clear.service``) on
every enumeration, including boot coldplug.

Caveat, accepted in ``.claude/modules/radio.md`` (the RTS=PTT trap): the Linux tty layer
asserts RTS+DTR *on open*, so the clearer itself blips PTT for a few
milliseconds — there is no portable way to open a tty without that, and a
millisecond blip beats an indefinitely held key. The clearing ioctl runs
immediately after open to keep that window minimal.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import struct
import sys
import termios

from common.cli import run_cli

DEFAULT_DEVICE = '/dev/digirig'


def modem_bits(fd: int) -> int:
    """Read the port's modem-control line state.

    Args:
        fd: An open serial-port file descriptor.

    Returns:
        The ``TIOCMGET`` bit set (``termios.TIOCM_*`` flags).
    """
    raw = fcntl.ioctl(fd, termios.TIOCMGET, struct.pack('I', 0))
    return int(struct.unpack('I', raw)[0])


def clear_rts_dtr(device: str) -> int:
    """Deassert RTS and DTR on ``device`` and verify they read back clear.

    Args:
        device: Path to the serial device.

    Returns:
        Process exit code: 0 when both lines verify clear, 1 otherwise.
    """
    try:
        fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError as exc:
        print(f'cannot open {device}: {exc}', file=sys.stderr)
        return 1
    try:
        mask = struct.pack('I', termios.TIOCM_RTS | termios.TIOCM_DTR)
        fcntl.ioctl(fd, termios.TIOCMBIC, mask)
        bits = modem_bits(fd)
    finally:
        os.close(fd)
    rts = bool(bits & termios.TIOCM_RTS)
    dtr = bool(bits & termios.TIOCM_DTR)
    rts_s = 'ASSERTED' if rts else 'clear'
    dtr_s = 'ASSERTED' if dtr else 'clear'
    print(f'{device}: RTS={rts_s} DTR={dtr_s}', flush=True)
    if rts or dtr:
        print('modem lines still asserted after TIOCMBIC', file=sys.stderr)
        return 1
    return 0


def main() -> int:
    """Parse args and clear the Digirig port's modem lines."""
    parser = argparse.ArgumentParser(description='Deassert RTS/DTR on the Digirig serial port.')
    parser.add_argument(
        '--device', default=DEFAULT_DEVICE, help='serial device (default %(default)s)'
    )
    args = parser.parse_args()
    return clear_rts_dtr(args.device)


if __name__ == '__main__':
    run_cli(main)
