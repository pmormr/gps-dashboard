"""Radio control routes — drive the Icom ID-5100A through a local ``rigctld``.

Read endpoints poll the rig's live state; write endpoints push freq/mode/tone/
repeater changes. Everything goes through :mod:`api.rigctld` (rigctld's TCP text
protocol) — no serial access here. Controls the **active main band only**:
the backend can't read which VFO is active, so a dual-band readout would have to flip
the radio's active VFO each poll. See ``plans/radio-platform-plan.md`` (R6).

Also serves the recorded-transmission log (``radio_transmissions`` rows +
their WAVs, written by the ``radio-recorder`` daemon) — a DB/filesystem read
path with no rigctld involvement.
"""

import json
import tempfile
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

from api.db import get_connection
from api.params import error, parse_limit
from api.rigctld import Rigctld, RigctldError
from common import proc
from radio import transmit
from radio.paths import audio_dir, soundboard_dir
from radio.recorder import gps_snap, rig_snapshot

radio_bp = Blueprint('radio', __name__)

SERVICE = 'radio-control'

#: Serializes transmits so a double-click can't key twice or interleave audio.
#: The app runs single-process (``python api/app.py``), so an in-process lock
#: suffices; the sentinel file (:func:`radio.transmit.tx_active`) handles the
#: separate recorder process.
_TX_LOCK = threading.Lock()

# Modes Hamlib reports for the ID-5100 (1a dump_caps). The route validates against
# this set so ``set_mode`` never forwards arbitrary text to the rig.
RADIO_MODES = ('FM', 'FMN', 'AM', 'AMN', 'D-STAR')

# CI-V tone modes → the (TONE, TSQL) function flags the backend toggles. "tone"
# transmits a CTCSS tone (repeater access); "tsql" also gates RX on a matching tone.
TONE_MODES: dict[str, tuple[bool, bool]] = {
    'off': (False, False),
    'tone': (True, False),
    'tsql': (False, True),
}

# UI shift name ↔ the token Hamlib's set/get_rptr_shift uses.
SHIFT_TO_RIG = {'simplex': 'None', 'plus': '+', 'minus': '-'}
RIG_TO_SHIFT = {'None': 'simplex', '+': 'plus', '-': 'minus'}

# Settable levels (normalized 0..1) → the Hamlib level name. The whitelist keeps
# ``set_level`` from forwarding arbitrary names to the rig.
LEVELS = {'af': 'AF', 'sql': 'SQL', 'rfpower': 'RFPOWER'}

# CI-V cmd 07 D0/D1: make the A/B band the Main band (manual §13-17). Raw frames —
# the Hamlib backend has no band-targeting model. Set-only: the active band is still
# unreadable (no get-VFO), but an explicit set pins it before any read/write.
BAND_SELECT = {'a': b'\x07\xd0', 'b': b'\x07\xd1'}

# CI-V cmd 16 59: single watch (00) / dualwatch (01), send+read (manual §13-17).
# Raw frames like band select — not modeled by the Hamlib backend. Single watch
# mutes the sub band everywhere, including the recorder's SP1 (A+B) feed.
DUALWATCH = b'\x16\x59'

# Transmission-log columns returned to the client. audio_path stays server-side
# (the client fetches bytes by id through the audio route); has_audio tells the
# UI whether a play control makes sense — the retention pruner NULLs audio_path
# but keeps the row. waveform is a JSON-string envelope decoded to a real array
# below, and survives the prune (drawn even once the WAV is gone).
_TX_COLUMNS = (
    'id, started_utc, ended_utc, duration_s, freq_hz, mode, dcd_main, '
    'peak_dbfs, rms_dbfs, lat, lon, waveform, is_tx, audio_path IS NOT NULL AS has_audio'
)


def _tone_mode(tone: bool | None, tsql: bool | None) -> str:
    """Collapse the (TONE, TSQL) flags into a single UI tone mode."""
    if tsql:
        return 'tsql'
    if tone:
        return 'tone'
    return 'off'


def _apply(action: Callable[[Rigctld], None]):
    """Run a rigctld write inside a connection, mapping failures to HTTP status.

    A missing/garbled daemon (``rprt is None``) is 503 (service unavailable); a rig
    that refused the command (a real ``RPRT`` code) is 502 (bad upstream).
    """
    try:
        with Rigctld() as rig:
            action(rig)
    except RigctldError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), (503 if exc.rprt is None else 502)
    return jsonify({'ok': True})


