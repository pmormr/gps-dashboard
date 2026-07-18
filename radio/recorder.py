"""VOX-gated radio transmission recorder — Digirig SP1 capture → WAV + DB rows.

Standalone daemon, the radio-plane sibling of the GPS logger: a continuous
``arecord`` pipe off the Digirig codec feeds fixed 100 ms blocks through the
pure VOX gate (``radio/vox.py``); each opening becomes a WAV file with a
ring-buffer pre-roll, and each close inserts one GPS-snapped
``radio_transmissions`` row. Design is R8 in ``plans/radio-platform-plan.md``.

Startup pins state the hardware won't hold for us: the C-Media codec resets to
max gain + AGC ON on every USB replug (the 2a trap), and the rig's AF level
sets the SP1 record level. Both pins re-run on every session (re)start, so a
replug — which kills ``arecord`` and bounces the session — re-pins on recovery
by construction.

Metadata honesty: the rigctld snapshot at gate-open reads the *active main
band only*, while SP1 audio is the A+B mix — a sub-band signal can carry a
wrong freq/mode tag, and ``dcd_main`` records what the main band's squelch
said so readers can judge the tag's confidence.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import sys
import time
import wave
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection
from typing import IO

from api.db import _canonical, get_connection, init_db
from api.rigctld import Rigctld, RigctldError
from common.proc import run
from radio.paths import audio_dir
from radio.vox import GateEvent, VoxGate, amplitude_dbfs, block_energy, rms_dbfs

SAMPLE_RATE = 48000
BLOCK_SECONDS = 0.1
BLOCK_FRAMES = int(SAMPLE_RATE * BLOCK_SECONDS)
BLOCK_BYTES = BLOCK_FRAMES * 2  # mono S16_LE

PREROLL_BLOCKS = 30  # 3 s of context ahead of the gate opening
HANG_BLOCKS = 20  # 2 s under the close threshold ends a capture
MAX_BLOCKS = 3000  # 5 min hard cap per file (held carrier → consecutive files)

# Field-tunable thresholds (R8): measured floor −49 dBFS RMS, open-squelch
# static −28. Hysteresis pair, both env-overridable in the unit.
OPEN_DBFS = float(os.environ.get('GPS_RADIO_OPEN_DBFS', '-40'))
CLOSE_DBFS = float(os.environ.get('GPS_RADIO_CLOSE_DBFS', '-45'))

ALSA_DEVICE = os.environ.get('GPS_RADIO_ALSA_DEVICE', 'plughw:CARD=Digirig')
MIXER_CARD = os.environ.get('GPS_RADIO_MIXER_CARD', 'Digirig')
#: ``control:value`` pairs pinned via amixer at session start. Control names are
#: env-overridable so a codec-revision rename is a unit edit, not a code change.
MIXER_SETS = os.environ.get('GPS_RADIO_MIXER_SETS', 'Auto Gain Control:off,Mic:10')
#: Rig AF level pinned at session start (reproducible record level); '' disables.
PIN_AF = os.environ.get('GPS_RADIO_PIN_AF', '0.15')

AUDIO_DIR = audio_dir()
MAX_AGE_DAYS = float(os.environ.get('GPS_RADIO_AUDIO_MAX_DAYS', '180'))
MAX_BYTES = int(float(os.environ.get('GPS_RADIO_AUDIO_MAX_GB', '4')) * 1024**3)

GPS_SNAP_MAX_AGE_SECONDS = 300
RIGCTLD_TIMEOUT_SECONDS = 0.75
HEARTBEAT_SECONDS = 60
PRUNE_INTERVAL_SECONDS = 24 * 3600
#: A .part file untouched this long is a crash leftover, not a live capture
#: (mtime stays fresh while writing; max capture length is 5 min).
STALE_PART_SECONDS = 600

# 2 s ALSA-side buffer + a 1 MiB pipe (~11 s of audio) so a slow gate-open
# metadata snapshot can never back arecord into an overrun.
ARECORD_CMD = [
    'arecord',
    '-D',
    ALSA_DEVICE,
    '-f',
    'S16_LE',
    '-r',
    str(SAMPLE_RATE),
    '-c',
    '1',
    '-t',
    'raw',
    '--buffer-time=2000000',
    '-q',
    '-',
]
PIPE_BYTES = 1 << 20

#: Ring entry: (raw block, sum_of_squares, peak, n_samples) — energy rides along
#: so pre-roll blocks fold into the capture's stats without a recompute.
RingEntry = tuple[bytes, int, int, int]


def audio_rel_path(started: datetime) -> str:
    """Relative audio path (under the audio dir) for a capture start time.

    Args:
        started: UTC start of the audio (gate open minus pre-roll).

    Returns:
        ``YYYY-MM/YYYYmmdd-HHMMSS-mmm.wav`` — month subdir, ms-distinct name.
    """
    return started.strftime('%Y-%m/%Y%m%d-%H%M%S') + f'-{started.microsecond // 1000:03d}.wav'


def prune_selection(
    files: list[tuple[str, float, int]], now_s: float, max_age_s: float, max_bytes: int
) -> list[str]:
    """Choose which audio files the retention pass deletes.

    Age rule first, then an oldest-first sweep until the survivors fit the
    size cap. Pure so the policy is table-testable.

    Args:
        files: ``(rel_path, mtime_epoch_s, size_bytes)`` per candidate file.
        now_s: Current epoch seconds.
        max_age_s: Maximum age before a file is deleted regardless of size.
        max_bytes: Total size the surviving files must fit within.

    Returns:
        Relative paths to delete, oldest first.
    """
    doomed: list[tuple[str, float, int]] = []
    kept: list[tuple[str, float, int]] = []
    for f in sorted(files, key=lambda t: t[1]):
        (doomed if now_s - f[1] > max_age_s else kept).append(f)
    total = sum(size for _, _, size in kept)
    for f in kept:
        if total <= max_bytes:
            break
        doomed.append(f)
        total -= f[2]
    return [rel for rel, _, _ in doomed]


@dataclass
class Capture:
    """One in-progress transmission: an open ``.part`` WAV plus running stats."""

    part_path: Path
    rel_path: str
    started: datetime
    writer: wave.Wave_write
    freq_hz: int | None
    mode: str | None
    dcd_main: int | None
    lat: float | None
    lon: float | None
    frames: int = 0
    sq_sum: int = 0
    peak: int = 0

    def write(self, block: bytes, sq: int, peak: int, n: int) -> None:
        """Append one block to the WAV and fold its energy into the stats."""
        self.writer.writeframesraw(block)
        self.sq_sum += sq
        self.peak = max(self.peak, peak)
        self.frames += n

    def close(self, conn: Connection) -> None:
        """Finalize the WAV (rename ``.part`` away) and insert the DB row."""
        self.writer.close()
        final = AUDIO_DIR / self.rel_path
        self.part_path.rename(final)
        duration = self.frames / SAMPLE_RATE
        ended = self.started + timedelta(seconds=duration)
        conn.execute(
            'INSERT INTO radio_transmissions '
            '(started_utc, ended_utc, duration_s, freq_hz, mode, dcd_main, '
            'peak_dbfs, rms_dbfs, audio_path, lat, lon) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                _canonical(self.started),
                _canonical(ended),
                round(duration, 3),
                self.freq_hz,
                self.mode,
                self.dcd_main,
                round(amplitude_dbfs(float(self.peak)), 1),
                round(rms_dbfs(self.sq_sum, self.frames), 1),
                self.rel_path,
                self.lat,
                self.lon,
            ),
        )
        conn.commit()
        freq = f'{self.freq_hz / 1e6:.3f} MHz' if self.freq_hz else 'freq n/a'
        print(f'captured {duration:.1f}s ({freq}) → {self.rel_path}', flush=True)


def rig_snapshot() -> tuple[int | None, str | None, int | None]:
    """Best-effort ``(freq_hz, mode, dcd)`` from rigctld at gate-open.

    Reads the active main band only (no get-VFO on this backend). Degrades to
    ``None`` fields when rigctld is unreachable — metadata must never block or
    drop a recording.
    """
    try:
        with Rigctld(timeout=RIGCTLD_TIMEOUT_SECONDS) as rig:
            freq = rig.get_freq()
            mode, _ = rig.get_mode()
            dcd = rig.get_dcd()
        return freq, mode, None if dcd is None else int(dcd)
    except RigctldError as exc:
        print(f'rigctld snapshot failed: {exc}', file=sys.stderr, flush=True)
        return None, None, None


def gps_snap(conn: Connection) -> tuple[float | None, float | None]:
    """Latest raw fix as ``(lat, lon)``, or ``(None, None)`` when stale/absent.

    Args:
        conn: Open DB connection.
    """
    row = conn.execute(
        'SELECT timestamp, lat, lon FROM gps_points ORDER BY id DESC LIMIT 1'
    ).fetchone()
    if row is None:
        return None, None
    fix_dt = datetime.fromisoformat(str(row['timestamp']).replace('Z', '+00:00'))
    if (datetime.now(UTC) - fix_dt).total_seconds() > GPS_SNAP_MAX_AGE_SECONDS:
        return None, None
    lat, lon = row['lat'], row['lon']
    return (
        None if lat is None else float(lat),
        None if lon is None else float(lon),
    )


def pin_mixer() -> None:
    """Pin the codec's capture chain — power-on defaults are max gain + AGC ON."""
    for item in MIXER_SETS.split(','):
        item = item.strip()
        if not item:
            continue
        name, _, value = item.rpartition(':')
        code, out, err = run(['amixer', '-c', MIXER_CARD, 'sset', name, value])
        if code != 0:
            detail = err.strip() or out.strip()
            print(f'mixer pin failed ({name}={value}): {detail}', file=sys.stderr, flush=True)


