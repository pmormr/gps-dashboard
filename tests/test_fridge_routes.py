"""Tests for the fridge control routes (``api/routes/fridge.py``).

The routes are exercised through the real Flask app with
:class:`common.ddmp.DdmpClient` replaced by an in-memory fake — no fridge on the
LAN. The fake reproduces the context-manager shape and the subscribe/write surface
the routes use, with knobs for a busy/dead slot (connect failures), a NAKing
fridge, and canned publishes. DB-backed state (status snapshot, history) is
seeded straight into the test database.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

import api.routes.fridge as fridge
from common import ddmp
from common.ddmp import DdmpError

TS = '2026-07-13T17:00:00.000Z'


class FakeDdmp:
    """In-memory stand-in for :class:`common.ddmp.DdmpClient`."""

    def __init__(
        self,
        *,
        fail_enters: int = 0,
        nak: bool = False,
        published: dict[tuple[int, ...], list[list[int]]] | None = None,
    ) -> None:
        self.fail_enters = fail_enters
        self.nak = nak
        self.published = published or {}
        self.writes: list[tuple[tuple[int, ...], list[int]]] = []
        self.enters = 0

    def __enter__(self) -> FakeDdmp:
        self.enters += 1
        if self.enters <= self.fail_enters:
            raise DdmpError('cannot reach the fridge')
        return self

    def __exit__(self, *exc: object) -> None: ...

    def ping(self) -> None: ...

    def subscribe(self, param: list[int]) -> list[list[int]]:
        return self.published.get(tuple(param), [])

    def write(self, param: list[int], value_bytes: list[int]) -> None:
        self.writes.append((tuple(param), list(value_bytes)))
        if self.nak:
            raise DdmpError('fridge refused', refused=True)


@pytest.fixture(autouse=True)
def reset_live_cache(monkeypatch):
    """Empty the module-level firmware-constants cache for every test."""
    monkeypatch.setattr(fridge, '_live_constants', None)
    monkeypatch.setattr(fridge, '_next_fetch_after', 0.0)
    monkeypatch.setattr(fridge, 'WRITE_RETRY_DELAY_S', 0.0)


def _patch_client(monkeypatch, **kwargs) -> FakeDdmp:
    fake = FakeDdmp(**kwargs)
    monkeypatch.setattr(fridge, 'DdmpClient', lambda *a, **k: fake)
    return fake


def _range_publishes() -> dict[tuple[int, ...], list[list[int]]]:
    """Canned publishes for the ranges/unit fetch (the probed real values)."""
    allowed = [36, 255, 100, 0]  # -22.0 .. 10.0 °C
    recommended = [106, 255, 40, 0]  # -15.0 .. 4.0 °C
    out: dict[tuple[int, ...], list[list[int]]] = {}
    for zone in (0, 1):
        out[tuple(ddmp.temp_range_param(zone))] = [allowed]
        out[tuple(ddmp.recommended_range_param(zone))] = [recommended]
    out[tuple(ddmp.PRESENTED_UNIT_PARAM)] = [[1]]
    return out


def _seed(tmp_path: Path, *, history: bool = False) -> None:
    """Insert a fridge registry row, one reading, and (optionally) history rows."""
    conn = sqlite3.connect(tmp_path / 'test.db')
    conn.execute(
        'INSERT INTO sensors (node, type, first_seen, last_seen, status) '
        "VALUES ('van', 'fridge', ?, ?, 'online')",
        (TS, TS),
    )
    sensor_id = conn.execute("SELECT id FROM sensors WHERE type = 'fridge'").fetchone()[0]
    conn.execute(
        'INSERT INTO fridge_readings (sensor_id, timestamp, comp0_temp_c, dc_current_a) '
        'VALUES (?, ?, 2.0, 0.9)',
        (sensor_id, TS),
    )
    if history:
        recent = datetime.now(UTC).strftime('%Y-%m-%dT%H:00:00.000Z')
        rows = [
            (sensor_id, 'hour', recent, 1.5, TS),
            (sensor_id, 'hour', '2020-01-01T00:00:00.000Z', 9.9, TS),  # outside any window
            (sensor_id, 'day', recent, 0.5, TS),
        ]
        conn.executemany(
            'INSERT INTO fridge_history (sensor_id, span, bucket_ts, dc_current_a, updated_at) '
            'VALUES (?, ?, ?, ?, ?)',
            rows,
        )
    conn.commit()
    conn.close()


def test_status_without_fridge_row(client, monkeypatch):
    """No registry row: an honest all-unknown payload, still 200."""
    _patch_client(monkeypatch, fail_enters=99)
    data = client.get('/api/fridge/status').get_json()
    assert data['online'] is False
    assert data['status'] == 'unknown'
    assert data['reading'] is None
    assert data['ranges'] is None


def test_status_with_reading_and_ranges(client, tmp_path, monkeypatch):
    """Seeded snapshot + a reachable fridge: reading, ranges, and unit populate."""
    _seed(tmp_path)
    _patch_client(monkeypatch, published=_range_publishes())
    data = client.get('/api/fridge/status').get_json()
    assert data['online'] is True
    assert data['last_seen'] == TS
    assert data['reading']['comp0_temp_c'] == 2.0
    assert data['reading']['dc_current_a'] == 0.9
    assert data['reading']['timestamp'] == TS
    assert data['ranges']['comp0']['allowed'] == {'min_c': -22.0, 'max_c': 10.0}
    assert data['ranges']['comp1']['recommended'] == {'min_c': -15.0, 'max_c': 4.0}
    assert data['temp_unit'] == 1


def test_status_ranges_fetch_cooldown(client, monkeypatch):
    """A failed live fetch arms the cooldown: the next status poll skips the fridge."""
    fake = _patch_client(monkeypatch, fail_enters=99)
    assert client.get('/api/fridge/status').get_json()['ranges'] is None
    assert client.get('/api/fridge/status').get_json()['ranges'] is None
    assert fake.enters == 1  # second call cooled down, no new session


def test_status_ranges_cached_after_success(client, monkeypatch):
    """A successful fetch is cached for the process: one session, many polls."""
    fake = _patch_client(monkeypatch, published=_range_publishes())
    client.get('/api/fridge/status')
    client.get('/api/fridge/status')
    assert fake.enters == 1


def test_setpoint_writes_and_reads_back(client, monkeypatch):
    """The happy path: encoded ddegC write to the zone param, live read-back."""
    param = tuple(ddmp.setpoint_param(1))
    fake = _patch_client(monkeypatch, published={param: [[0x6A, 0xFF]]})
    resp = client.post('/api/fridge/setpoint', json={'zone': 1, 'temp_c': -15.0})
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'zone': 1, 'set_c': -15.0}
    assert fake.writes == [(param, [0x6A, 0xFF])]


def test_setpoint_validation(client, monkeypatch):
    """Zone and temp_c are typed-checked; bools are rejected like radio's idiom."""
    _patch_client(monkeypatch)
    assert client.post('/api/fridge/setpoint', json={'zone': 2, 'temp_c': 4}).status_code == 400
    assert client.post('/api/fridge/setpoint', json={'zone': True, 'temp_c': 4}).status_code == 400
    assert client.post('/api/fridge/setpoint', json={'zone': 0, 'temp_c': 'x'}).status_code == 400
    assert client.post('/api/fridge/setpoint', json={'zone': 0, 'temp_c': True}).status_code == 400
    assert client.post('/api/fridge/setpoint', json={}).status_code == 400