@radio_bp.get('/api/radio/status')
def status():
    """Live main-band state read from the rig via rigctld.

    Returns freq/mode/S-meter plus the tone and repeater settings and the rig's
    DCD/PTT flags. When rigctld is unreachable (cable unplugged or the service
    disabled), returns 503 with the systemd service state so the page can render an
    honest "offline" head instead of erroring.
    """
    try:
        with Rigctld() as rig:
            freq = rig.get_freq()
            mode, passband = rig.get_mode()
            rawstr = rig.get_level('RAWSTR')
            levels = {key: rig.get_level(name) for key, name in LEVELS.items()}
            tone_tenths = rig.get_ctcss_tone()
            tone_mode = _tone_mode(rig.get_func('TONE'), rig.get_func('TSQL'))
            shift = rig.get_rptr_shift()
            offs = rig.get_rptr_offs()
            dcd = rig.get_dcd()
            ptt = rig.get_ptt()
            dualwatch = rig.get_dualwatch()
    except RigctldError as exc:
        return jsonify({'online': False, 'service': proc.service_state(SERVICE), 'error': str(exc)})

    return jsonify(
        {
            'online': True,
            'freq_hz': freq,
            'mode': mode,
            'passband_hz': passband,
            'rawstr': rawstr,
            'levels': levels,
            'tone_mode': tone_mode,
            'ctcss_tone_hz': round(tone_tenths / 10.0, 1) if tone_tenths else None,
            'rptr_shift': RIG_TO_SHIFT.get(shift or 'None', 'simplex'),
            'rptr_offset_hz': offs,
            'dcd': dcd,
            'ptt': ptt,
            'dualwatch': dualwatch,
        }
    )


@radio_bp.post('/api/radio/freq')
def set_freq():
    """Tune the active band. Body: ``{"hz": <positive number>}``."""
    data = request.get_json(silent=True) or {}
    hz = data.get('hz')
    if not isinstance(hz, (int, float)) or isinstance(hz, bool) or hz <= 0:
        return error("'hz' must be a positive number", 400)
    return _apply(lambda rig: rig.set_freq(int(hz)))


@radio_bp.post('/api/radio/band')
def set_band():
    """Make the A or B band the Main band. Body: ``{"band": a|b}``."""
    data = request.get_json(silent=True) or {}
    band = data.get('band')
    if band not in BAND_SELECT:
        return error(f"'band' must be one of {tuple(BAND_SELECT)}", 400)
    return _apply(lambda rig: rig.send_civ(BAND_SELECT[band]))


@radio_bp.post('/api/radio/dualwatch')
def set_dualwatch():
    """Enable or disable dualwatch. Body: ``{"on": true|false}``.

    Single watch keeps the current main band and mutes the sub band — which
    also removes the sub band from the recorder's SP1 audio feed.
    """
    data = request.get_json(silent=True) or {}
    on = data.get('on')
    if not isinstance(on, bool):
        return error("'on' must be a boolean", 400)
    return _apply(lambda rig: rig.send_civ(DUALWATCH + (b'\x01' if on else b'\x00')))


@radio_bp.post('/api/radio/level')
def set_level():
    """Set a rig level. Body: ``{"level": af|sql|rfpower, "value": <0..1>}``."""
    data = request.get_json(silent=True) or {}
    level = data.get('level')
    if level not in LEVELS:
        return error(f"'level' must be one of {tuple(LEVELS)}", 400)
    value = data.get('value')
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        return error("'value' must be a number in 0..1", 400)
    return _apply(lambda rig: rig.set_level(LEVELS[level], float(value)))


@radio_bp.post('/api/radio/mode')
def set_mode():
    """Set the active-band mode. Body: ``{"mode": <RADIO_MODES>, "passband_hz"?: int}``."""
    data = request.get_json(silent=True) or {}
    mode = data.get('mode')
    if mode not in RADIO_MODES:
        return error(f"'mode' must be one of {RADIO_MODES}", 400)
    passband = data.get('passband_hz', 0)
    if not isinstance(passband, int) or isinstance(passband, bool) or passband < 0:
        return error("'passband_hz' must be a non-negative integer", 400)
    return _apply(lambda rig: rig.set_mode(mode, passband))


