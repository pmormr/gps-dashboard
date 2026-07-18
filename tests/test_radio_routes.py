"""Tests for the radio control routes (``api/routes/radio.py``).

The routes are exercised through the real Flask app, with :class:`api.rigctld.Rigctld`
replaced by an in-memory fake — so no rigctld daemon or serial port is needed. The
fake reproduces the context-manager shape and the getter/setter surface the routes use.
"""

from __future__ import annotations

import api.routes.radio as radio
from api.rigctld import RigctldError


class FakeRig:
    """In-memory stand-in for :class:`api.rigctld.Rigctld`."""

    def __init__(self, *, fail_enter: bool = False, set_rprt: int | None = None) -> None:
        self._fail_enter = fail_enter
        self._set_rprt = set_rprt
        self.calls: list[tuple] = []

    def __enter__(self) -> FakeRig:
        if self._fail_enter:
            raise RigctldError('cannot reach rigctld at 127.0.0.1:4532')
        return self

    def __exit__(self, *exc: object) -> None: ...

    def get_freq(self) -> int:
        return 146520000

    def get_mode(self) -> tuple[str, int]:
        return 'FM', 15000

    def get_level(self, name: str) -> float | None:
        return {'RAWSTR': 142.0, 'AF': 0.4, 'SQL': 0.25, 'RFPOWER': 1.0}.get(name)

    def get_func(self, name: str) -> bool | None:
        return False

    def get_ctcss_tone(self) -> int | None:
        return 1000

    def get_rptr_shift(self) -> str | None:
        return '+'

    def get_rptr_offs(self) -> int | None:
        return 600000

    def get_dcd(self) -> bool | None:
        return False

    def get_ptt(self) -> bool | None:
        return False

    def _write(self, name: str, *args: object) -> None:
        self.calls.append((name, *args))
        if self._set_rprt is not None:
            raise RigctldError('rig refused', rprt=self._set_rprt)

    def set_freq(self, hz: int) -> None:
        self._write('set_freq', hz)

    def set_mode(self, mode: str, passband: int = 0) -> None:
        self._write('set_mode', mode, passband)

    def set_ctcss_tone(self, tenths: int) -> None:
        self._write('set_ctcss_tone', tenths)

    def set_func(self, name: str, on: bool) -> None:
        self._write('set_func', name, on)

    def set_rptr_shift(self, shift: str) -> None:
        self._write('set_rptr_shift', shift)

    def set_rptr_offs(self, hz: int) -> None:
        self._write('set_rptr_offs', hz)

    def set_level(self, name: str, value: float) -> None:
        self._write('set_level', name, value)

    def send_civ(self, payload: bytes) -> None:
        self._write('send_civ', payload)


def _patch_rig(monkeypatch, **kwargs) -> None:
    monkeypatch.setattr(radio, 'Rigctld', lambda *a, **k: FakeRig(**kwargs))


