#!/usr/bin/env python3
"""Standalone Icom CI-V connectivity probe (no Hamlib, stdlib only).

Confirms two-way CI-V control over a serial cable by auto-sweeping common baud
rates, auto-discovering the radio's CI-V address (broadcast read-ID), and then
reading and decoding the operating frequency. Intended as the decisive on-radio
bench test for whether the OPC-478UC clone cable can drive the ID-5100A.

Usage (Pi / Linux)::

    uv run tools/civ_probe.py --device /dev/ttyACM0

Usage (dev laptop / macOS)::

    uv run tools/civ_probe.py --device /dev/cu.usbmodem5A4B0354141

On Linux the device needs dialout-group membership (or sudo). Pass --baud and/or
--address to skip the sweep once the working values are known.
"""

from __future__ import annotations

import argparse
import os
import select
import sys
import termios
import time

# Standard CI-V baud rates, ordered most-likely-first for the ID-5100 SP jack.
BAUD_SWEEP: tuple[int, ...] = (19200, 9600, 4800, 38400)

# termios speed constants for the bauds we probe.
_SPEED = {
    4800: termios.B4800,
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
}

CONTROLLER = 0xE0  # our address (a PC controller, per Icom convention)
BROADCAST = 0x00  # CI-V broadcast: any radio answers from its own address
DEFAULT_ADDR = 0x8C  # ID-5100 factory CI-V address


