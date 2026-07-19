"""Fridge control routes — drive the Dometic CFX3 over DDMP (``common.ddmp``).

The read/write split matches the fridge's single-app-client constraint: ``status``
is served from the DB (the reader's ≤60 s-old snapshot, timestamped so the UI wears
the age) plus a once-per-process live fetch of the firmware-constant setpoint
ranges; only the **writes** (zone setpoint, zone power) open a live DDMP session —
short, retried once on a busy slot, and answered with an in-session read-back so
the UI reflects the change immediately instead of waiting out the next poll.
``history`` reads the stored ``fridge_history`` tier (see ``mqttbus/ingest.py``).

Error mapping mirrors the radio routes: 400 bad input, 502 the fridge refused
(NAK), 503 the fridge is unreachable.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, request

from api.db import get_connection
from api.params import error, parse_time
from api.sensor_schema import READING_TABLES
from common import ddmp
from common.ddmp import HISTORY_BUCKET_S, DdmpClient, DdmpError
from common.timefmt import parse_iso

fridge_bp = Blueprint('fridge', __name__)

# One connect retry after this pause: the 60 s poller's session (or the phone app)
# may hold the fridge's single client slot; sessions run well under a second.
WRITE_RETRY_DELAY_S = 1.5

# After a failed live ranges fetch, don't re-claim the app slot for this long.
RANGES_COOLDOWN_S = 300.0

# span → default trailing window for /api/fridge/history when no start is given.
HISTORY_DEFAULT_WINDOW_S = {
    'hour': 24 * 3600,
    'day': 14 * 86400,
    'week': 12 * 604800,
}

# Lazily-cached firmware constants (setpoint ranges + presented unit): fetched
# live at most once per process, with a cooldown after failures. None = not yet
# fetched (or last attempt failed).
_live_constants: dict | None = None
_next_fetch_after = 0.0


def _apply(action: Callable[[DdmpClient], dict]):
    """Run a DDMP write session, mapping failures to HTTP status.

    Retries the whole session once on a transport failure (a busy client slot is
    indistinguishable from a dead fridge at connect time; the writes are
    idempotent, so a lost-ACK replay is harmless). A NAK is a real refusal — 502,
    no retry; a transport failure after the retry is 503.
    """
    for attempt in (0, 1):
        try:
            with DdmpClient() as client:
                return jsonify({'ok': True, **action(client)})
        except DdmpError as exc:
            if exc.refused:
                return jsonify({'ok': False, 'error': str(exc)}), 502
            if attempt == 0:
                time.sleep(WRITE_RETRY_DELAY_S)
                continue
            return jsonify({'ok': False, 'error': str(exc)}), 503
    raise AssertionError('unreachable')


def _decode_range(frames: list[list[int]]) -> dict | None:
    """Decode a range topic's publish into ``{min_c, max_c}`` (None if absent)."""
    if not frames:
        return None
    values = ddmp.decode_int16_array(frames[-1])
    if len(values) < 2:
        return None
    return {'min_c': values[0], 'max_c': values[1]}


def _fetch_live_constants() -> dict:
    """One live DDMP session for the firmware constants the control UI needs.

    Returns:
        ``{'ranges': {'comp0': {allowed, recommended}, 'comp1': ...},
        'temp_unit': 0|1|None}`` (0 = °C, 1 = °F — how the fridge itself displays).

    Raises:
        DdmpError: When the fridge is unreachable or the session dies.
    """
    with DdmpClient() as client:
        client.ping()
        ranges = {}
        for zone in (0, 1):
            ranges[f'comp{zone}'] = {
                'allowed': _decode_range(client.subscribe(ddmp.temp_range_param(zone))),
                'recommended': _decode_range(client.subscribe(ddmp.recommended_range_param(zone))),
            }
        unit_frames = client.subscribe(ddmp.PRESENTED_UNIT_PARAM)
    unit = ddmp.decode_value('u8', unit_frames[-1]) if unit_frames else None
    return {'ranges': ranges, 'temp_unit': unit}


def _live_constants_cached() -> dict | None:
    """Return the cached firmware constants, fetching live at most once per process.

    A failed fetch leaves the cache empty and arms a cooldown so status polls
    don't hammer the fridge's single client slot while it's off the LAN.
    """
    global _live_constants, _next_fetch_after
    if _live_constants is not None:
        return _live_constants
    if time.monotonic() < _next_fetch_after:
        return None
    try:
        fetched = _fetch_live_constants()
    except DdmpError:
        _next_fetch_after = time.monotonic() + RANGES_COOLDOWN_S
        return None
    if all(zone['allowed'] is None for zone in fetched['ranges'].values()):
        # The fridge answered the session but published no ranges — treat it as
        # a failed fetch rather than caching an empty shape for the process life.
        _next_fetch_after = time.monotonic() + RANGES_COOLDOWN_S
        return None
    _live_constants = fetched
    return _live_constants


def _allowed_range(zone: int) -> dict | None:
    """Return the allowed setpoint range for a zone from the warm cache only.

    Deliberately does not trigger a live fetch — a write should cost one
    session, not two; with a cold cache the fridge itself is the validator
    (an out-of-range write NAKs → 502).
    """
    if _live_constants is None:
        return None
    return _live_constants['ranges'][f'comp{zone}']['allowed']


def _fridge_sensor(conn) -> dict | None:
    """Return the fridge's registry row (id/status/last_seen), or None."""
    row = conn.execute(
        "SELECT id, status, last_seen FROM sensors WHERE type = 'fridge' "
        'ORDER BY last_seen DESC LIMIT 1'
    ).fetchone()
    return dict(row) if row else None