def test_setpoint_range_check_when_cache_warm(client, monkeypatch):
    """A warm ranges cache rejects out-of-range setpoints before touching the fridge."""
    fake = _patch_client(monkeypatch, published=_range_publishes())
    client.get('/api/fridge/status')  # warm the cache
    resp = client.post('/api/fridge/setpoint', json={'zone': 0, 'temp_c': 40.0})
    assert resp.status_code == 400
    assert fake.writes == []


def test_setpoint_nak_is_502(client, monkeypatch):
    """A fridge NAK is a refusal: 502, no retry."""
    fake = _patch_client(monkeypatch, nak=True)
    resp = client.post('/api/fridge/setpoint', json={'zone': 0, 'temp_c': 4.0})
    assert resp.status_code == 502
    assert fake.enters == 1


def test_setpoint_unreachable_is_503_after_retry(client, monkeypatch):
    """Two dead connects → 503; the second attempt proves the retry happened."""
    fake = _patch_client(monkeypatch, fail_enters=99)
    resp = client.post('/api/fridge/setpoint', json={'zone': 0, 'temp_c': 4.0})
    assert resp.status_code == 503
    assert fake.enters == 2


def test_setpoint_retry_covers_busy_slot(client, monkeypatch):
    """One dead connect (the poller holding the slot) then success → 200."""
    param = tuple(ddmp.setpoint_param(0))
    fake = _patch_client(monkeypatch, fail_enters=1, published={param: [[40, 0]]})
    resp = client.post('/api/fridge/setpoint', json={'zone': 0, 'temp_c': 4.0})
    assert resp.status_code == 200
    assert resp.get_json()['set_c'] == 4.0
    assert fake.enters == 2


