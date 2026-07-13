#!/usr/bin/env python3
"""Standalone Dometic CFX3 DDMP survey probe (stdlib only).

Surveys the fridge's DDMP TCP server (port 13142) beyond the topics the
production reader stores: subscribes the history topics (DC current + per-zone
temperature, hour/day/week spans), the setpoint range topics, and the presented
temperature unit, printing every frame raw + decoded so the undocumented parts
of the protocol (bucket width, array order, units, chunking, the trailing
status byte) can be pinned empirically. The wire format is Dometic's DDMP as
reverse engineered by keshavdv/dometic-cfx3: CR-delimited ``{"ddmp": [...]}``
JSON frames of ``[action, p0, p1, p2, p3, *value_bytes]``.

Self-contained on purpose — the fridge lives on the van LAN, so this gets
scp'd to the Pi and run with the system python3, no repo venv:

    scp tools/cfx3_probe.py pmorgan@192.168.42.178:/tmp/
    ssh pmorgan@192.168.42.178 python3 /tmp/cfx3_probe.py

Modes::

    python3 cfx3_probe.py                      # one survey session
    python3 cfx3_probe.py --watch 60           # repeated DC-history sessions + diffs
    python3 cfx3_probe.py --write-test 0 4.0   # setpoint write round-trip (restores)

``CFX_HOST`` / ``CFX_PORT`` (or --host/--port) point at the fridge. Every poll
briefly claims the fridge's single app-client slot, like the production reader.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import socket
import sys
import time

DEFAULT_HOST = os.environ.get('CFX_HOST', '192.168.42.185')
DEFAULT_PORT = int(os.environ.get('CFX_PORT', '13142'))

# DDMP action codes (frame byte 0).
PUBLISH, SUBSCRIBE, PING, HELLO, ACK, NAK, NOP = 0, 1, 2, 3, 4, 5, 6
ACTION_NAMES = {0: 'PUBLISH', 1: 'SUBSCRIBE', 2: 'PING', 3: 'HELLO', 4: 'ACK', 5: 'NAK', 6: 'NOP'}

CONNECT_TIMEOUT_S = 5.0
QUIET_S = 1.5  # a silent this-long after a subscribe means the fridge is done answering

#: name -> topic param. The survey covers everything this project may decode but
#: the production reader doesn't yet store; params from keshavdv/dometic-cfx3.
SURVEY: list[tuple[str, list[int]]] = [
    ('dc_hist_hour', [0, 64, 3, 1]),
    ('dc_hist_day', [0, 65, 3, 1]),
    ('dc_hist_week', [0, 66, 3, 1]),
    ('comp0_temp_hist_hour', [0, 64, 1, 1]),
    ('comp0_temp_hist_day', [0, 65, 1, 1]),
    ('comp0_temp_hist_week', [0, 66, 1, 1]),
    ('comp1_temp_hist_hour', [16, 64, 1, 1]),
    ('comp1_temp_hist_day', [16, 65, 1, 1]),
    ('comp1_temp_hist_week', [16, 66, 1, 1]),
    ('comp0_temp_range', [0, 128, 1, 1]),
    ('comp1_temp_range', [16, 128, 1, 1]),
    ('comp0_recommended_range', [0, 129, 1, 1]),
    ('comp1_recommended_range', [16, 129, 1, 1]),
    ('presented_temp_unit', [0, 0, 2, 1]),
]

#: the --watch subset: the DC power-usage history spans being pinned.
WATCH: list[tuple[str, list[int]]] = SURVEY[:3]

SETPOINT_PARAMS: dict[int, list[int]] = {0: [0, 2, 1, 1], 1: [16, 2, 1, 1]}


def encode_frame(data: list[int]) -> bytes:
    """Encode one DDMP frame: the JSON envelope plus the CR delimiter."""
    return (json.dumps({'ddmp': data}) + '\r').encode()


def split_frames(buf: bytes) -> tuple[list[list[int]], bytes]:
    """Split a receive buffer into complete frames plus the unconsumed tail."""
    frames: list[list[int]] = []
    while b'\r' in buf:
        line, buf = buf.split(b'\r', 1)
        if not line.strip():
            continue
        try:
            frame = json.loads(line)['ddmp']
        except (ValueError, KeyError, TypeError):
            print(f'    !! malformed line: {line[:120]!r}')
            continue
        if isinstance(frame, list) and frame and all(isinstance(b, int) for b in frame):
            frames.append(frame)
    return frames, buf


def int16le_scaled(data: list[int]) -> list[float]:
    """Decode consecutive int16-LE pairs as signed deci-values (÷10)."""
    out: list[float] = []
    for i in range(0, len(data) - 1, 2):
        raw = (data[i + 1] << 8) | data[i]
        if raw >= 0x8000:
            raw -= 0x10000
        out.append(raw / 10)
    return out


class Session:
    """One short DDMP TCP session: connect, subscribe/collect, close.

    ACKs every incoming PING and PUBLISH (an unACKed fridge retries and then
    drops the client) and records every PUBLISH by its 4-byte topic param.
    """

    def __init__(self, host: str, port: int) -> None:
        t0 = time.monotonic()
        self.sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S)
        self.connect_ms = (time.monotonic() - t0) * 1000
        self.sock.settimeout(QUIET_S)
        self.buf = b''
        self.published: dict[tuple[int, ...], list[list[int]]] = {}
        self.acks = 0
        self.naks = 0

    def close(self) -> None:
        self.sock.close()

    def send(self, data: list[int]) -> None:
        self.sock.sendall(encode_frame(data))

    def collect(self, quiet_s: float = QUIET_S) -> None:
        """Receive until the fridge goes quiet, handling every complete frame."""
        self.sock.settimeout(quiet_s)
        while True:
            try:
                chunk = self.sock.recv(4096)
            except TimeoutError:
                return
            if not chunk:
                return
            self.buf += chunk
            frames, self.buf = split_frames(self.buf)
            for frame in frames:
                self._handle(frame)

    def _handle(self, frame: list[int]) -> None:
        action = frame[0]
        if action == PING:
            self.send([ACK])
        elif action == PUBLISH:
            self.send([ACK])
            self.published.setdefault(tuple(frame[1:5]), []).append(frame[5:])
        elif action == ACK:
            self.acks += 1
        elif action == NAK:
            self.naks += 1
        else:
            print(f'    ?? unexpected {ACTION_NAMES.get(action, action)}: {frame}')

    def subscribe(self, param: list[int], quiet_s: float = QUIET_S) -> None:
        self.send([SUBSCRIBE, *param])
        self.collect(quiet_s)


def print_frames(name: str, param: list[int], frames: list[list[int]] | None) -> None:
    """Print every publish for one topic: raw, hex, decode candidates, tail byte."""
    tag = f'{name} {param}'
    if not frames:
        print(f'== {tag}: NO PUBLISH')
        return
    print(f'== {tag}: {len(frames)} publish frame(s)')
    for i, data in enumerate(frames):
        hexs = ' '.join(f'{b:02x}' for b in data)
        print(f'   #{i} ({len(data)} bytes) raw={data}')
        print(f'      hex: {hexs}')
        if len(data) >= 2:
            body = data[:-1] if len(data) % 2 == 1 else data
            print(f'      int16le/10: {int16le_scaled(body)}')
        if len(data) % 2 == 1:
            print(f'      trailing byte: data[{len(data) - 1}] = {data[-1]}')


def survey(host: str, port: int, topics: list[tuple[str, list[int]]]) -> Session:
    """Run one session subscribing every topic; print a timing summary."""
    t0 = time.monotonic()
    s = Session(host, port)
    try:
        s.send([PING])
        s.collect(0.5)
        for _name, param in topics:
            s.subscribe(param)
        s.collect()  # drain stragglers for the last subscribes
    finally:
        s.close()
    total_ms = (time.monotonic() - t0) * 1000
    n_pub = sum(len(v) for v in s.published.values())
    print(
        f'-- session: connect {s.connect_ms:.0f} ms, total {total_ms:.0f} ms, '
        f'{n_pub} publishes, {s.acks} ACKs, {s.naks} NAKs'
    )
    return s


def run_survey(host: str, port: int) -> int:
    """Default mode: one full survey session, everything printed."""
    print(f'CFX3 DDMP survey: {host}:{port}')
    s = survey(host, port, SURVEY)
    for name, param in SURVEY:
        print_frames(name, param, s.published.get(tuple(param)))
    unknown = set(s.published) - {tuple(p) for _n, p in SURVEY}
    for param_t in sorted(unknown):
        print_frames('UNSOLICITED', list(param_t), s.published[param_t])
    return 0


def describe_shift(prev: list[float], cur: list[float]) -> str:
    """Name the relationship between consecutive array snapshots."""
    if prev == cur:
        return 'unchanged'
    if len(prev) == len(cur) and len(cur) > 1:
        if cur[:-1] == prev[1:]:
            return 'SHIFTED LEFT by 1 (newest at END)'
        if cur[1:] == prev[:-1]:
            return 'SHIFTED RIGHT by 1 (newest at START)'
    changed = [i for i, (a, b) in enumerate(zip(prev, cur, strict=False)) if a != b]
    return f'changed at index(es) {changed}'


def run_watch(host: str, port: int, interval: float) -> int:
    """--watch mode: repeated DC-history sessions with per-span diffs."""
    print(f'CFX3 DDMP watch: {host}:{port} every {interval:g}s (Ctrl+C to stop)')
    last: dict[str, tuple[list[float], list[int]]] = {}
    while True:
        stamp = time.strftime('%H:%M:%S')
        try:
            s = survey(host, port, WATCH)
        except OSError as e:
            print(f'[{stamp}] unreachable: {e}')
            time.sleep(interval)
            continue
        for name, param in WATCH:
            frames = s.published.get(tuple(param))
            if not frames:
                print(f'[{stamp}] {name}: no publish')
                continue
            data = frames[0]
            body = data[:-1] if len(data) % 2 == 1 else data
            values = int16le_scaled(body)
            tail = data[-1] if len(data) % 2 == 1 else None
            note = f' [{len(frames)} frames!]' if len(frames) > 1 else ''
            if name in last:
                pv, pd = last[name]
                shift = describe_shift(pv, values)
                tail_note = '' if pd == data else ' tail/raw changed'
                print(f'[{stamp}] {name}: {values} tail={tail} — {shift}{tail_note}{note}')
            else:
                print(f'[{stamp}] {name}: {values} tail={tail} (baseline){note}')
            last[name] = (values, data)
        time.sleep(interval)


def run_write_test(host: str, port: int, zone: int, temp_c: float) -> int:
    """--write-test mode: set a zone setpoint, read back, restore the original."""
    param = SETPOINT_PARAMS[zone]
    print(f'CFX3 DDMP write test: zone {zone} -> {temp_c:g} C (param {param})')
    s = Session(host, port)
    try:
        s.send([PING])
        s.collect(0.5)

        s.subscribe(param)
        frames = s.published.get(tuple(param))
        if not frames:
            print('ABORT: could not read the current setpoint.')
            return 1
        original = int16le_scaled(frames[0][:2])[0]
        print(f'current setpoint: {original:g} C')

        def write_setpoint(value_c: float, label: str) -> bool:
            deci = math.ceil(value_c * 10)
            acks0, naks0 = s.acks, s.naks
            s.send([PUBLISH, *param, deci & 0xFF, (deci >> 8) & 0xFF])
            s.collect()
            verdict = 'ACK' if s.acks > acks0 else ('NAK' if s.naks > naks0 else 'NO REPLY')
            print(f'{label} PUBLISH {value_c:g} C -> {verdict}')
            s.published.pop(tuple(param), None)
            s.subscribe(param)
            back = s.published.get(tuple(param))
            if back:
                print(f'{label} read-back: {int16le_scaled(back[0][:2])[0]:g} C')
            else:
                print(f'{label} read-back: NO PUBLISH')
            return verdict == 'ACK'

        ok = write_setpoint(temp_c, 'write')
        write_setpoint(original, 'restore')
        return 0 if ok else 1
    finally:
        s.close()


def main() -> int:
    """Parse args and dispatch the selected probe mode."""
    ap = argparse.ArgumentParser(description='Dometic CFX3 DDMP survey probe (stdlib only).')
    ap.add_argument('--host', default=DEFAULT_HOST, help='fridge IP (or CFX_HOST env)')
    ap.add_argument('--port', type=int, default=DEFAULT_PORT, help='DDMP port (or CFX_PORT env)')
    ap.add_argument('--watch', type=float, metavar='SECONDS', help='repeat DC-history sessions')
    ap.add_argument(
        '--write-test',
        nargs=2,
        metavar=('ZONE', 'TEMP_C'),
        help='setpoint write round-trip on ZONE (0|1); restores the original value',
    )
    args = ap.parse_args()

    try:
        if args.write_test:
            zone, temp_c = int(args.write_test[0]), float(args.write_test[1])
            if zone not in SETPOINT_PARAMS:
                ap.error('ZONE must be 0 or 1')
            return run_write_test(args.host, args.port, zone, temp_c)
        if args.watch:
            return run_watch(args.host, args.port, args.watch)
        return run_survey(args.host, args.port)
    except OSError as e:
        print(f'unreachable: {e}')
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\nInterrupted.')
        sys.exit(130)