@fridge_bp.get('/api/fridge/status')
def status():
    """Control-page state: the latest stored snapshot plus liveness and ranges.

    Always 200. ``reading`` is the reader's most recent ``fridge_readings`` row
    (≤60 s old when the stream is online; its ``timestamp`` lets the UI wear the
    age); ``online``/``last_seen`` come from the sensor registry (MQTT status
    flag). ``ranges``/``temp_unit`` are the lazily-cached firmware constants —
    null until a live fetch has succeeded.
    """
    conn = get_connection()
    sensor = _fridge_sensor(conn)
    reading = None
    if sensor is not None:
        spec = READING_TABLES['fridge']
        cols = ', '.join(['timestamp', *spec['metrics']])
        row = conn.execute(
            f'SELECT {cols} FROM {spec["table"]} WHERE sensor_id = ? '
            'ORDER BY timestamp DESC LIMIT 1',
            (sensor['id'],),
        ).fetchone()
        reading = dict(row) if row else None
    constants = _live_constants_cached()
    return jsonify(
        {
            'online': bool(sensor) and sensor['status'] == 'online',
            'status': sensor['status'] if sensor else 'unknown',
            'last_seen': sensor['last_seen'] if sensor else None,
            'reading': reading,
            'ranges': constants['ranges'] if constants else None,
            'temp_unit': constants['temp_unit'] if constants else None,
        }
    )


@fridge_bp.post('/api/fridge/setpoint')
def set_setpoint():
    """Set a zone's target temperature. Body: ``{"zone": 0|1, "temp_c": number}``.

    ``temp_c`` is validated against the fridge's own allowed range when the
    ranges cache is warm; otherwise the fridge is the validator (a refused write
    NAKs → 502). Returns the setpoint read back live from the fridge.
    """
    data = request.get_json(silent=True) or {}
    zone = data.get('zone')
    if zone not in (0, 1) or isinstance(zone, bool):
        return error("'zone' must be 0 or 1", 400)
    temp_c = data.get('temp_c')
    if not isinstance(temp_c, (int, float)) or isinstance(temp_c, bool):
        return error("'temp_c' must be a number", 400)
    allowed = _allowed_range(zone)
    if allowed is not None and not allowed['min_c'] <= temp_c <= allowed['max_c']:
        return error(f"'temp_c' must be within {allowed['min_c']}..{allowed['max_c']} °C", 400)

    def action(client: DdmpClient) -> dict:
        param = ddmp.setpoint_param(zone)
        client.write(param, ddmp.encode_ddegc(float(temp_c)))
        frames = client.subscribe(param)
        set_c = ddmp.decode_value('ddegC', frames[-1]) if frames else None
        return {'zone': zone, 'set_c': set_c}

    return _apply(action)


@fridge_bp.post('/api/fridge/power')
def set_power():
    """Turn a compartment on or off. Body: ``{"zone": 0|1, "on": bool}``.

    Per-zone only by design — the master cooler-power topic is deliberately not
    exposed (a remote whole-fridge "off" silently thaws food). Returns the power
    state read back live from the fridge.
    """
    data = request.get_json(silent=True) or {}
    zone = data.get('zone')
    if zone not in (0, 1) or isinstance(zone, bool):
        return error("'zone' must be 0 or 1", 400)
    on = data.get('on')
    if not isinstance(on, bool):
        return error("'on' must be a boolean", 400)

    def action(client: DdmpClient) -> dict:
        param = ddmp.zone_power_param(zone)
        client.write(param, ddmp.encode_bool(on))
        frames = client.subscribe(param)
        power = ddmp.decode_value('bool', frames[-1]) if frames else None
        return {'zone': zone, 'power': power}

    return _apply(action)


@fridge_bp.get('/api/fridge/history')
def history():
    """Stored DC power-usage buckets. Query: ``?span=hour|day|week&start=&end=``.

    Serves the ``fridge_history`` tier (the fridge's own consumption history,
    retained past its 7-bucket window). Defaults to a span-appropriate trailing
    window; ``points`` are bucket-start timestamps in canonical ms-UTC.
    """
    span = request.args.get('span', 'hour')
    if span not in HISTORY_BUCKET_S:
        return error(f"'span' must be one of {tuple(HISTORY_BUCKET_S)}", 400)
    end = start = None
    if raw_end := request.args.get('end'):
        end, err = parse_time(raw_end, 'end')
        if err:
            return err
    if raw_start := request.args.get('start'):
        start, err = parse_time(raw_start, 'start')
        if err:
            return err
    if start is None:
        end_dt = parse_iso(end) if end else datetime.now(UTC)
        window = timedelta(seconds=HISTORY_DEFAULT_WINDOW_S[span])
        start = (end_dt - window).strftime('%Y-%m-%dT%H:%M:%S.000Z')

    conn = get_connection()
    sensor = _fridge_sensor(conn)
    points: list[dict] = []
    updated_at = None
    if sensor is not None:
        clauses = ['sensor_id = ?', 'span = ?', 'bucket_ts >= ?']
        args: list[object] = [sensor['id'], span, start]
        if end:
            clauses.append('bucket_ts <= ?')
            args.append(end)
        rows = conn.execute(
            'SELECT bucket_ts, dc_current_a, updated_at FROM fridge_history '
            f'WHERE {" AND ".join(clauses)} ORDER BY bucket_ts',
            args,
        ).fetchall()
        points = [{'ts': r['bucket_ts'], 'dc_current_a': r['dc_current_a']} for r in rows]
        updated_at = max((r['updated_at'] for r in rows), default=None)
    return jsonify(
        {
            'span': span,
            'bucket_s': HISTORY_BUCKET_S[span],
            'points': points,
            'updated_at': updated_at,
        }
    )
