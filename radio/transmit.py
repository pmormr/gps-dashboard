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

import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from api.rigctld import Rigctld, RigctldError

#: Hard ceiling on how long PTT may stay asserted for one transmission — a hung
#: playback cannot sit on the air longer than this. Env-overridable in the unit.
MAX_TX_SECONDS = float(os.environ.get('GPS_RADIO_MAX_TX_SECONDS', '120'))

#: Delay after keying before audio starts — the rig needs a beat to come up on TX
#: or it clips the first syllable. Env-overridable.
TX_SETTLE_SECONDS = float(os.environ.get('GPS_RADIO_TX_SETTLE_SECONDS', '0.2'))

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


def piper_argv(model: Path, out: Path) -> list[str]:
    """piper argv rendering to WAV ``out`` (the text is supplied on stdin)."""
    return ['piper', '--model', str(model), '--output_file', str(out)]


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
    text: str, out: Path, *, engine: str = 'espeak', piper_model: str = PIPER_MODEL
) -> Path:
    """Render ``text`` to a normalized 48 kHz mono WAV at ``out``.

    Callsign ID is the operator's responsibility (R11) — bake KC3HEU into the
    text; the console injects nothing.

    Args:
        text: The words to speak.
        out: Destination WAV path for the normalized render.
        engine: ``'espeak'`` (default, robotic) or ``'piper'`` (natural).
        piper_model: Path to the piper voice model; required for the piper engine.

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
        _run_tool(piper_argv(Path(piper_model), raw), stdin_text=text)
    elif engine == 'espeak':
        _run_tool(espeak_argv(text, raw))
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
    wav: Path, *, settle_s: float = TX_SETTLE_SECONDS, max_seconds: float = MAX_TX_SECONDS
) -> None:
    """Key the rig, play ``wav`` out the Digirig, then unkey — via the item-1 guard.

    Keying goes through :func:`keyed_tx`, so PTT is guaranteed released however
    playback exits. aplay is bounded by ``max_seconds`` so a hung player can't
    outlive the watchdog (a clip longer than the cap is truncated, by design).

    Args:
        wav: A normalized WAV to transmit.
        settle_s: Delay after keying before audio starts.
        max_seconds: PTT/playback ceiling (the watchdog bound).

    Raises:
        TransmitError: If playback fails.
        RigctldError: If keying fails.
    """
    with keyed_tx(max_seconds):
        time.sleep(settle_s)
        _run_tool(aplay_argv(wav), timeout=max_seconds)