@radio_bp.post('/api/radio/tone')
def set_tone():
    """Set CTCSS tone mode. Body: ``{"mode": off|tone|tsql, "hz"?: number}``.

    ``hz`` is the CTCSS frequency in Hz (e.g. 100.0); required unless ``mode`` is off.
    """
    data = request.get_json(silent=True) or {}
    tone_mode = data.get('mode')
    if tone_mode not in TONE_MODES:
        return error(f"'mode' must be one of {tuple(TONE_MODES)}", 400)
    hz = data.get('hz')
    if tone_mode != 'off' and (not isinstance(hz, (int, float)) or isinstance(hz, bool) or hz <= 0):
        return error("'hz' (CTCSS frequency) is required when enabling a tone", 400)
    tone_on, tsql_on = TONE_MODES[tone_mode]

    def action(rig: Rigctld) -> None:
        if hz:
            rig.set_ctcss_tone(round(hz * 10))  # Hamlib CTCSS is tenths of Hz
        rig.set_func('TONE', tone_on)
        rig.set_func('TSQL', tsql_on)

    return _apply(action)


@radio_bp.post('/api/radio/repeater')
def set_repeater():
    """Set repeater shift. Body: ``{"shift": simplex|plus|minus, "offset_hz"?: int}``.

    ``offset_hz`` is required for a non-simplex shift (e.g. 600000 on 2 m).
    """
    data = request.get_json(silent=True) or {}
    shift = data.get('shift')
    if shift not in SHIFT_TO_RIG:
        return error(f"'shift' must be one of {tuple(SHIFT_TO_RIG)}", 400)
    offset = data.get('offset_hz')
    if shift != 'simplex' and (
        not isinstance(offset, int) or isinstance(offset, bool) or offset <= 0
    ):
        return error("'offset_hz' is required for a non-simplex shift", 400)

    def action(rig: Rigctld) -> None:
        if shift != 'simplex' and offset:
            rig.set_rptr_offs(offset)
        rig.set_rptr_shift(SHIFT_TO_RIG[shift])

    return _apply(action)


def _parse_band(spec: object, label: str) -> dict:
    """Validate one band's cross-band staging spec, or raise ``ValueError``.

    A spec is ``{"freq_hz": >0, "mode": <RADIO_MODES>, "tone"?: {"mode":
    off|tone|tsql, "hz"?: number}}``. The tone defaults to off; ``hz`` is
    required whenever a tone is enabled. Returns the normalized fields the
    staging sequence applies.
    """
    if not isinstance(spec, dict):
        raise ValueError(f"'{label}' must be an object with freq_hz/mode")
    freq = spec.get('freq_hz')
    if not isinstance(freq, (int, float)) or isinstance(freq, bool) or freq <= 0:
        raise ValueError(f"'{label}.freq_hz' must be a positive number")
    mode = spec.get('mode')
    if mode not in RADIO_MODES:
        raise ValueError(f"'{label}.mode' must be one of {RADIO_MODES}")
    tone = spec.get('tone') or {'mode': 'off'}
    if not isinstance(tone, dict):
        raise ValueError(f"'{label}.tone' must be an object")
    tone_mode = tone.get('mode', 'off')
    if tone_mode not in TONE_MODES:
        raise ValueError(f"'{label}.tone.mode' must be one of {tuple(TONE_MODES)}")
    hz = tone.get('hz')
    if tone_mode != 'off' and (not isinstance(hz, (int, float)) or isinstance(hz, bool) or hz <= 0):
        raise ValueError(f"'{label}.tone.hz' is required when a tone is enabled")
    return {
        'freq': int(freq),
        'mode': mode,
        'tone_mode': tone_mode,
        'hz': hz if tone_mode != 'off' else None,
    }


