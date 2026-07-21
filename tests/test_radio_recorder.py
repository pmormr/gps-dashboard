"""Recorder pure-helper tests — naming, retention policy, capture finalize, GPS snap."""

from __future__ import annotations

import io
import json
import os
import wave
from array import array
from collections import deque
from datetime import UTC, datetime

import pytest

import radio.recorder as recorder
from api.db import init_db, now_canonical
from api.rigctld import RigctldError
from radio import freqstate
from radio.levels import LevelKeeper
from radio.recorder import Capture, audio_rel_path, gps_snap, prune_selection, read_block
from radio.vox import block_energy
from radio.waveform import WAVEFORM_SUBBLOCKS


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """An initialised throwaway DB connection (places sidecar rides beside it)."""
    import api.db as db

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    c = db.get_connection()
    init_db(c)
    yield c
    c.close()


class TestAudioRelPath:
    def test_month_subdir_and_ms_name(self):
        started = datetime(2026, 7, 18, 12, 34, 56, 789000, tzinfo=UTC)
        assert audio_rel_path(started) == '2026-07/20260718-123456-789.wav'


class TestPruneSelection:
    def test_nothing_doomed_when_within_limits(self):
        files = [('a.wav', 900.0, 10), ('b.wav', 950.0, 10)]
        assert prune_selection(files, 1000.0, max_age_s=500.0, max_bytes=100) == []

    def test_age_rule(self):
        files = [('old.wav', 100.0, 10), ('new.wav', 900.0, 10)]
        assert prune_selection(files, 1000.0, max_age_s=500.0, max_bytes=100) == ['old.wav']

    def test_size_cap_sweeps_oldest_first(self):
        files = [('a.wav', 100.0, 10), ('b.wav', 200.0, 10), ('c.wav', 300.0, 10)]
        assert prune_selection(files, 400.0, max_age_s=10_000.0, max_bytes=15) == [
            'a.wav',
            'b.wav',
        ]

    def test_age_then_size(self):
        files = [('a.wav', 100.0, 10), ('b.wav', 200.0, 10), ('c.wav', 300.0, 10)]
        # a dies of age; survivors b+c (20 B) exceed 15 B → oldest survivor b dies too.
        assert prune_selection(files, 1000.0, max_age_s=850.0, max_bytes=15) == [
            'a.wav',
            'b.wav',
        ]


class TestReadBlock:
    def test_full_block(self):
        data = b'\x01' * recorder.BLOCK_BYTES
        assert read_block(io.BytesIO(data)) == data

    def test_eof_short_read_returns_empty(self):
        assert read_block(io.BytesIO(b'\x01' * 10)) == b''


class TestGpsSnap:
    def test_fresh_fix(self, conn):
        conn.execute(
            'INSERT INTO gps_points (timestamp, lat, lon, mode) VALUES (?, ?, ?, ?)',
            (now_canonical(), 39.7, -105.2, 3),
        )
        assert gps_snap(conn) == (39.7, -105.2)

    def test_stale_fix_returns_none(self, conn):
        conn.execute(
            'INSERT INTO gps_points (timestamp, lat, lon, mode) VALUES (?, ?, ?, ?)',
            ('2020-01-01T00:00:00.000Z', 39.7, -105.2, 3),
        )
        assert gps_snap(conn) == (None, None)

    def test_no_points(self, conn):
        assert gps_snap(conn) == (None, None)


def _pending_capture(tmp_path) -> Capture:
    """A minimal unmaterialized capture for metadata-behavior tests."""
    return Capture(
        part_path=tmp_path / 'x.wav.part',
        rel_path='x.wav',
        started=datetime(2026, 7, 18, tzinfo=UTC),
        freq_hz=None,
        mode=None,
        dcd_main=None,
        lat=None,
        lon=None,
    )


class _FakeDcdRig:
    """Context-manager stand-in for Rigctld yielding a fixed DCD reading."""

    def __init__(self, dcd: bool | None = True, fail: bool = False) -> None:
        self._dcd = dcd
        self._fail = fail

    def __enter__(self) -> _FakeDcdRig:
        if self._fail:
            raise RigctldError('cannot reach rigctld')
        return self

    def __exit__(self, *exc: object) -> None: ...

    def get_dcd(self) -> bool | None:
        return self._dcd


class _FakeLevelsRig:
    """Context-manager Rigctld stand-in scripting SQL reads + capturing sets."""

    def __init__(self, sql: float | None = 0.2, fail: bool = False) -> None:
        self._sql = sql
        self._fail = fail
        self.sets: list[tuple[str, float]] = []

    def __enter__(self) -> _FakeLevelsRig:
        if self._fail:
            raise RigctldError('cannot reach rigctld')
        return self

    def __exit__(self, *exc: object) -> None: ...

    def set_level(self, name: str, value: float) -> None:
        self.sets.append((name, value))

    def get_level(self, name: str) -> float | None:
        return self._sql