def pin_af() -> None:
    """Pin the rig's AF level for a reproducible record level (best-effort)."""
    if not PIN_AF:
        return
    try:
        with Rigctld(timeout=RIGCTLD_TIMEOUT_SECONDS) as rig:
            rig.set_level('AF', float(PIN_AF))
        print(f'AF pinned to {PIN_AF}', flush=True)
    except RigctldError as exc:
        print(f'AF pin failed (level unpinned): {exc}', file=sys.stderr, flush=True)


def prune_audio(conn: Connection) -> None:
    """Apply retention: delete doomed WAVs, NULL their rows' ``audio_path``.

    Rows outlive their audio by design — the transmission log and map pins
    survive the pruner. Also sweeps crash-leftover ``.part`` files.

    Args:
        conn: Open DB connection.
    """
    files: list[tuple[str, float, int]] = []
    for p in AUDIO_DIR.rglob('*.wav'):
        st = p.stat()
        files.append((str(p.relative_to(AUDIO_DIR)), st.st_mtime, st.st_size))
    doomed = prune_selection(files, time.time(), MAX_AGE_DAYS * 86400, MAX_BYTES)
    for rel in doomed:
        (AUDIO_DIR / rel).unlink(missing_ok=True)
        conn.execute(
            'UPDATE radio_transmissions SET audio_path = NULL WHERE audio_path = ?', (rel,)
        )
    if doomed:
        conn.commit()
        print(f'pruned {len(doomed)} audio file(s)', flush=True)
    now = time.time()
    for p in AUDIO_DIR.rglob('*.part'):
        if now - p.stat().st_mtime > STALE_PART_SECONDS:
            p.unlink(missing_ok=True)
            print(f'removed stale partial {p.name}', file=sys.stderr, flush=True)