def test_status_online(client, monkeypatch):
    _patch_rig(monkeypatch)
    resp = client.get('/api/radio/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['online'] is True
    assert data['freq_hz'] == 146520000
    assert data['mode'] == 'FM'
    assert data['rawstr'] == 142.0
    assert 'strength_db' not in data  # Hamlib's generic table, not ID-5100-calibrated
    assert data['levels'] == {'af': 0.4, 'sql': 0.25, 'rfpower': 1.0}
    assert data['ctcss_tone_hz'] == 100.0  # 1000 tenths → 100.0 Hz
    assert data['rptr_shift'] == 'plus'


def test_set_band_sends_civ_frame(client, monkeypatch):
    fake = FakeRig()
    monkeypatch.setattr(radio, 'Rigctld', lambda *a, **k: fake)
    resp = client.post('/api/radio/band', json={'band': 'b'})
    assert resp.status_code == 200
    assert fake.calls == [('send_civ', b'\x07\xd1')]


def test_set_band_rejects_unknown(client, monkeypatch):
    _patch_rig(monkeypatch)
    assert client.post('/api/radio/band', json={'band': 'c'}).status_code == 400


def test_set_level_ok(client, monkeypatch):
    _patch_rig(monkeypatch)
    resp = client.post('/api/radio/level', json={'level': 'af', 'value': 0.35})
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True


def test_set_level_rejects_unknown_name_and_range(client, monkeypatch):
    _patch_rig(monkeypatch)
    assert client.post('/api/radio/level', json={'level': 'ptt', 'value': 1}).status_code == 400
    assert client.post('/api/radio/level', json={'level': 'af', 'value': 1.5}).status_code == 400
    assert client.post('/api/radio/level', json={'level': 'af', 'value': True}).status_code == 400


def test_status_offline_when_daemon_unreachable(client, monkeypatch):
    _patch_rig(monkeypatch, fail_enter=True)
    monkeypatch.setattr(radio.proc, 'service_state', lambda _name: 'inactive')
    resp = client.get('/api/radio/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['online'] is False
    assert data['service'] == 'inactive'


def test_set_freq_ok(client, monkeypatch):
    _patch_rig(monkeypatch)
    resp = client.post('/api/radio/freq', json={'hz': 146520000})
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True


def test_set_freq_rejects_non_positive(client, monkeypatch):
    _patch_rig(monkeypatch)
    resp = client.post('/api/radio/freq', json={'hz': 0})
    assert resp.status_code == 400


def test_set_freq_rejects_bool(client, monkeypatch):
    # bool is an int subclass; the route must not accept True as a frequency.
    _patch_rig(monkeypatch)
    resp = client.post('/api/radio/freq', json={'hz': True})
    assert resp.status_code == 400


def test_set_mode_rejects_unknown(client, monkeypatch):
    _patch_rig(monkeypatch)
    resp = client.post('/api/radio/mode', json={'mode': 'SSB'})
    assert resp.status_code == 400


def test_set_freq_daemon_unreachable_is_503(client, monkeypatch):
    _patch_rig(monkeypatch, fail_enter=True)
    resp = client.post('/api/radio/freq', json={'hz': 146520000})
    assert resp.status_code == 503


def test_set_freq_rig_refusal_is_502(client, monkeypatch):
    _patch_rig(monkeypatch, set_rprt=-1)
    resp = client.post('/api/radio/freq', json={'hz': 146520000})
    assert resp.status_code == 502


def test_set_tone_requires_hz_when_enabling(client, monkeypatch):
    _patch_rig(monkeypatch)
    resp = client.post('/api/radio/tone', json={'mode': 'tone'})
    assert resp.status_code == 400


def test_set_tone_off_needs_no_hz(client, monkeypatch):
    _patch_rig(monkeypatch)
    resp = client.post('/api/radio/tone', json={'mode': 'off'})
    assert resp.status_code == 200


def test_set_repeater_requires_offset_for_shift(client, monkeypatch):
    _patch_rig(monkeypatch)
    resp = client.post('/api/radio/repeater', json={'shift': 'plus'})
    assert resp.status_code == 400


def _insert_tx(
    *,
    duration_s: float = 8.0,
    dcd_main: int | None = 1,
    audio_path: str | None = None,
) -> int:
    """Insert one ``radio_transmissions`` row into the test DB, returning its id."""
    from api.db import get_connection

    conn = get_connection()
    cur = conn.execute(
        'INSERT INTO radio_transmissions (started_utc, ended_utc, duration_s, freq_hz, mode, '
        'dcd_main, peak_dbfs, rms_dbfs, audio_path, lat, lon) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (
            '2026-07-18T20:00:00.000Z',
            '2026-07-18T20:00:08.000Z',
            duration_s,
            146520000,
            'FM',
            dcd_main,
            -16.0,
            -30.5,
            audio_path,
            39.7,
            -105.2,
        ),
    )
    conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


class TestTransmissionList:
    """``GET /api/radio/transmissions`` — the recorded-transmission log read."""

    def test_empty(self, client):
        data = client.get('/api/radio/transmissions').get_json()
        assert data == {'transmissions': [], 'count': 0, 'total': 0}

    def test_newest_first_and_shape(self, client):
        first = _insert_tx(audio_path='2026-07/a.wav')
        second = _insert_tx(audio_path=None)
        data = client.get('/api/radio/transmissions').get_json()
        assert [t['id'] for t in data['transmissions']] == [second, first]
        newest = data['transmissions'][0]
        assert newest['has_audio'] is False  # pruned row: metadata survives
        assert data['transmissions'][1]['has_audio'] is True
        assert 'audio_path' not in newest  # server-side detail, not API surface
        assert newest['freq_hz'] == 146520000
        assert newest['dcd_main'] == 1

    def test_keyset_paging(self, client):
        ids = [_insert_tx() for _ in range(5)]
        page1 = client.get('/api/radio/transmissions?limit=2').get_json()
        assert [t['id'] for t in page1['transmissions']] == [ids[4], ids[3]]
        assert page1['total'] == 5
        page2 = client.get(f'/api/radio/transmissions?limit=2&before_id={ids[3]}').get_json()
        assert [t['id'] for t in page2['transmissions']] == [ids[2], ids[1]]
        assert page2['total'] == 5  # total ignores the cursor

    def test_min_s_filters_rows_and_total(self, client):
        _insert_tx(duration_s=5.2, dcd_main=0)  # a touchscreen-beep blip
        voice = _insert_tx(duration_s=10.9)
        data = client.get('/api/radio/transmissions?min_s=6').get_json()
        assert [t['id'] for t in data['transmissions']] == [voice]
        assert data['total'] == 1

    def test_bad_params(self, client):
        assert client.get('/api/radio/transmissions?limit=0').status_code == 400
        assert client.get('/api/radio/transmissions?before_id=x').status_code == 400
        assert client.get('/api/radio/transmissions?min_s=x').status_code == 400
        assert client.get('/api/radio/transmissions?min_s=-1').status_code == 400


class TestTransmissionAudio:
    """``GET /api/radio/transmissions/<id>/audio`` — the WAV read path."""

    def _write_wav(self, tmp_path, rel: str, payload: bytes) -> None:
        path = tmp_path / 'radio-audio' / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def test_serves_wav(self, client, tmp_path):
        payload = b'RIFF' + bytes(100)
        self._write_wav(tmp_path, '2026-07/a.wav', payload)
        tx = _insert_tx(audio_path='2026-07/a.wav')
        resp = client.get(f'/api/radio/transmissions/{tx}/audio')
        assert resp.status_code == 200
        assert resp.mimetype == 'audio/wav'
        assert resp.data == payload

    def test_range_request_scrubs(self, client, tmp_path):
        # <audio> scrubbing depends on Range support (send_file conditional=True).
        self._write_wav(tmp_path, '2026-07/a.wav', b'RIFF' + bytes(100))
        tx = _insert_tx(audio_path='2026-07/a.wav')
        resp = client.get(f'/api/radio/transmissions/{tx}/audio', headers={'Range': 'bytes=0-3'})
        assert resp.status_code == 206
        assert resp.data == b'RIFF'

    def test_unknown_row_404(self, client):
        assert client.get('/api/radio/transmissions/999/audio').status_code == 404

    def test_pruned_row_404(self, client):
        tx = _insert_tx(audio_path=None)
        assert client.get(f'/api/radio/transmissions/{tx}/audio').status_code == 404

    def test_file_gone_404(self, client):
        tx = _insert_tx(audio_path='2026-07/vanished.wav')
        assert client.get(f'/api/radio/transmissions/{tx}/audio').status_code == 404

    def test_traversal_guard_404(self, client, tmp_path):
        (tmp_path / 'secret.bin').write_bytes(b'not audio')
        tx = _insert_tx(audio_path='../secret.bin')
        assert client.get(f'/api/radio/transmissions/{tx}/audio').status_code == 404