@radio_bp.post('/api/radio/stage_crossband')
def stage_crossband():
    """Stage a cross-band-repeat setup — configure both bands + dualwatch at once.

    Body::

        {"a": <band spec>, "b": <band spec>,
         "rfpower"?: <0..1>, "main"?: "a"|"b"}

    where a band spec is ``{"freq_hz": >0, "mode": <RADIO_MODES>,
    "tone"?: {"mode": off|tone|tsql, "hz"?: number}}``.

    Applies in one rigctld connection, in order: pin band A Main → set its
    freq/mode/tone → pin band B Main → set its freq/mode/tone → set TX power
    (if given) → dualwatch ON → restore the requested Main band (default ``a``).
    Any rig refusal aborts the sequence (502).

    Repeater Mode itself is **not** CI-V-controllable on this rig (1.5e) — this
    only stages the two bands; the operator engages Repeater Mode on the
    touchscreen afterward. Cross-band retransmission carries station-ID
    obligations the rig doesn't automate (operator's responsibility).
    """
    data = request.get_json(silent=True) or {}
    try:
        band_a = _parse_band(data.get('a'), 'a')
        band_b = _parse_band(data.get('b'), 'b')
    except ValueError as exc:
        return error(str(exc), 400)
    main = data.get('main', 'a')
    if main not in BAND_SELECT:
        return error("'main' must be 'a' or 'b'", 400)
    rfpower = data.get('rfpower')
    if rfpower is not None and (
        not isinstance(rfpower, (int, float)) or isinstance(rfpower, bool) or not 0 <= rfpower <= 1
    ):
        return error("'rfpower' must be a number in 0..1", 400)

    def apply_band(rig: Rigctld, band: str, spec: dict) -> None:
        rig.send_civ(BAND_SELECT[band])
        rig.set_freq(spec['freq'])
        rig.set_mode(spec['mode'], 0)
        if spec['hz']:
            rig.set_ctcss_tone(round(spec['hz'] * 10))
        tone_on, tsql_on = TONE_MODES[spec['tone_mode']]
        rig.set_func('TONE', tone_on)
        rig.set_func('TSQL', tsql_on)

    def action(rig: Rigctld) -> None:
        apply_band(rig, 'a', band_a)
        apply_band(rig, 'b', band_b)
        if rfpower is not None:
            rig.set_level('RFPOWER', float(rfpower))
        rig.send_civ(DUALWATCH + b'\x01')  # dualwatch ON: both bands live
        rig.send_civ(BAND_SELECT[main])  # leave the chosen band as Main

    return _apply(action)


def _resolve_clip(filename: str) -> Path | None:
    """Resolve a soundboard filename to a real file inside the soundboard dir.

    Guards against path traversal: the resolved path must stay under the root and
    be a supported audio file. Returns ``None`` for anything that fails the check.
    """
    root = soundboard_dir().resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    if path.suffix.lower() not in transmit.SOUNDBOARD_EXTS:
        return None
    return path


@radio_bp.get('/api/radio/soundboard')
def soundboard():
    """Transmit-console config: staged soundboard clips + the available TTS engines.

    ``engines`` lists espeak-ng always and piper only when a voice model is
    configured, so the UI never offers an engine that would 500 on use.
    """
    clips = transmit.list_soundboard(soundboard_dir())
    engines = ['espeak'] + (['piper'] if transmit.PIPER_MODEL else [])
    return jsonify(
        {
            'clips': [{'filename': c.filename, 'label': c.label} for c in clips],
            'engines': engines,
        }
    )


@radio_bp.post('/api/radio/transmit')
def do_transmit():
    """Transmit a soundboard clip or a TTS phrase on the active band.

    Body is exactly one of:
      - ``{"clip": "<filename>"}`` — a staged soundboard file, or
      - ``{"text": "...", "engine"?: "espeak"|"piper", "rate"?: <0.2..2.0>}`` —
        synthesized speech (``rate`` is a speed multiplier, <1 slower).

    Both forms accept ``"settle_ms"`` (0..2000, default 250) — the post-key delay
    before audio starts, so the receiving rigs open squelch before the first word.

    Keys the rig through the never-stuck-keyed guard (R11), plays the normalized
    audio out the Digirig, and logs the clean source as an ``is_tx`` row. Callsign
    ID is the operator's responsibility — bake KC3HEU into the text/clip.

    Status: 409 if a transmit is already in flight; 502/503 on a rig
    refusal/unreachable daemon; 500 on a render/playback tool failure.
    """
    data = request.get_json(silent=True) or {}
    clip = data.get('clip')
    text = data.get('text')
    if bool(clip) == bool(text):
        return error("provide exactly one of 'clip' or 'text'", 400)
    engine = data.get('engine', 'espeak')
    if text and engine not in ('espeak', 'piper'):
        return error("'engine' must be 'espeak' or 'piper'", 400)
    if text and not isinstance(text, str):
        return error("'text' must be a string", 400)
    settle_ms = data.get('settle_ms', 250)
    if (
        not isinstance(settle_ms, (int, float))
        or isinstance(settle_ms, bool)
        or not 0 <= settle_ms <= 2000
    ):
        return error("'settle_ms' must be a number in 0..2000", 400)
    rate = data.get('rate', 1.0)
    if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not 0.2 <= rate <= 2.0:
        return error("'rate' must be a number in 0.2..2.0", 400)

    if not _TX_LOCK.acquire(blocking=False):
        return jsonify({'ok': False, 'error': 'a transmission is already in progress'}), 409
    try:
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / 'tx.wav'
            if clip:
                src = _resolve_clip(clip)
                if src is None:
                    return error('unknown soundboard clip', 404)
                transmit.prepare_clip(src, wav)
            else:
                transmit.render_tts(text, wav, engine=engine, rate=rate)

            started = datetime.now(UTC)
            with transmit.tx_active():
                transmit.transmit_wav(wav, settle_s=settle_ms / 1000)
            freq, mode, _ = rig_snapshot()
            conn = get_connection()
            lat, lon = gps_snap(conn)
            tx_id = transmit.archive_and_log(
                conn, wav, started=started, freq_hz=freq, mode=mode, lat=lat, lon=lon
            )
        return jsonify({'ok': True, 'id': tx_id})
    except transmit.TransmitError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500
    except RigctldError as exc:
        return jsonify({'ok': False, 'error': str(exc)}), (503 if exc.rprt is None else 502)
    finally:
        _TX_LOCK.release()


