"""Radio control routes — drive the Icom ID-5100A through a local ``rigctld``.

Read endpoints poll the rig's live state; write endpoints push freq/mode/tone/
repeater changes. Everything goes through :mod:`api.rigctld` (rigctld's TCP text
protocol) — no serial access here. Phase 1 controls the **active main band only**:
the backend can't read which VFO is active, so a dual-band readout would have to flip
the radio's active VFO each poll. See ``plans/radio-platform-plan.md`` (R6).
"""

from collections.abc import Callable

from flask import Blueprint, jsonify, request

from api.rigctld import Rigctld, RigctldError
from common import proc

radio_bp = Blueprint('radio', __name__)

SERVICE = 'radio-control'

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


def _err(message: str, status: int):
    """A ``({'error': ...}, status)`` JSON tuple for a bad request."""
    return jsonify({'error': message}), status


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
        }
    )


@radio_bp.post('/api/radio/freq')
def set_freq():
    """Tune the active band. Body: ``{"hz": <positive number>}``."""
    data = request.get_json(silent=True) or {}
    hz = data.get('hz')
    if not isinstance(hz, (int, float)) or isinstance(hz, bool) or hz <= 0:
        return _err("'hz' must be a positive number", 400)
    return _apply(lambda rig: rig.set_freq(int(hz)))


@radio_bp.post('/api/radio/band')
def set_band():
    """Make the A or B band the Main band. Body: ``{"band": a|b}``."""
    data = request.get_json(silent=True) or {}
    band = data.get('band')
    if band not in BAND_SELECT:
        return _err(f"'band' must be one of {tuple(BAND_SELECT)}", 400)
    return _apply(lambda rig: rig.send_civ(BAND_SELECT[band]))


@radio_bp.post('/api/radio/level')
def set_level():
    """Set a rig level. Body: ``{"level": af|sql|rfpower, "value": <0..1>}``."""
    data = request.get_json(silent=True) or {}
    level = data.get('level')
    if level not in LEVELS:
        return _err(f"'level' must be one of {tuple(LEVELS)}", 400)
    value = data.get('value')
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
        return _err("'value' must be a number in 0..1", 400)
    return _apply(lambda rig: rig.set_level(LEVELS[level], float(value)))


@radio_bp.post('/api/radio/mode')
def set_mode():
    """Set the active-band mode. Body: ``{"mode": <RADIO_MODES>, "passband_hz"?: int}``."""
    data = request.get_json(silent=True) or {}
    mode = data.get('mode')
    if mode not in RADIO_MODES:
        return _err(f"'mode' must be one of {RADIO_MODES}", 400)
    passband = data.get('passband_hz', 0)
    if not isinstance(passband, int) or isinstance(passband, bool) or passband < 0:
        return _err("'passband_hz' must be a non-negative integer", 400)
    return _apply(lambda rig: rig.set_mode(mode, passband))


@radio_bp.post('/api/radio/tone')
def set_tone():
    """Set CTCSS tone mode. Body: ``{"mode": off|tone|tsql, "hz"?: number}``.

    ``hz`` is the CTCSS frequency in Hz (e.g. 100.0); required unless ``mode`` is off.
    """
    data = request.get_json(silent=True) or {}
    tone_mode = data.get('mode')
    if tone_mode not in TONE_MODES:
        return _err(f"'mode' must be one of {tuple(TONE_MODES)}", 400)
    hz = data.get('hz')
    if tone_mode != 'off' and (not isinstance(hz, (int, float)) or isinstance(hz, bool) or hz <= 0):
        return _err("'hz' (CTCSS frequency) is required when enabling a tone", 400)
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
        return _err(f"'shift' must be one of {tuple(SHIFT_TO_RIG)}", 400)
    offset = data.get('offset_hz')
    if shift != 'simplex' and (
        not isinstance(offset, int) or isinstance(offset, bool) or offset <= 0
    ):
        return _err("'offset_hz' is required for a non-simplex shift", 400)

    def action(rig: Rigctld) -> None:
        if shift != 'simplex' and offset:
            rig.set_rptr_offs(offset)
        rig.set_rptr_shift(SHIFT_TO_RIG[shift])

    return _apply(action)