def open_capture(conn: Connection, ring: deque[RingEntry]) -> Capture:
    """Start a capture: snapshot metadata, open the ``.part`` WAV, write pre-roll.

    Args:
        conn: Open DB connection (for the GPS snap).
        ring: The pre-roll ring buffer; its contents become the file's head.

    Returns:
        The in-progress :class:`Capture`.
    """
    started = datetime.now(UTC) - timedelta(seconds=len(ring) * BLOCK_SECONDS)
    rel = audio_rel_path(started)
    part = AUDIO_DIR / (rel + '.part')
    part.parent.mkdir(parents=True, exist_ok=True)
    writer = wave.open(str(part), 'wb')
    writer.setnchannels(1)
    writer.setsampwidth(2)
    writer.setframerate(SAMPLE_RATE)
    freq, mode, dcd = rig_snapshot()
    lat, lon = gps_snap(conn)
    cap = Capture(
        part_path=part,
        rel_path=rel,
        started=started,
        writer=writer,
        freq_hz=freq,
        mode=mode,
        dcd_main=dcd,
        lat=lat,
        lon=lon,
    )
    for block, sq, peak, n in ring:
        cap.write(block, sq, peak, n)
    return cap


def read_block(pipe: IO[bytes]) -> bytes:
    """Read exactly one block from the arecord pipe, or ``b''`` at EOF."""
    buf = b''
    while len(buf) < BLOCK_BYTES:
        chunk = pipe.read(BLOCK_BYTES - len(buf))
        if not chunk:
            return b''
        buf += chunk
    return buf