def open_port(device: str, baud: int) -> int:
    """Open and raw-configure a serial port at the given baud.

    Args:
        device: Serial device path.
        baud: Baud rate; must be a key of ``_SPEED``.

    Returns:
        An open file descriptor configured 8N1, raw, non-blocking.
    """
    fd = os.open(device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    speed = _SPEED[baud]
    attrs = termios.tcgetattr(fd)  # [iflag, oflag, cflag, lflag, ispeed, ospeed, cc]
    attrs[0] = 0  # iflag: no input processing
    attrs[1] = 0  # oflag: no output processing
    attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD  # cflag: 8N1, local, rx on
    attrs[3] = 0  # lflag: non-canonical, no echo
    attrs[4] = speed  # ispeed
    attrs[5] = speed  # ospeed
    attrs[6][termios.VMIN] = 0  # non-blocking read
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def transact(fd: int, frame: bytes, settle: float = 0.5) -> bytes:
    """Send a CI-V frame and collect everything read back within ``settle`` seconds.

    Args:
        fd: Open serial descriptor.
        frame: Raw CI-V frame to transmit.
        settle: Seconds to keep reading after transmit.

    Returns:
        All bytes received, including the single-wire bus echo of ``frame``.
    """
    termios.tcflush(fd, termios.TCIOFLUSH)
    os.write(fd, frame)
    out = b''
    end = time.monotonic() + settle
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            chunk = os.read(fd, 256)
            if chunk:
                out += chunk
    return out


def parse_frames(buf: bytes) -> list[bytes]:
    """Split a byte buffer into CI-V frames (FE.. preamble to FD terminator).

    Args:
        buf: Raw bytes off the bus.

    Returns:
        A list of complete frames, each ``b'\\xfe\\xfe' .. b'\\xfd'``.
    """
    frames: list[bytes] = []
    i, n = 0, len(buf)
    while i < n - 1:
        if buf[i] == 0xFE and buf[i + 1] == 0xFE:
            k = i + 2
            while k < n and buf[k] != 0xFD:
                k += 1
            if k < n:
                frames.append(buf[i : k + 1])
                i = k + 1
                continue
        i += 1
    return frames


def replies(buf: bytes) -> list[tuple[int, int, bytes]]:
    """Extract frames addressed to us, dropping our own single-wire echo.

    Args:
        buf: Raw bytes off the bus.

    Returns:
        ``(from_addr, cmd, data)`` tuples for frames whose destination is the
        controller address (i.e. genuine radio replies, not the echo).
    """
    out: list[tuple[int, int, bytes]] = []
    for f in parse_frames(buf):
        body = f.lstrip(b'\xfe')[:-1]  # strip preamble + trailing FD
        if len(body) < 3:
            continue
        to_addr, from_addr, cmd, data = body[0], body[1], body[2], body[3:]
        if to_addr == CONTROLLER and from_addr != CONTROLLER:
            out.append((from_addr, cmd, data))
    return out


def decode_freq(data: bytes) -> int:
    """Decode an Icom little-endian BCD frequency payload to Hz.

    Args:
        data: At least 5 BCD bytes (1Hz/10Hz first, 100MHz/1GHz last).

    Returns:
        Frequency in Hz.
    """
    digits: list[int] = []
    for b in data[:5]:
        digits.append(b & 0x0F)
        digits.append(b >> 4)
    return sum(d * 10**i for i, d in enumerate(digits))


def frame(to_addr: int, cmd: int, *data: int) -> bytes:
    """Build a CI-V frame from the controller to ``to_addr``."""
    return bytes([0xFE, 0xFE, to_addr, CONTROLLER, cmd, *data, 0xFD])


def probe_baud(device: str, baud: int, address: int | None) -> tuple[int, int] | None:
    """Try one baud: discover the address (if unknown) and read the frequency.

    Args:
        device: Serial device path.
        baud: Baud to try.
        address: Known CI-V address, or None to auto-discover via broadcast.

    Returns:
        ``(address, freq_hz)`` on a successful frequency read, else None.
    """
    fd = open_port(device, baud)
    try:
        addr = address
        if addr is None:
            for from_addr, cmd, _data in replies(transact(fd, frame(BROADCAST, 0x19, 0x00))):
                if cmd == 0x19:
                    addr = from_addr
                    print(f'  [{baud:>5}] read-ID reply -> CI-V address 0x{addr:02X}')
                    break
        candidates = [addr] if addr is not None else [DEFAULT_ADDR]
        for cand in candidates:
            for from_addr, cmd, data in replies(transact(fd, frame(cand, 0x03))):
                if cmd == 0x03 and len(data) >= 5:
                    return from_addr, decode_freq(data)
        return None
    finally:
        os.close(fd)


def main() -> int:
    """Parse args, run the probe, and report a verdict."""
    ap = argparse.ArgumentParser(description='Icom CI-V connectivity probe (stdlib only).')
    ap.add_argument('--device', default='/dev/ttyACM0', help='serial device path')
    ap.add_argument('--baud', type=int, choices=sorted(_SPEED), help='fixed baud (else sweep)')
    ap.add_argument(
        '--address',
        type=lambda s: int(s, 0),
        help='known CI-V address, e.g. 0x8C (else auto-discover)',
    )
    args = ap.parse_args()

    bauds = (args.baud,) if args.baud else BAUD_SWEEP
    print(f'probing {args.device} at baud {", ".join(map(str, bauds))} ...')
    for baud in bauds:
        try:
            result = probe_baud(args.device, baud, args.address)
        except OSError as e:
            print(f'  [{baud:>5}] port error: {e}')
            continue
        if result:
            addr, hz = result
            print(f'\nSUCCESS: CI-V control confirmed at {baud} baud, address 0x{addr:02X}')
            print(f'         operating frequency = {hz / 1e6:.6f} MHz')
            print(f'\nHamlib: rigctl -m 3071 -r {args.device} -s {baud} -c 0x{addr:02X}')
            return 0

    print('\nNO REPLY from the radio. The cable enumerates, but no CI-V answer came back.')
    print('Check, in order:')
    print('  1. Cable seated in the SP (CI-V) jack, radio powered on.')
    print('  2. Radio SET menu: CI-V baud (SP Jack) fixed (not Auto), CI-V Transceive ON.')
    print('  3. CI-V address matches (default 0x8C); pass --address if you changed it.')
    print('  4. Right device path (ls /dev/ttyACM* /dev/ttyUSB*).')
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nInterrupted.')
        sys.exit(130)
