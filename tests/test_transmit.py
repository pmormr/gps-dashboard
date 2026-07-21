"""Tests for the transmit safety rail (``radio/transmit.py``).

The never-stuck-keyed invariant is the load-bearing property here: PTT must be
released however the ``with`` block exits, and the watchdog must force-release a
block that runs long — over its own connection, since the point is independence
from a wedged caller. ``Rigctld`` is faked at the module boundary so every
``set_ptt`` (the keyer's and the watchdog's) lands in one ordered event list.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from api.rigctld import RigctldError
from radio import transmit


def make_fake_rig(events: list[object], fail_unkey: bool = False) -> type:
    """A ``Rigctld`` stand-in whose ``set_ptt`` calls append to ``events``.

    Args:
        events: Shared ordered log; ``True`` = key, ``False`` = unkey.
        fail_unkey: When set, every unkey records then raises, to exercise the
            independent-retry path.
    """

    class FakeRig:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        def __enter__(self) -> FakeRig:
            return self

        def __exit__(self, *_a: object) -> bool:
            return False

        def set_ptt(self, on: bool) -> None:
            events.append(bool(on))
            if not on and fail_unkey:
                raise RigctldError('unkey refused')

    return FakeRig


def test_keys_then_unkeys_on_normal_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(transmit, 'Rigctld', make_fake_rig(events))
    with transmit.keyed_tx():
        events.append('body')
    assert events == [True, 'body', False]


def test_unkeys_when_block_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(transmit, 'Rigctld', make_fake_rig(events))
    with pytest.raises(ValueError):
        with transmit.keyed_tx():
            raise ValueError('playback blew up')
    assert events == [True, False]  # released despite the failure


def test_failed_primary_unkey_retries_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(transmit, 'Rigctld', make_fake_rig(events, fail_unkey=True))
    with transmit.keyed_tx():  # instant block; watchdog never fires
        pass
    # key, primary unkey (raises), then the independent _force_unkey retry
    assert events == [True, False, False]


def test_watchdog_force_unkeys_while_block_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []
    monkeypatch.setattr(transmit, 'Rigctld', make_fake_rig(events))
    with transmit.keyed_tx(max_seconds=0.05):
        time.sleep(0.3)
        # The watchdog (0.05 s) has fired independently of this block's exit.
        assert False in events, 'watchdog should have force-unkeyed mid-block'
    assert events[0] is True


# --- audio preparation: argv builders + soundboard listing ----------------------------


def test_espeak_argv_shape() -> None:
    argv = transmit.espeak_argv('hello KC3HEU', Path('/tmp/out.wav'), voice='en-us', wpm=160)
    assert argv == ['espeak-ng', '-v', 'en-us', '-s', '160', '-w', '/tmp/out.wav', 'hello KC3HEU']


def test_piper_argv_text_on_stdin() -> None:
    # Text is NOT in argv (piper reads stdin) — only model + output.
    argv = transmit.piper_argv(Path('/models/lessac.onnx'), Path('/tmp/out.wav'))
    assert argv == ['piper', '--model', '/models/lessac.onnx', '--output_file', '/tmp/out.wav']


def test_normalize_argv_pins_tx_format() -> None:
    argv = transmit.normalize_argv(Path('/tmp/in.mp3'), Path('/tmp/out.wav'))
    assert argv[:5] == ['ffmpeg', '-nostdin', '-y', '-i', '/tmp/in.mp3']
    # 48 kHz mono S16_LE with the loudnorm filter — the TX contract.
    assert '-ar' in argv and argv[argv.index('-ar') + 1] == '48000'
    assert '-ac' in argv and argv[argv.index('-ac') + 1] == '1'
    assert argv[argv.index('-c:a') + 1] == 'pcm_s16le'
    assert 'loudnorm' in argv[argv.index('-af') + 1]


def test_aplay_argv_targets_digirig() -> None:
    argv = transmit.aplay_argv(Path('/tmp/out.wav'), device='plughw:CARD=Digirig')
    assert argv == ['aplay', '-D', 'plughw:CARD=Digirig', '/tmp/out.wav']


def test_clip_label_humanizes_filename() -> None:
    assert transmit.clip_label('meet_at_camp.wav') == 'meet at camp'
    assert transmit.clip_label('radio-check.mp3') == 'radio-check'


def test_list_soundboard_filters_and_sorts(tmp_path: Path) -> None:
    (tmp_path / 'zebra.wav').write_bytes(b'')
    (tmp_path / 'alpha.mp3').write_bytes(b'')
    (tmp_path / '.hidden.wav').write_bytes(b'')  # dotfile → skipped
    (tmp_path / 'notes.txt').write_bytes(b'')  # wrong type → skipped
    (tmp_path / 'sub').mkdir()  # dir → skipped
    clips = transmit.list_soundboard(tmp_path)
    assert [c.filename for c in clips] == ['alpha.mp3', 'zebra.wav']  # sorted by label
    assert clips[0].label == 'alpha'


def test_list_soundboard_missing_dir_is_empty(tmp_path: Path) -> None:
    assert transmit.list_soundboard(tmp_path / 'nope') == []


def test_render_tts_rejects_empty_text(tmp_path: Path) -> None:
    with pytest.raises(transmit.TransmitError):
        transmit.render_tts('   ', tmp_path / 'out.wav')


def test_render_tts_rejects_unknown_engine(tmp_path: Path) -> None:
    with pytest.raises(transmit.TransmitError):
        transmit.render_tts('hello', tmp_path / 'out.wav', engine='festival')


def test_render_tts_piper_without_model_raises(tmp_path: Path) -> None:
    with pytest.raises(transmit.TransmitError, match='model'):
        transmit.render_tts('hello', tmp_path / 'out.wav', engine='piper', piper_model='')