def test_power_writes_and_reads_back(client, monkeypatch):
    """Zone power round-trip: bool byte write, live read-back, confirm-off scope."""
    param = tuple(ddmp.zone_power_param(1))
    fake = _patch_client(monkeypatch, published={param: [[0]]})
    resp = client.post('/api/fridge/power', json={'zone': 1, 'on': False})
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True, 'zone': 1, 'power': 0}
    assert fake.writes == [(param, [0])]


def test_power_validation(client, monkeypatch):
    """'on' must be a real boolean; zone rules match setpoint's."""
    _patch_client(monkeypatch)
    assert client.post('/api/fridge/power', json={'zone': 0, 'on': 1}).status_code == 400
    assert client.post('/api/fridge/power', json={'zone': 3, 'on': True}).status_code == 400
    assert client.post('/api/fridge/power', json={}).status_code == 400


def test_history_default_window_and_span_filter(client, tmp_path, monkeypatch):
    """The hour span's default trailing window keeps recent rows, drops 2020's."""
    _seed(tmp_path, history=True)
    _patch_client(monkeypatch, fail_enters=99)
    data = client.get('/api/fridge/history?span=hour').get_json()
    assert data['span'] == 'hour'
    assert data['bucket_s'] == ddmp.HISTORY_BUCKET_S['hour']
    assert [p['dc_current_a'] for p in data['points']] == [1.5]
    assert data['updated_at'] == TS


def test_history_explicit_range(client, tmp_path, monkeypatch):
    """Explicit start/end bounds override the default window."""
    _seed(tmp_path, history=True)
    _patch_client(monkeypatch, fail_enters=99)
    data = client.get(
        '/api/fridge/history?span=hour&start=2019-01-01T00:00:00Z&end=2021-01-01T00:00:00Z'
    ).get_json()
    assert [p['dc_current_a'] for p in data['points']] == [9.9]


def test_history_validation(client, monkeypatch):
    """Unknown spans and unparseable times are 400s."""
    _patch_client(monkeypatch, fail_enters=99)
    assert client.get('/api/fridge/history?span=month').status_code == 400
    assert client.get('/api/fridge/history?span=hour&start=not-a-time').status_code == 400


def test_history_empty_without_fridge_row(client, monkeypatch):
    """No registry row: an empty series, not an error."""
    _patch_client(monkeypatch, fail_enters=99)
    data = client.get('/api/fridge/history?span=week').get_json()
    assert data['points'] == []
    assert data['updated_at'] is None