class TestHeartbeatLevels:
    def test_pins_af_and_adopts_sql(self, monkeypatch):
        rig = _FakeLevelsRig(sql=0.2)
        monkeypatch.setattr(recorder, 'Rigctld', lambda *a, **k: rig)
        monkeypatch.setattr(recorder, 'PIN_AF', '0.25')
        keeper = LevelKeeper(open_dbfs=-40.0)
        assert recorder.heartbeat_levels(keeper)
        assert rig.sets == [('AF', 0.25)]
        assert keeper.known_sql == 0.2

    def test_applies_keeper_action(self, monkeypatch):
        keeper = LevelKeeper(open_dbfs=-40.0)
        keeper.heartbeat(0.2)
        keeper.heartbeat(None)  # offline gap; the power-cycle revert follows
        rig = _FakeLevelsRig(sql=0.173)
        monkeypatch.setattr(recorder, 'Rigctld', lambda *a, **k: rig)
        monkeypatch.setattr(recorder, 'PIN_AF', '0.25')
        assert recorder.heartbeat_levels(keeper)
        assert ('SQL', 0.2) in rig.sets

    def test_unreachable_marks_keeper_offline(self, monkeypatch):
        keeper = LevelKeeper(open_dbfs=-40.0)
        keeper.heartbeat(0.2)
        monkeypatch.setattr(recorder, 'Rigctld', lambda *a, **k: _FakeLevelsRig(fail=True))
        assert not recorder.heartbeat_levels(keeper)
        assert not keeper.online

    def test_pin_disabled_by_empty_env(self, monkeypatch):
        rig = _FakeLevelsRig(sql=0.2)
        monkeypatch.setattr(recorder, 'Rigctld', lambda *a, **k: rig)
        monkeypatch.setattr(recorder, 'PIN_AF', '')
        assert recorder.heartbeat_levels(LevelKeeper(open_dbfs=-40.0))
        assert rig.sets == []


class TestPollDcd:
    def test_open_reading_folds_in(self, tmp_path, monkeypatch):
        monkeypatch.setattr(recorder, 'Rigctld', lambda *a, **k: _FakeDcdRig(dcd=True))
        cap = _pending_capture(tmp_path)
        recorder.poll_dcd(cap)
        assert cap.dcd_main == 1

    def test_one_failure_disables_polling(self, tmp_path, monkeypatch):
        monkeypatch.setattr(recorder, 'Rigctld', lambda *a, **k: _FakeDcdRig(fail=True))
        cap = _pending_capture(tmp_path)
        recorder.poll_dcd(cap)
        assert cap.dcd_main is None and not cap.dcd_poll_ok
        # A later healthy rigctld must not resurrect polling mid-capture.
        monkeypatch.setattr(recorder, 'Rigctld', lambda *a, **k: _FakeDcdRig(dcd=True))
        recorder.poll_dcd(cap)
        assert cap.dcd_main is None


