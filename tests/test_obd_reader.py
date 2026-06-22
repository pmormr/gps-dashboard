"""Tests for the OBD reader's pure logic (``sensors/obd.py``).

The load-bearing contract is that the reader's poll columns stay in lockstep with
the shared ``obd`` schema (minus the derived ``fuel_rate_lph``), so a snapshot maps
cleanly onto ``obd_readings``. Also covers the synthetic reader, the voltage gate,
and the snapshot a full poll cycle publishes.
"""

from __future__ import annotations

import json

from api.sensor_schema import READING_TABLES
from sensors.obd import PID_SPECS, FakeReader, engine_running, numeric, poll_loop


class StubClient:
    """Captures publishes so ``poll_loop`` can be driven without a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        """Record one publish."""
        self.published.append((topic, payload))


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


def test_engine_running_gate() -> None:
    """The voltage gate reads the fake (alternator voltage) as running."""
    assert engine_running(FakeReader()) is True


def test_numeric_handles_none() -> None:
    """A None response coerces to None rather than raising."""
    assert numeric(None) is None


def test_poll_loop_publishes_full_snapshot() -> None:
    """One cycle publishes a complete snapshot: ts + every polled column, all numeric."""
    client = StubClient()
    poll_loop(FakeReader(), client, 'sensors/van/obd', once=True)

    assert len(client.published) == 1
    topic, raw = client.published[0]
    assert topic == 'sensors/van/obd'
    payload = json.loads(raw)

    reader_columns = {spec.column for spec in PID_SPECS}
    assert set(payload) == {'ts', *reader_columns}
    assert 'fuel_rate_lph' not in payload  # derived later → ingest stores NULL
    assert all(isinstance(payload[col], int | float) for col in reader_columns)