def run_session(conn: Connection) -> None:
    """Run one capture session: spawn arecord and gate blocks until it dies.

    Raises on arecord exit (device unplugged, ALSA error) so the caller's
    backoff loop restarts the session — which re-pins mixer state, covering
    the USB-replug-resets-the-codec trap by construction.

    Args:
        conn: Open DB connection.
    """
    proc = subprocess.Popen(ARECORD_CMD, stdout=subprocess.PIPE)
    assert proc.stdout is not None
    setpipe = getattr(fcntl, 'F_SETPIPE_SZ', None)
    if setpipe is not None:
        try:
            fcntl.fcntl(proc.stdout.fileno(), setpipe, PIPE_BYTES)
        except OSError:
            pass  # capped by /proc/sys/fs/pipe-max-size; the ALSA buffer still covers stalls

    ring: deque[RingEntry] = deque(maxlen=PREROLL_BLOCKS)
    gate = VoxGate(OPEN_DBFS, CLOSE_DBFS, HANG_BLOCKS, MAX_BLOCKS)
    cap: Capture | None = None
    blocks = captures = 0
    rms_min = rms_max = rms_last = 0.0
    last_heartbeat = last_prune = time.monotonic()
    first_window = True
    try:
        while True:
            block = read_block(proc.stdout)
            if not block:
                raise RuntimeError(f'arecord exited (rc={proc.poll()})')
            sq, peak, n = block_energy(block)
            rms = rms_dbfs(sq, n)
            blocks += 1
            rms_last = rms
            if first_window:
                rms_min = rms_max = rms
                first_window = False
            rms_min = min(rms_min, rms)
            rms_max = max(rms_max, rms)

            event = gate.feed(rms)
            if event is GateEvent.OPEN:
                cap = open_capture(conn, ring)
                captures += 1
            if cap is not None:
                cap.write(block, sq, peak, n)
            ring.append((block, sq, peak, n))
            if event is GateEvent.CLOSE and cap is not None:
                cap.close(conn)
                cap = None

            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                state = 'open' if gate.is_open else 'closed'
                print(
                    f'heartbeat: blocks={blocks} captures={captures} gate={state} '
                    f'rms min/last/max = {rms_min:.1f}/{rms_last:.1f}/{rms_max:.1f} dBFS',
                    flush=True,
                )
                blocks = captures = 0
                first_window = True
                last_heartbeat = now
            if now - last_prune >= PRUNE_INTERVAL_SECONDS:
                prune_audio(conn)
                last_prune = now
    finally:
        if cap is not None:
            cap.close(conn)  # salvage a capture cut short by shutdown/error
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    """Run the recorder loop, re-pinning and restarting the session on failure."""
    conn = get_connection()
    init_db(conn)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    print(
        f'radio recorder started (device={ALSA_DEVICE}, audio={AUDIO_DIR}, '
        f'gate {OPEN_DBFS:g}/{CLOSE_DBFS:g} dBFS)',
        flush=True,
    )
    while True:
        try:
            pin_mixer()
            pin_af()
            prune_audio(conn)
            run_session(conn)
        except KeyboardInterrupt:
            print('radio recorder stopped', flush=True)
            break
        except Exception as exc:
            print(f'recorder error: {exc}, retrying in 5s', file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
