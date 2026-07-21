"""Key the rig over the Digirig RTS line and optionally transmit test audio.

The bench test for the RTS hardware-PTT path (:mod:`radio.ptt`) — the non-CI-V
key that still works when CI-V control is locked out, most importantly while the
rig is in cross-band **Repeater Mode** (which NAKs CI-V PTT with ``RPRT -9``).

**Run this AT THE RIG: it keys the transmitter and puts a carrier on the air.**
Its purpose is to answer the one thing the CI-V path can't — does the rig
transmit injected mic-jack audio while it's cross-band repeating? Put the rig in
Repeater Mode, run this, and listen on a second radio:

    # ID'd voice test (default phrase includes the callsign):
    uv run tools/radio_rts_ptt.py --say "This is KC3HEU testing R T S keying"
    # bare carrier, no audio (watch the rig's TX indicator) — ID verbally yourself:
    uv run tools/radio_rts_ptt.py --seconds 3
    # a staged clip:
    uv run tools/radio_rts_ptt.py --wav /mnt/nvme/data/radio-tx/soundboard/foo.wav

STATION ID (§97.119): the default ``--say`` phrase carries KC3HEU. If you pass
``--wav`` or ``--seconds``, you are the control operator and must ID verbally.
"""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path

from common.cli import run_cli
from radio import transmit
from radio.ptt import DIGIRIG_SERIAL_DEVICE, keyed_tx_rts

#: Default spoken phrase — carries the callsign so a voice test is self-ID'd.
DEFAULT_SAY = 'This is KC3HEU testing R T S keying, one two three.'


def _prepare_audio(args: argparse.Namespace, tmp: Path) -> Path | None:
    """Render/normalize the audio to transmit, or ``None`` for a carrier-only run."""
    if args.seconds is not None:
        return None
    if args.wav:
        src = Path(args.wav)
        if args.no_normalize:
            return src
        return transmit.prepare_clip(src, tmp / 'tx.wav')
    return transmit.render_tts(args.say or DEFAULT_SAY, tmp / 'tx.wav', engine='espeak')


def _key_and_transmit(args: argparse.Namespace, wav: Path | None) -> None:
    """Key over RTS, play the audio (or hold a bare carrier), then unkey."""
    with keyed_tx_rts(args.device, max_seconds=args.max_seconds):
        print('KEYED — RTS asserted (the rig should be transmitting)', flush=True)
        time.sleep(args.settle)
        if wav is None:
            hold = min(args.seconds, args.max_seconds)
            print(f'carrier only — holding {hold:.1f}s …', flush=True)
            time.sleep(hold)
        else:
            print('transmitting audio …', flush=True)
            transmit._run_tool(
                transmit.aplay_argv(wav, args.audio_device), timeout=args.max_seconds
            )
    print('UNKEYED — RTS cleared', flush=True)


def main() -> int:
    """Parse args and run one RTS-keyed transmission."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--device',
        default=DIGIRIG_SERIAL_DEVICE,
        help='Digirig serial device (default: %(default)s)',
    )
    parser.add_argument('--say', help=f'TTS phrase to transmit (default: {DEFAULT_SAY!r})')
    parser.add_argument('--wav', help='audio file to transmit instead of TTS')
    parser.add_argument(
        '--seconds', type=float, help='carrier only: key for N s with no audio (ID verbally)'
    )
    parser.add_argument(
        '--settle', type=float, default=0.25, help='delay after keying before audio (s)'
    )
    parser.add_argument(
        '--max-seconds', type=float, default=30.0, help='hard PTT cap / watchdog (s)'
    )
    parser.add_argument(
        '--audio-device', default=transmit.TX_ALSA_DEVICE, help='ALSA playback device'
    )
    parser.add_argument(
        '--no-normalize', action='store_true', help='play --wav as-is (skip loudnorm)'
    )
    args = parser.parse_args()

    if sum([bool(args.say), bool(args.wav), args.seconds is not None]) > 1:
        parser.error('use at most one of --say / --wav / --seconds')
    if args.seconds is not None and args.seconds <= 0:
        parser.error('--seconds must be positive')

    print(
        '*** This keys the transmitter and puts a carrier on the air. '
        'You are the control operator (KC3HEU). ***',
        flush=True,
    )
    with tempfile.TemporaryDirectory() as tmp:
        wav = _prepare_audio(args, Path(tmp))
        _key_and_transmit(args, wav)
    return 0


if __name__ == '__main__':
    run_cli(main)
