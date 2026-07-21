"""Transmit plane — key the Icom ID-5100A for the announce/soundboard console.

The TX sibling of :mod:`radio.recorder`. Phase 3 (R11 in
``plans/radio-platform-plan.md``) is **operator-clicked only**: every transmission
is a ``/radio`` button press by the licensed control op (KC3HEU), so there is no
scheduler and no automatic-control exposure.

This module owns the **never-stuck-keyed invariant**. :func:`keyed_tx` is the one
sanctioned way to assert PTT: it releases in a ``finally`` no matter how the block
exits, and it arms an independent watchdog — on its *own* rigctld connection, so a
playback wedged inside the block while holding the main connection still gets
unkeyed — that force-releases after ``max_seconds``. A stuck transmitter is
illegal, jams the channel, and cooks the finals. Belt-and-suspenders beyond
software: set the rig's own time-out timer (TOT) as a hardware backstop.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import wave
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from sqlite3 import Connection

from api.rigctld import Rigctld, RigctldError
from common.timefmt import format_canonical
from radio.paths import audio_dir, tx_sentinel_path
from radio.vox import amplitude_dbfs, block_energy, rms_dbfs
from radio.waveform import (
    WAVEFORM_BUCKETS,
    WAVEFORM_SUBBLOCKS,
    block_subpeaks,
    build_envelope,
)

#: Hard ceiling on how long PTT may stay asserted for one transmission — a hung
#: playback cannot sit on the air longer than this. Env-overridable in the unit.
MAX_TX_SECONDS = float(os.environ.get('GPS_RADIO_MAX_TX_SECONDS', '120'))

#: Default delay after keying before audio starts — the receiving rigs need a beat
#: to open squelch or they clip the first word. Field-measured ~250 ms; the UI
#: overrides it per transmission, this is the fallback + env default.
TX_SETTLE_SECONDS = float(os.environ.get('GPS_RADIO_TX_SETTLE_SECONDS', '0.25'))

#: ALSA playback device (the Digirig codec's playback substream; full-duplex, so
#: it coexists with the recorder's dsnoop capture). ``plughw`` handles conversion.
TX_ALSA_DEVICE = os.environ.get('GPS_RADIO_TX_ALSA_DEVICE', 'plughw:CARD=Digirig')

#: espeak-ng voice + speed (the robotic default engine).
ESPEAK_VOICE = os.environ.get('GPS_RADIO_ESPEAK_VOICE', 'en-us')
ESPEAK_WPM = int(os.environ.get('GPS_RADIO_ESPEAK_WPM', '160'))

#: piper voice-model path; '' disables the piper engine (espeak still works). The
#: model is a manual NVMe install like the MediaMTX binary — not carried by uv sync.
PIPER_MODEL = os.environ.get('GPS_RADIO_PIPER_MODEL', '')

#: One-pass EBU R128 loudness normalize + true-peak ceiling, applied to every TTS
#: render and soundboard clip: a quiet clip and a loud phrase hit the *same*
#: deviation (the item-5 rig-mic-gain calibration pins to it), and the −1.5 dBFS
#: true-peak ceiling keeps the DAC from clipping into splatter. Env-overridable.
TX_LOUDNORM = os.environ.get('GPS_RADIO_TX_LOUDNORM', 'loudnorm=I=-16:TP=-1.5:LRA=11')

#: Soundboard file types the console lists; ffmpeg normalizes them all to the TX
#: format, so non-WAV sources are free.
SOUNDBOARD_EXTS = ('.wav', '.mp3', '.ogg', '.m4a', '.flac')

#: A keying context-manager factory: called ``keyer(max_seconds=...)`` and used as
#: a ``with`` block that holds PTT for its duration. :func:`keyed_tx` (CI-V, this
#: module) and :func:`radio.ptt.keyed_tx_rts` (RTS hardware) both satisfy it.
Keyer = Callable[..., AbstractContextManager[None]]


def _force_unkey() -> None:
    """Watchdog action: drop PTT over a *fresh* rigctld connection, best-effort.

    Runs on the timer thread, so it must never touch the keyer's own connection —
    independence is the whole point, since the main thread may be wedged inside
    playback still holding its socket. Swallows its own errors (there is nothing
    left to fall back to); the rig's TOT is the hardware last resort.
    """
    try:
        with Rigctld() as rig:
            rig.set_ptt(False)
        print(
            'TX watchdog: force-unkeyed (transmission exceeded the cap)',
            file=sys.stderr,
            flush=True,
        )
    except RigctldError as exc:
        print(f'TX watchdog: force-unkey FAILED: {exc}', file=sys.stderr, flush=True)


@contextmanager
def keyed_tx(max_seconds: float = MAX_TX_SECONDS) -> Iterator[None]:
    """Hold the transmitter keyed for the ``with`` block; guaranteed unkeyed after.

    PTT is asserted on entry and released in a ``finally`` however the block exits
    (return, exception, or the watchdog). If the primary unkey itself fails, an
    independent :func:`_force_unkey` runs over a fresh connection before the
    watchdog is cancelled — the safety net is never dropped on a failed release.

    Args:
        max_seconds: Watchdog ceiling — PTT is force-released if the block runs
            past this, independently of the block's own control flow.

    Yields:
        Control to the caller with the rig on the air.

    Raises:
        RigctldError: If *keying* fails (nothing is left asserted in that case);
            a failed *unkey* is handled internally, not raised.
    """
    watchdog = threading.Timer(max_seconds, _force_unkey)
    with Rigctld() as rig:
        rig.set_ptt(True)
        watchdog.start()
        try:
            yield
        finally:
            try:
                rig.set_ptt(False)
            except RigctldError as exc:
                print(
                    f'TX unkey failed on the primary connection: {exc}; retrying independently',
                    file=sys.stderr,
                    flush=True,
                )
                _force_unkey()
            finally:
                watchdog.cancel()


# --- audio preparation: pure argv builders + soundboard listing -----------------------


class TransmitError(RuntimeError):
    """A render/normalize/playback step failed (missing tool, nonzero exit, timeout)."""


@dataclass(frozen=True)
class SoundboardClip:
    """One staged soundboard file the console can transmit.

    Attributes:
        filename: The file's name within the soundboard dir — the id a TX request
            names (re-validated against the dir before use, never trusted raw).
        label: Display label derived from the filename.
    """

    filename: str
    label: str


def clip_label(filename: str) -> str:
    """Human label for a soundboard file: drop the extension, underscores → spaces."""
    return Path(filename).stem.replace('_', ' ').strip()


def list_soundboard(directory: Path) -> list[SoundboardClip]:
    """List staged soundboard clips (supported audio files), sorted by label.

    Args:
        directory: The soundboard dir; a missing dir yields an empty list.

    Returns:
        One entry per audio file (dotfiles and other types skipped), sorted
        case-insensitively by label.
    """
    if not directory.is_dir():
        return []
    clips = [
        SoundboardClip(p.name, clip_label(p.name))
        for p in directory.iterdir()
        if p.is_file() and not p.name.startswith('.') and p.suffix.lower() in SOUNDBOARD_EXTS
    ]
    return sorted(clips, key=lambda c: c.label.lower())


def espeak_argv(
    text: str, out: Path, voice: str = ESPEAK_VOICE, wpm: int = ESPEAK_WPM
) -> list[str]:
    """espeak-ng argv rendering ``text`` to WAV ``out`` (the robotic default voice)."""
    return ['espeak-ng', '-v', voice, '-s', str(wpm), '-w', str(out), text]


def piper_argv(model: Path, out: Path, length_scale: float = 1.0) -> list[str]:
    """piper argv rendering to WAV ``out`` (the text is supplied on stdin).

    ``length_scale`` stretches the timing — >1 is slower — the piper analogue of
    espeak's words-per-minute (``length_scale = 1 / rate``).
    """
    return [
        'piper',
        '--model',
        str(model),
        '--length-scale',
        f'{length_scale:g}',
        '--output_file',
        str(out),
    ]


def normalize_argv(src: Path, dst: Path, loudnorm: str = TX_LOUDNORM) -> list[str]:
    """ffmpeg argv: loudness-normalize ``src`` → 48 kHz mono S16_LE WAV ``dst``."""
    # fmt: off
    return [
        'ffmpeg', '-nostdin', '-y', '-i', str(src),
        '-af', loudnorm, '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(dst),
    ]
    # fmt: on


def aplay_argv(wav: Path, device: str = TX_ALSA_DEVICE) -> list[str]:
    """aplay argv playing ``wav`` out the Digirig playback substream."""
    return ['aplay', '-D', device, str(wav)]


# --- orchestration: thin subprocess wrappers over the builders ------------------------


def _run_tool(
    argv: list[str], *, stdin_text: str | None = None, timeout: float | None = None
) -> None:
    """Run one audio tool, mapping a missing binary / nonzero exit / timeout to TransmitError.

    Args:
        argv: The command and its arguments.
        stdin_text: Text piped to the tool's stdin (piper reads its text there).
        timeout: Kill and raise after this many seconds.

    Raises:
        TransmitError: The binary is absent, exited nonzero, or timed out.
    """
    try:
        result = subprocess.run(
            argv, input=stdin_text, capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError as exc:
        raise TransmitError(f'{argv[0]} not installed') from exc
    except subprocess.TimeoutExpired as exc:
        raise TransmitError(f'{argv[0]} timed out after {timeout}s') from exc
    if result.returncode != 0:
        raise TransmitError(f'{argv[0]} failed (rc={result.returncode}): {result.stderr.strip()}')


def render_tts(
    text: str,
    out: Path,
    *,
    engine: str = 'espeak',
    piper_model: str = PIPER_MODEL,
    rate: float = 1.0,
) -> Path:
    """Render ``text`` to a normalized 48 kHz mono WAV at ``out``.

    Callsign ID is the operator's responsibility (R11) — bake KC3HEU into the
    text; the console injects nothing.

    Args:
        text: The words to speak.
        out: Destination WAV path for the normalized render.
        engine: ``'espeak'`` (default, robotic) or ``'piper'`` (natural).
        piper_model: Path to the piper voice model; required for the piper engine.
        rate: Speech-speed multiplier — 1.0 is each engine's normal, 0.5 half
            speed. Maps to espeak words-per-minute (``ESPEAK_WPM * rate``) and to
            piper's length scale (``1 / rate``).

    Returns:
        ``out``.

    Raises:
        TransmitError: Empty text, an unknown engine, a piper engine with no
            model, or a tool failure.
    """
    if not text.strip():
        raise TransmitError('nothing to say (empty text)')
    raw = out.with_suffix('.raw.wav')
    if engine == 'piper':
        if not piper_model:
            raise TransmitError('piper engine selected but no voice model configured')
        _run_tool(piper_argv(Path(piper_model), raw, length_scale=1.0 / rate), stdin_text=text)
    elif engine == 'espeak':
        _run_tool(espeak_argv(text, raw, wpm=max(1, round(ESPEAK_WPM * rate))))
    else:
        raise TransmitError(f'unknown TTS engine {engine!r}')
    _run_tool(normalize_argv(raw, out))
    raw.unlink(missing_ok=True)
    return out


def prepare_clip(src: Path, out: Path) -> Path:
    """Normalize a soundboard file ``src`` to a TX-ready WAV ``out``."""
    _run_tool(normalize_argv(src, out))
    return out


def transmit_wav(
    wav: Path,
    *,
    settle_s: float = TX_SETTLE_SECONDS,
    max_seconds: float = MAX_TX_SECONDS,
    keyer: Keyer = keyed_tx,
) -> None:
    """Key the rig, play ``wav`` out the Digirig, then unkey — via a keyer guard.

    ``keyer`` selects the PTT path: the default CI-V :func:`keyed_tx`, or
    :func:`radio.ptt.keyed_tx_rts` for RTS hardware keying — the only path that
    works while the rig is in cross-band Repeater Mode, which NAKs CI-V PTT.
    Either keyer guarantees PTT is released however playback exits; aplay is
    bounded by ``max_seconds`` so a hung player can't outlive the watchdog (a
    clip longer than the cap is truncated, by design).

    Args:
        wav: A normalized WAV to transmit.
        settle_s: Delay after keying before audio starts.
        max_seconds: PTT/playback ceiling (the watchdog bound).
        keyer: The keying context-manager factory (called ``keyer(max_seconds=...)``).

    Raises:
        TransmitError: If playback fails.
        RigctldError: If CI-V keying fails.
        OSError: If RTS keying fails (the Digirig serial device won't open/toggle).
    """
    with keyer(max_seconds=max_seconds):
        time.sleep(settle_s)
        _run_tool(aplay_argv(wav), timeout=max_seconds)


# --- self-TX logging: sentinel + clean-source archive + row ---------------------------


@contextmanager
def tx_active() -> Iterator[None]:
    """Mark self-TX in progress so the recorder suppresses logging its loopback.

    Creates the sentinel the recorder polls (:func:`radio.paths.tx_sentinel_path`)
    and removes it on exit however the block ends — so a failed transmit can never
    wedge the recorder permanently off.
    """
    sentinel = tx_sentinel_path()
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.touch()
    try:
        yield
    finally:
        sentinel.unlink(missing_ok=True)


def tx_rel_path(started: datetime) -> str:
    """Relative archive path for a TX recording: ``tx/YYYY-MM/....wav``.

    A ``tx/`` subtree keeps operator transmissions visually separate from RX
    captures on disk; both live under the audio root, so the retention pruner and
    the audio route cover them the same way.

    Args:
        started: UTC start of the transmission.

    Returns:
        The relative path (month subdir, ms-distinct name).
    """
    return started.strftime('tx/%Y-%m/%Y%m%d-%H%M%S') + f'-{started.microsecond // 1000:03d}.wav'


def wav_envelope(path: Path) -> tuple[float, float, float, list[int]]:
    """Derive ``(duration_s, peak_dbfs, rms_dbfs, waveform)`` from a mono S16 WAV.

    Decodes the clean source we transmitted so a TX row carries the same stats and
    waveform strip as an RX capture — reusing the recorder's per-block peak/energy
    and the shared envelope builder, but over the whole file at once rather than
    the recorder's incremental path.

    Args:
        path: A mono S16_LE WAV (the normalized audio that was transmitted).

    Returns:
        Duration in seconds, whole-file peak/RMS in dBFS, and the 0..255 envelope.
    """
    with wave.open(str(path), 'rb') as w:
        n_frames = w.getnframes()
        rate = w.getframerate()
        raw = w.readframes(n_frames)
    block_bytes = max(2, int(rate * 0.1) * 2)  # 100 ms blocks, matching the recorder cadence
    peak = sq_sum = total = 0
    peaks: list[int] = []
    for i in range(0, len(raw), block_bytes):
        block = raw[i : i + block_bytes]
        sq, blk_peak, n = block_energy(block)
        sq_sum += sq
        peak = max(peak, blk_peak)
        total += n
        peaks.extend(block_subpeaks(block, WAVEFORM_SUBBLOCKS))
    duration = n_frames / rate if rate else 0.0
    return (
        duration,
        round(amplitude_dbfs(float(peak)), 1),
        round(rms_dbfs(sq_sum, total), 1),
        build_envelope(peaks, WAVEFORM_BUCKETS),
    )


def archive_and_log(
    conn: Connection,
    played_wav: Path,
    *,
    started: datetime,
    freq_hz: int | None,
    mode: str | None,
    lat: float | None,
    lon: float | None,
    freq_b_hz: int | None = None,
) -> int:
    """Archive a transmitted WAV under the audio root and insert its log row.

    The row is ``is_tx=1`` and carries the clean source audio plus a waveform
    built from it, so the unified ``/radio`` log renders a transmission exactly
    like a received capture. ``dcd_main`` is NULL — squelch is meaningless on TX.

    Args:
        conn: Open DB connection.
        played_wav: The normalized WAV that was transmitted (the clean source).
        started: UTC time the transmission began.
        freq_hz: Active/main-band frequency at transmit time, or ``None``.
        mode: Active/main-band mode, or ``None``.
        lat: GPS-snapped latitude, or ``None`` when stale/absent.
        lon: GPS-snapped longitude, or ``None`` when stale/absent.
        freq_b_hz: The second band's frequency for a cross-band Repeater-Mode
            transmission (it went out on both), or ``None`` for a single band.

    Returns:
        The new row's id.
    """
    duration, peak_dbfs, rms_val, waveform = wav_envelope(played_wav)
    rel = tx_rel_path(started)
    dest = audio_dir() / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(played_wav, dest)
    ended = started + timedelta(seconds=duration)
    cur = conn.execute(
        'INSERT INTO radio_transmissions '
        '(started_utc, ended_utc, duration_s, freq_hz, mode, dcd_main, '
        'peak_dbfs, rms_dbfs, audio_path, lat, lon, waveform, is_tx, freq_b_hz) '
        'VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 1, ?)',
        (
            format_canonical(started),
            format_canonical(ended),
            round(duration, 3),
            freq_hz,
            mode,
            peak_dbfs,
            rms_val,
            rel,
            lat,
            lon,
            json.dumps(waveform),
            freq_b_hz,
        ),
    )
    conn.commit()
    assert cur.lastrowid is not None  # guaranteed by a successful INSERT
    return cur.lastrowid