class TestCaptureClose:
    def test_finalizes_file_and_inserts_row(self, conn, tmp_path, monkeypatch):
        audio_dir = tmp_path / 'audio'
        monkeypatch.setattr(recorder, 'AUDIO_DIR', audio_dir)

        started = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)
        rel = audio_rel_path(started)
        part = audio_dir / (rel + '.part')
        part.parent.mkdir(parents=True)
        writer = wave.open(str(part), 'wb')
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(recorder.SAMPLE_RATE)
        cap = Capture(
            part_path=part,
            rel_path=rel,
            started=started,
            writer=writer,
            freq_hz=146520000,
            mode='FM',
            dcd_main=1,
            lat=39.7,
            lon=-105.2,
        )

        block = array('h', [8192, -8192] * (recorder.BLOCK_FRAMES // 2)).tobytes()
        sq, peak, n = block_energy(block)
        cap.write(block, sq, peak, n)
        cap.close(conn)

        final = audio_dir / rel
        assert final.exists() and not part.exists()
        with wave.open(str(final), 'rb') as r:
            assert r.getnframes() == recorder.BLOCK_FRAMES
            assert r.getframerate() == recorder.SAMPLE_RATE

        row = conn.execute('SELECT * FROM radio_transmissions').fetchone()
        assert row['started_utc'] == '2026-07-18T12:00:00.000Z'
        assert row['ended_utc'] == '2026-07-18T12:00:00.100Z'
        assert row['duration_s'] == pytest.approx(0.1)
        assert row['freq_hz'] == 146520000
        assert row['mode'] == 'FM'
        assert row['dcd_main'] == 1
        assert row['audio_path'] == rel
        assert (row['lat'], row['lon']) == (39.7, -105.2)
        # ±8192 constant amplitude = quarter scale ≈ −12.0 dBFS for both stats.
        assert row['peak_dbfs'] == pytest.approx(-12.0, abs=0.1)
        assert row['rms_dbfs'] == pytest.approx(-12.0, abs=0.1)
        # One block → WAVEFORM_SUBBLOCKS bars (sub-block sampling), each the same
        # constant amplitude; ≈−12 dBFS in the −64 window ≈ 207/255.
        assert json.loads(row['waveform']) == [207] * WAVEFORM_SUBBLOCKS
        assert row['freq_b_hz'] is None  # a normal single-band capture

    def test_open_capture_infers_repeater_pair(self, conn, tmp_path, monkeypatch):
        monkeypatch.setattr(recorder, 'AUDIO_DIR', tmp_path / 'audio')
        monkeypatch.setattr(recorder, 'rig_snapshot', lambda: (None, None, None))  # Repeater Mode
        freqstate.remember_main(conn, 146520000, 'FM')
        freqstate.remember_staged(conn, 146520000, 445000000)

        cap = recorder.open_capture(conn, deque())
        assert cap.freq_hz == 146520000
        assert cap.mode == 'FM'
        assert cap.freq_b_hz == 445000000  # the RX audio is the A+B mix

    def test_open_capture_remembers_live_read(self, conn, tmp_path, monkeypatch):
        monkeypatch.setattr(recorder, 'AUDIO_DIR', tmp_path / 'audio')
        monkeypatch.setattr(recorder, 'rig_snapshot', lambda: (147120000, 'FM', 1))  # normal mode
        recorder.open_capture(conn, deque())
        # A live read seeds the store, so a later blocked read can infer it.
        assert freqstate.infer_freq(conn) == (147120000, 'FM', None)

    def test_ram_buffer_materializes_byte_exact(self, conn, tmp_path, monkeypatch):
        audio_dir = tmp_path / 'audio'
        monkeypatch.setattr(recorder, 'AUDIO_DIR', audio_dir)
        started = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)
        rel = audio_rel_path(started)
        cap = Capture(
            part_path=audio_dir / (rel + '.part'),
            rel_path=rel,
            started=started,
            freq_hz=None,
            mode=None,
            dcd_main=None,
            lat=None,
            lon=None,
        )

        first = array('h', [8192, -8192] * (recorder.BLOCK_FRAMES // 2)).tobytes()
        second = array('h', [4096, -4096] * (recorder.BLOCK_FRAMES // 2)).tobytes()
        cap.write(first, *block_energy(first))
        assert not cap.part_path.exists()  # a pending capture never touches disk
        cap.materialize()
        assert cap.part_path.exists() and not cap.buffer
        cap.write(second, *block_energy(second))
        cap.close(conn)

        with wave.open(str(audio_dir / rel), 'rb') as r:
            assert r.getnframes() == 2 * recorder.BLOCK_FRAMES
            assert r.readframes(r.getnframes()) == first + second
        row = conn.execute('SELECT * FROM radio_transmissions').fetchone()
        assert row['duration_s'] == pytest.approx(0.2)
        assert row['peak_dbfs'] == pytest.approx(-12.0, abs=0.1)  # buffered stats folded

    def test_note_dcd_any_open_reading_sticks(self, tmp_path):
        cap = _pending_capture(tmp_path)
        cap.note_dcd(False)
        assert cap.dcd_main == 0
        cap.note_dcd(True)
        assert cap.dcd_main == 1
        cap.note_dcd(False)
        assert cap.dcd_main == 1  # any open reading is sticky

    def test_row_survives_audio_prune(self, conn, tmp_path, monkeypatch):
        audio_dir = tmp_path / 'audio'
        monkeypatch.setattr(recorder, 'AUDIO_DIR', audio_dir)
        monkeypatch.setattr(recorder, 'MAX_AGE_DAYS', 0.0)

        rel = '2026-07/x.wav'
        f = audio_dir / rel
        f.parent.mkdir(parents=True)
        f.write_bytes(b'RIFF')
        os.utime(f, (1000.0, 1000.0))
        conn.execute(
            'INSERT INTO radio_transmissions '
            '(started_utc, ended_utc, duration_s, audio_path) VALUES (?, ?, ?, ?)',
            ('2026-07-18T12:00:00.000Z', '2026-07-18T12:00:01.000Z', 1.0, rel),
        )
        recorder.prune_audio(conn)

        assert not f.exists()
        row = conn.execute('SELECT * FROM radio_transmissions').fetchone()
        assert row is not None and row['audio_path'] is None


def test_schema_has_started_index(conn):
    names = {
        r['name']
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE tbl_name = 'radio_transmissions'"
        )
    }
    assert 'radio_transmissions' in names
    assert 'idx_radio_transmissions_started' in names
