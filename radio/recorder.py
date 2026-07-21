"""VOX-gated radio transmission recorder — Digirig SP1 capture → WAV + DB rows.

Standalone daemon, the radio-plane sibling of the GPS logger: a continuous
``arecord`` pipe off the Digirig codec feeds fixed 100 ms blocks through the
pure VOX gate (``radio/vox.py``); each opening becomes a WAV file with a
ring-buffer pre-roll, and each close inserts one GPS-snapped
``radio_transmissions`` row. Design is R8 in ``plans/radio-platform-plan.md``.

Each row also carries a compact ``waveform`` envelope — sub-block peaks
(``WAVEFORM_SUBBLOCKS`` per block, for time resolution past the block cadence)
resampled + absolute-encoded (``radio/waveform.py``) at close. Storing it on the
row means the player/log strip draw with no WAV re-decode and the waveform
outlives the audio the retention pruner drops.

Startup pins state the hardware won't hold for us: the C-Media codec resets to
max gain + AGC ON on every USB replug (the 2a trap), and the rig's AF level
sets the SP1 record level. Both pins re-run on every session (re)start, so a
replug — which kills ``arecord`` and bounces the session — re-pins on recovery
by construction.

Metadata honesty: the rigctld snapshot at gate-open reads the *active main
band only*, while SP1 audio is the A+B mix — a sub-band signal can carry a
wrong freq/mode tag, and ``dcd_main`` records what the main band's squelch
said (sampled at gate-open *and* ~1 Hz across the capture — a squelch that
opens a beat after the VOX trigger still reads true) so readers can judge the
tag's confidence.

Commit rule (2e): the gate discards captures with fewer than
``MIN_LOUD_BLOCKS`` above-threshold blocks — squelch-crackle/beep transients
sit at exactly voice level, so activity, not level, separates them (see the
corpus analysis in ``plans/radio-platform-plan.md``). Captures buffer in RAM
until they cross the rule; a discard never touches disk or the DB.

Levels: AF is the record-level calibration and is re-asserted on **every**
heartbeat, not just session start — the rig forgets CI-V-set levels across a
power cycle. Squelch is operator-owned; the ``LevelKeeper`` (``radio/
levels.py``) remembers the dialed value, restores it after a rig power cycle,
clamps accidental too-high settings, and raises it one bounded step on the
two evidence-backed too-low signatures (flap storm / stuck-open static).
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import wave
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection
from typing import IO

from api.db import get_connection, init_db
from api.rigctld import Rigctld, RigctldError
from common.proc import run
from common.timefmt import age_seconds, format_canonical
from radio.levels import LevelKeeper
from radio.paths import audio_dir, tx_sentinel_path
from radio.vox import GateEvent, VoxGate, amplitude_dbfs, block_energy, rms_dbfs
from radio.waveform import WAVEFORM_BUCKETS, WAVEFORM_SUBBLOCKS, block_subpeaks, build_envelope

SAMPLE_RATE = 48000
BLOCK_SECONDS = 0.1
BLOCK_FRAMES = int(SAMPLE_RATE * BLOCK_SECONDS)
BLOCK_BYTES = BLOCK_FRAMES * 2  # mono S16_LE

PREROLL_BLOCKS = 30  # 3 s of context ahead of the gate opening
HANG_BLOCKS = 20  # 2 s under the close threshold ends a capture
MAX_BLOCKS = 3000  # 5 min hard cap per file (held carrier → consecutive files)

# Field-tunable thresholds. With the USB ground isolator inline the capture
# floor is ~−64 dBFS RMS (loudest floor block ~−58); open-squelch static ~−17.
# OPEN clears the loudest floor block by ~6 dB so the floor never trips it;
# CLOSE stays above the floor too, so floor blocks fall below it and the gate
# still closes. Hysteresis pair, both env-overridable in the unit.
OPEN_DBFS = float(os.environ.get('GPS_RADIO_OPEN_DBFS', '-52'))
CLOSE_DBFS = float(os.environ.get('GPS_RADIO_CLOSE_DBFS', '-56'))
#: Commit rule (2e): a capture keeps only if it holds this many loud blocks
#: (≥ OPEN_DBFS). Corpus-derived: blip transients measured 1–5 loud blocks at
#: exactly voice level, voice 10–12 — level can't separate them, activity can.
#: Trade-off: an isolated one-word transmission (~3–4 blocks) is discarded too.
MIN_LOUD_BLOCKS = int(os.environ.get('GPS_RADIO_MIN_LOUD_BLOCKS', '6'))

ALSA_DEVICE = os.environ.get('GPS_RADIO_ALSA_DEVICE', 'plughw:CARD=Digirig')
MIXER_CARD = os.environ.get('GPS_RADIO_MIXER_CARD', 'Digirig')
#: ``control:value`` pairs pinned via amixer at session start. Control names are
#: env-overridable so a codec-revision rename is a unit edit, not a code change.
MIXER_SETS = os.environ.get('GPS_RADIO_MIXER_SETS', 'Auto Gain Control:off,Mic:10')
#: Rig AF level, re-asserted every heartbeat (the record-level calibration —
#: see the unit file for the calibration note); '' disables.
PIN_AF = os.environ.get('GPS_RADIO_PIN_AF', '0.25')
#: Squelch guard rails (radio/levels.py): SQL readings above SANE_MAX are
#: clamped back as accidents ('' disables); GUARD_DISCARDS discards per
#: rolling 10-minute window triggers a bounded flap-storm raise (0 disables),
#: never above GUARD_MAX_SQL.
SQL_SANE_MAX = os.environ.get('GPS_RADIO_SQL_SANE_MAX', '0.5')
GUARD_DISCARDS = int(os.environ.get('GPS_RADIO_GUARD_DISCARDS', '15'))
GUARD_MAX_SQL = float(os.environ.get('GPS_RADIO_GUARD_MAX_SQL', '0.35'))

AUDIO_DIR = audio_dir()
MAX_AGE_DAYS = float(os.environ.get('GPS_RADIO_AUDIO_MAX_DAYS', '180'))
MAX_BYTES = int(float(os.environ.get('GPS_RADIO_AUDIO_MAX_GB', '4')) * 1024**3)

GPS_SNAP_MAX_AGE_SECONDS = 300
RIGCTLD_TIMEOUT_SECONDS = 0.75
#: Blocks between DCD polls while a capture is active (~1 s) — squelch state is
#: sampled across the capture, not just at gate-open, so ``dcd_main`` no longer
#: misses a squelch that opens a beat after the VOX trigger.
DCD_POLL_BLOCKS = 10
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
    """One in-progress transmission, RAM-buffered until it earns its file.

    Blocks accumulate in ``buffer`` until the gate's loud count crosses the
    commit rule (``MIN_LOUD_BLOCKS``); :meth:`materialize` then opens the
    ``.part`` WAV and flushes. A capture the gate discards before that leaves
    no trace on disk or in the DB. Memory stays bounded: an unmaterialized
    capture either crosses the rule or hang-closes within a few seconds.
    """

    part_path: Path
    rel_path: str
    started: datetime
    freq_hz: int | None
    mode: str | None
    dcd_main: int | None
    lat: float | None
    lon: float | None
    writer: wave.Wave_write | None = None
    buffer: list[RingEntry] = field(default_factory=list)
    peaks: list[int] = field(default_factory=list)
    dcd_poll_ok: bool = True
    frames: int = 0
    sq_sum: int = 0
    peak: int = 0

    def write(self, block: bytes, sq: int, peak: int, n: int) -> None:
        """Append one block (to the WAV or the RAM buffer) and fold its stats.

        The waveform envelope accumulates ``WAVEFORM_SUBBLOCKS`` peaks per block
        (sub-block sampling) so its time resolution beats the VOX block cadence;
        the whole-block ``peak`` still feeds the ``peak_dbfs`` stat.
        """
        if self.writer is None:
            self.buffer.append((block, sq, peak, n))
        else:
            self.writer.writeframesraw(block)
        self.sq_sum += sq
        self.peak = max(self.peak, peak)
        self.peaks.extend(block_subpeaks(block, WAVEFORM_SUBBLOCKS))
        self.frames += n

    def materialize(self) -> None:
        """Open the ``.part`` WAV and flush the buffered blocks into it."""
        self.part_path.parent.mkdir(parents=True, exist_ok=True)
        self.writer = wave.open(str(self.part_path), 'wb')
        self.writer.setnchannels(1)
        self.writer.setsampwidth(2)
        self.writer.setframerate(SAMPLE_RATE)
        for block, _, _, _ in self.buffer:
            self.writer.writeframesraw(block)
        self.buffer.clear()

    def discard(self) -> None:
        """Abandon an in-progress capture: close and delete any partial file.

        No DB row is written. Used when the recorder must drop what it was
        tracking outside the normal gate close — e.g. the operator's own transmit
        keyed mid-capture (the loopback must not be logged as received).
        """
        if self.writer is not None:
            self.writer.close()
            self.writer = None
            self.part_path.unlink(missing_ok=True)

    def note_dcd(self, dcd: bool) -> None:
        """Fold one squelch reading in: any open reading makes ``dcd_main`` 1."""
        self.dcd_main = 1 if dcd else (self.dcd_main or 0)

    def close(self, conn: Connection) -> None:
        """Finalize the WAV (rename ``.part`` away) and insert the DB row.

        Only a materialized capture closes — the gate's commit rule guarantees
        a CLOSE event follows a loud count that already triggered materialize.
        """
        assert self.writer is not None
        self.writer.close()
        final = AUDIO_DIR / self.rel_path
        self.part_path.rename(final)
        duration = self.frames / SAMPLE_RATE
        ended = self.started + timedelta(seconds=duration)
        waveform = json.dumps(build_envelope(self.peaks, WAVEFORM_BUCKETS))
        conn.execute(
            'INSERT INTO radio_transmissions '
            '(started_utc, ended_utc, duration_s, freq_hz, mode, dcd_main, '
            'peak_dbfs, rms_dbfs, audio_path, lat, lon, waveform) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                format_canonical(self.started),
                format_canonical(ended),
                round(duration, 3),
                self.freq_hz,
                self.mode,
                self.dcd_main,
                round(amplitude_dbfs(float(self.peak)), 1),
                round(rms_dbfs(self.sq_sum, self.frames), 1),
                self.rel_path,
                self.lat,
                self.lon,
                waveform,
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
    if age_seconds(str(row['timestamp'])) > GPS_SNAP_MAX_AGE_SECONDS:
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


def heartbeat_levels(keeper: LevelKeeper, announce: bool = False) -> bool:
    """One best-effort rigctld levels pass: re-pin AF, read SQL, apply the keeper.

    Runs at session start and on every heartbeat — AF must be *continuously*
    asserted because a rig power cycle silently reverts CI-V-set levels.

    Args:
        keeper: The squelch state machine; its returned action is applied.
        announce: Log the successful AF pin (session start / recovery only,
            so a powered-off rig doesn't spam the journal once a minute).

    Returns:
        True when the rig was reachable.
    """
    try:
        with Rigctld(timeout=RIGCTLD_TIMEOUT_SECONDS) as rig:
            if PIN_AF:
                rig.set_level('AF', float(PIN_AF))
            sql = rig.get_level('SQL')
            action = keeper.heartbeat(sql)
            if action is not None and sql is not None:
                reason, value = action
                rig.set_level('SQL', value)
                print(f'squelch {reason}: SQL {sql:.3f} -> {value:.3f}', flush=True)
    except RigctldError as exc:
        keeper.heartbeat(None)
        if announce:
            print(f'levels unpinned (rig unreachable): {exc}', file=sys.stderr, flush=True)
        return False
    if announce and PIN_AF:
        print(f'AF pinned to {PIN_AF}', flush=True)
    return True


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
    """Start a capture: snapshot metadata and buffer the pre-roll — no file yet.

    The ``.part`` WAV is created later by :meth:`Capture.materialize`, once the
    gate's loud count crosses the commit rule; a gate that discards first never
    touches the disk.

    Args:
        conn: Open DB connection (for the GPS snap).
        ring: The pre-roll ring buffer; its contents become the capture's head.

    Returns:
        The in-progress :class:`Capture`.
    """
    started = datetime.now(UTC) - timedelta(seconds=len(ring) * BLOCK_SECONDS)
    rel = audio_rel_path(started)
    freq, mode, dcd = rig_snapshot()
    lat, lon = gps_snap(conn)
    cap = Capture(
        part_path=AUDIO_DIR / (rel + '.part'),
        rel_path=rel,
        started=started,
        freq_hz=freq,
        mode=mode,
        dcd_main=dcd,
        lat=lat,
        lon=lon,
    )
    for block, sq, peak, n in ring:
        cap.write(block, sq, peak, n)
    return cap


def poll_dcd(cap: Capture) -> None:
    """Best-effort mid-capture squelch poll folded into ``dcd_main``.

    One failed poll disables polling for the rest of the capture, so an
    unreachable rigctld costs a single timeout — metadata must never starve
    the audio pipe.

    Args:
        cap: The active capture.
    """
    if not cap.dcd_poll_ok:
        return
    try:
        with Rigctld(timeout=RIGCTLD_TIMEOUT_SECONDS) as rig:
            dcd = rig.get_dcd()
    except RigctldError:
        cap.dcd_poll_ok = False
        return
    if dcd is not None:
        cap.note_dcd(bool(dcd))


def read_block(pipe: IO[bytes]) -> bytes:
    """Read exactly one block from the arecord pipe, or ``b''`` at EOF."""
    buf = b''
    while len(buf) < BLOCK_BYTES:
        chunk = pipe.read(BLOCK_BYTES - len(buf))
        if not chunk:
            return b''
        buf += chunk
    return buf


def run_session(conn: Connection, keeper: LevelKeeper) -> None:
    """Run one capture session: spawn arecord and gate blocks until it dies.

    Raises on arecord exit (device unplugged, ALSA error) so the caller's
    backoff loop restarts the session — which re-pins mixer state, covering
    the USB-replug-resets-the-codec trap by construction.

    Args:
        conn: Open DB connection.
        keeper: The squelch state machine (outlives sessions, so operator
            memory survives arecord bounces).
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
    gate = VoxGate(OPEN_DBFS, CLOSE_DBFS, HANG_BLOCKS, MAX_BLOCKS, MIN_LOUD_BLOCKS)
    tx_sentinel = tx_sentinel_path()
    cap: Capture | None = None
    blocks = captures = discarded = 0
    rms_min = rms_max = rms_last = 0.0
    last_heartbeat = last_prune = time.monotonic()
    first_window = True
    levels_ok = True
    try:
        while True:
            block = read_block(proc.stdout)
            if not block:
                raise RuntimeError(f'arecord exited (rc={proc.poll()})')
            if tx_sentinel.exists():
                # Our own transmit is keyed — suppress recording so the rig's
                # monitor/sidetone loopback is never logged as a received
                # transmission; the TX path logs the clean source itself (R11).
                if cap is not None:
                    cap.discard()
                    cap = None
                gate.reset()
                ring.clear()
                continue
            sq, peak, n = block_energy(block)
            rms = rms_dbfs(sq, n)
            keeper.feed_block(rms)
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
            if cap is not None:
                cap.write(block, sq, peak, n)
                if cap.writer is None and gate.loud_blocks >= MIN_LOUD_BLOCKS:
                    cap.materialize()
                if blocks % DCD_POLL_BLOCKS == 0:
                    poll_dcd(cap)
            ring.append((block, sq, peak, n))
            if event is GateEvent.CLOSE and cap is not None:
                cap.close(conn)
                captures += 1
                cap = None
            elif event is GateEvent.DISCARD and cap is not None:
                discarded += 1
                keeper.note_discard()
                cap = None

            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_SECONDS:
                levels_ok = heartbeat_levels(keeper, announce=not levels_ok)
                state = 'open' if gate.is_open else 'closed'
                print(
                    f'heartbeat: blocks={blocks} captures={captures} discarded={discarded} '
                    f'gate={state} '
                    f'rms min/last/max = {rms_min:.1f}/{rms_last:.1f}/{rms_max:.1f} dBFS',
                    flush=True,
                )
                blocks = captures = discarded = 0
                first_window = True
                last_heartbeat = now
            if now - last_prune >= PRUNE_INTERVAL_SECONDS:
                prune_audio(conn)
                last_prune = now
    finally:
        # Salvage a materialized capture cut short by shutdown/error; a capture
        # still below the commit rule is, by that rule, a discard.
        if cap is not None and cap.writer is not None:
            cap.close(conn)
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
    keeper = LevelKeeper(
        open_dbfs=OPEN_DBFS,
        sane_max=float(SQL_SANE_MAX) if SQL_SANE_MAX else None,
        guard_discards=GUARD_DISCARDS,
        guard_max_sql=GUARD_MAX_SQL,
    )
    print(
        f'radio recorder started (device={ALSA_DEVICE}, audio={AUDIO_DIR}, '
        f'gate {OPEN_DBFS:g}/{CLOSE_DBFS:g} dBFS, commit >= {MIN_LOUD_BLOCKS} loud, '
        f'sql guard {GUARD_DISCARDS} discards / max {GUARD_MAX_SQL:g})',
        flush=True,
    )
    while True:
        try:
            pin_mixer()
            heartbeat_levels(keeper, announce=True)
            prune_audio(conn)
            run_session(conn, keeper)
        except KeyboardInterrupt:
            print('radio recorder stopped', flush=True)
            break
        except Exception as exc:
            print(f'recorder error: {exc}, retrying in 5s', file=sys.stderr, flush=True)
            time.sleep(5)


if __name__ == '__main__':
    main()