@radio_bp.get('/api/radio/transmissions')
def list_transmissions():
    """Recorded-transmission log, newest first, keyset-paged.

    Query params: ``limit`` (default 50, max 500); ``before_id`` — return rows
    with ``id`` below it (pass the last row's id to fetch the next page);
    ``min_s`` — duration floor in seconds, the server-side blip filter (the
    rig's touchscreen beeps VOX-trigger minimum-length ~5 s captures).
    ``total`` counts every row matching ``min_s`` regardless of paging, so the
    UI can size the log and know when it has loaded everything.
    """
    limit, err = parse_limit(request.args, default=50, maximum=500)
    if err:
        return err

    before_raw = request.args.get('before_id')
    before_id: int | None = None
    if before_raw is not None:
        try:
            before_id = int(before_raw)
        except ValueError:
            return error("'before_id' must be an integer", 400)

    min_raw = request.args.get('min_s')
    min_s: float | None = None
    if min_raw is not None:
        try:
            min_s = float(min_raw)
        except ValueError:
            return error("'min_s' must be a number", 400)
        if min_s < 0:
            return error("'min_s' must be >= 0", 400)

    conn = get_connection()
    where = ['duration_s >= ?'] if min_s is not None else []
    params: list[float] = [min_s] if min_s is not None else []
    total_sql = 'SELECT COUNT(*) FROM radio_transmissions'
    if where:
        total_sql += ' WHERE ' + ' AND '.join(where)
    total = conn.execute(total_sql, params).fetchone()[0]

    if before_id is not None:
        where.append('id < ?')
        params.append(before_id)
    sql = f'SELECT {_TX_COLUMNS} FROM radio_transmissions'
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY id DESC LIMIT ?'
    rows = conn.execute(sql, [*params, limit]).fetchall()

    transmissions = [
        dict(r)
        | {
            'has_audio': bool(r['has_audio']),
            'waveform': json.loads(r['waveform']) if r['waveform'] else None,
        }
        for r in rows
    ]
    return jsonify({'transmissions': transmissions, 'count': len(transmissions), 'total': total})


@radio_bp.get('/api/radio/transmissions/<int:tx_id>/audio')
def transmission_audio(tx_id: int):
    """Serve one capture's WAV; 404 when the row, its audio, or the file is gone.

    ``conditional=True`` gives Range support, which ``<audio>`` scrubbing needs.
    A pruned row (``audio_path`` NULL) is a 404 like a missing row — the log
    endpoint's ``has_audio`` is how the UI avoids offering dead play buttons.
    """
    conn = get_connection()
    row = conn.execute(
        'SELECT audio_path FROM radio_transmissions WHERE id = ?', (tx_id,)
    ).fetchone()
    if row is None or row['audio_path'] is None:
        return error('no audio for this transmission', 404)
    root = audio_dir().resolve()
    path = (root / row['audio_path']).resolve()
    # Rows are recorder-written, but never let a stored path escape the root.
    if not path.is_relative_to(root) or not path.is_file():
        return error('audio file missing', 404)
    return send_file(path, mimetype='audio/wav', conditional=True, download_name=path.name)
