"""Tests for the OBD reader's pure logic (``sensors/obd_reader.py``).

The load-bearing contract is that the reader's poll columns stay in lockstep with
the shared ``obd`` schema (minus the derived ``fuel_rate_lph``), so a snapshot maps
cleanly onto ``obd_readings``. Also covers the synthetic reader, the link-state
classification (unplugged USB vs. out-of-socket vs. plugged in), the transition-only
status publishing, and the snapshot a full poll cycle publishes.
"""

from __future__ import annotations

import json
import logging

import obd
import pytest

from api.sensor_schema import READING_TABLES
from sensors import obd_reader
from sensors.obd_reader import (
    PID_SPECS,
    FakeReader,
    LinkState,
    ObdReader,
    _ProbeNoiseFilter,
    numeric,
    poll_loop,
)


class StubClient:
    """Captures publishes so ``poll_loop`` can be driven without a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, bool]] = []

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        """Record one publish."""
        self.published.append((topic, payload, retain))


class StubConn:
    """A python-OBD connection stub pinned to one connect-time status."""

    def __init__(self, status: str) -> None:
        self._status = status
        self.closed = False

    def status(self) -> str:
        """Return the pinned status."""
        return self._status

    def close(self) -> None:
        """Record the close."""
        self.closed = True


class StubReader:
    """A reader stub pinned to one link state, for driving the parked loop."""

    def __init__(self, link: LinkState) -> None:
        self.link = link
        self.probes = 0

    def probe(self) -> LinkState:
        """Return the pinned link state, counting calls."""
        self.probes += 1
        return self.link

    def pollable(self) -> bool:
        """Never pollable — the stub models an unplugged/parked link."""
        return False

    def voltage(self) -> float | None:
        """No voltage readable."""
        return None

    def poll(self, spec: object) -> float | None:
        """Never reached — the stub never becomes pollable."""
        return None

    def close(self) -> None:
        """No-op; the stub holds no resources."""


def test_reader_columns_match_schema() -> None:
    """Every polled column is an obd_readings metric, and the set is complete.

    ``fuel_rate_lph`` is the one schema column the reader does not poll (no PID on the
    speed-density Pentastar — derived in Phase 4), so it is the only allowed gap.
    """
    reader_columns = {spec.column for spec in PID_SPECS}
    schema_columns = set(READING_TABLES['obd']['metrics'])

    assert reader_columns <= schema_columns  # no column the writer can't store
    assert schema_columns - reader_columns == {'fuel_rate_lph'}


def test_pid_columns_unique() -> None:
    """No column is polled twice (would double-query the bus and last-write-wins)."""
    columns = [spec.column for spec in PID_SPECS]
    assert len(columns) == len(set(columns))


def test_fake_reader_yields_every_column() -> None:
    """The synthetic reader returns a numeric value for every spec (no NULL snapshot)."""
    reader = FakeReader()
    for spec in PID_SPECS:
        assert isinstance(reader.poll(spec), float)


def test_numeric_handles_none() -> None:
    """A None response coerces to None rather than raising."""
    assert numeric(None) is None


@pytest.mark.parametrize(
    ('status', 'expected', 'held'),
    [
        (obd.OBDStatus.NOT_CONNECTED, 'no_adapter', False),
        (obd.OBDStatus.ELM_CONNECTED, 'no_car', False),
        (obd.OBDStatus.OBD_CONNECTED, 'online', True),
        (obd.OBDStatus.CAR_CONNECTED, 'online', True),
    ],
)
def test_probe_classifies_link(
    monkeypatch: pytest.MonkeyPatch, status: str, expected: str, held: bool
) -> None:
    """The probe maps python-OBD's connect status onto the link vocabulary.

    Only a powered OBD socket (``OBD_CONNECTED``/``CAR_CONNECTED``) keeps the
    connection open; the failure classifications close it so the next wake re-probes.
    """
    conn = StubConn(status)
    monkeypatch.setattr(obd_reader.obd, 'OBD', lambda **kwargs: conn)
    reader = ObdReader(port='/dev/ttyUSB0', baud=None, protocol=None, fast=False)

    assert reader.probe() == expected
    assert conn.closed is (not held)


def test_pollable_requires_car_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pollable only once the ECUs negotiated a protocol (``CAR_CONNECTED``)."""
    reader = ObdReader(port='/dev/ttyUSB0', baud=None, protocol=None, fast=False)
    assert reader.pollable() is False  # no connection at all

    conn = StubConn(obd.OBDStatus.OBD_CONNECTED)
    monkeypatch.setattr(obd_reader.obd, 'OBD', lambda **kwargs: conn)
    assert reader.probe() == 'online'
    assert reader.pollable() is False  # socket powered, ignition off

    reader.close()
    conn = StubConn(obd.OBDStatus.CAR_CONNECTED)
    monkeypatch.setattr(obd_reader.obd, 'OBD', lambda **kwargs: conn)
    assert reader.probe() == 'online'
    assert reader.pollable() is True


def test_poll_loop_publishes_link_transition_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A persistent link failure publishes one retained status, not one per wake."""
    client = StubClient()
    reader = StubReader('no_adapter')

    def fake_sleep(seconds: float) -> None:
        if reader.probes >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(obd_reader.time, 'sleep', fake_sleep)
    with pytest.raises(KeyboardInterrupt):
        poll_loop(reader, client, 'sensors/van/obd', 'sensors/van/obd/status', once=False)

    assert reader.probes == 2
    assert client.published == [('sensors/van/obd/status', 'no_adapter', True)]


def test_probe_noise_filter_drops_benign_keeps_real() -> None:
    """The log filter suppresses python-OBD's probe chatter, keeps real errors."""
    noise_filter = _ProbeNoiseFilter()

    def record(message: str) -> logging.LogRecord:
        return logging.LogRecord('obd.elm327', logging.ERROR, __file__, 0, message, None, None)

    assert noise_filter.filter(record('Adapter connected, but the ignition is off')) is False
    assert noise_filter.filter(record('Failed to query protocol 0100: unable to connect')) is False
    assert noise_filter.filter(record('Cannot load commands: No connection to car')) is False
    assert noise_filter.filter(record('[Errno 2] could not open port /dev/ttyUSB0')) is False
    assert noise_filter.filter(record('OBD2 socket disconnected')) is False
    assert noise_filter.filter(record('Serial port /dev/ttyUSB0 disappeared')) is True


def test_poll_loop_publishes_full_snapshot() -> None:
    """One cycle publishes the link status plus a complete snapshot (ts + every column)."""
    client = StubClient()
    poll_loop(FakeReader(), client, 'sensors/van/obd', 'sensors/van/obd/status', once=True)

    assert len(client.published) == 2
    assert client.published[0] == ('sensors/van/obd/status', 'online', True)
    topic, raw, retain = client.published[1]
    assert topic == 'sensors/van/obd'
    assert retain is True
    payload = json.loads(raw)

    reader_columns = {spec.column for spec in PID_SPECS}
    assert set(payload) == {'ts', *reader_columns}
    assert 'fuel_rate_lph' not in payload  # derived later → ingest stores NULL
    assert all(isinstance(payload[col], int | float) for col in reader_columns)
