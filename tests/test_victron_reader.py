"""Tests for the Victron reader's pure logic (``sensors/victron_reader.py``).

The load-bearing contract: the reader's snapshot columns stay in lockstep with the
shared ``victron`` schema, the Venus topic→column map only targets real columns, and a
publish tick emits a complete snapshot. Also covers the instance-wildcard topic
matching, the ``{"value": …}`` payload parsing, and the synthetic source.
"""

from __future__ import annotations

import json

from api.sensor_schema import READING_TABLES
from sensors.victron_reader import (
    TOPIC_MAP,
    VICTRON_COLUMNS,
    FakeSource,
    build_snapshot,
    column_for_topic,
    parse_value,
    publish_loop,
)


class StubClient:
    """Captures publishes so ``publish_loop`` can be driven without a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, topic: str, payload: str, qos: int = 0, retain: bool = False) -> None:
        """Record one publish."""
        self.published.append((topic, payload))


def test_columns_match_schema() -> None:
    """The snapshot column set is exactly the shared ``victron`` schema's metrics."""
    assert VICTRON_COLUMNS == READING_TABLES['victron']['metrics']


def test_columns_unique() -> None:
    """No column appears twice (would last-write-wins in the snapshot)."""
    assert len(VICTRON_COLUMNS) == len(set(VICTRON_COLUMNS))


def test_topic_map_targets_real_columns() -> None:
    """Every topic maps to a column the writer can store (no orphan targets)."""
    assert set(TOPIC_MAP.values()) <= set(VICTRON_COLUMNS)


def test_column_for_topic_wildcards_instance() -> None:
    """Lookup is instance-independent for device services; ``system`` aggregates map."""
    assert column_for_topic('system/0/Dc/Battery/Soc') == 'battery_soc'
    assert column_for_topic('solarcharger/279/Pv/V') == 'pv_voltage'
    assert column_for_topic('solarcharger/42/Pv/V') == 'pv_voltage'
    assert column_for_topic('vebus/276/State') == 'vebus_state'
    assert column_for_topic('solarcharger/279/History/Daily/0/Yield') == 'pv_yield_today_kwh'


def test_column_for_topic_ignores_unmapped() -> None:
    """Unmapped or malformed relative topics return None rather than raising."""
    assert column_for_topic('system/0/Serial') is None
    assert column_for_topic('battery/278/Soc') is None  # not in the map
    assert column_for_topic('weird') is None


def test_parse_value_extracts_number() -> None:
    """A Venus ``{"value": n}`` payload yields the number; null/strings yield None."""
    assert parse_value(b'{"value": 57.1}') == 57.1
    assert parse_value(b'{"value": 1}') == 1
    assert parse_value(b'{"value": null}') is None
    assert parse_value(b'{"value": "vebus"}') is None
    assert parse_value(b'not json') is None


def test_fake_source_yields_every_column() -> None:
    """The synthetic source returns a numeric value for every column."""
    snapshot = FakeSource().snapshot()
    assert set(snapshot) == set(VICTRON_COLUMNS)
    assert all(isinstance(value, int | float) for value in snapshot.values())


def test_build_snapshot_has_ts_and_all_columns() -> None:
    """The payload carries a timestamp plus every column (None when unseen)."""
    snapshot = build_snapshot({col: None for col in VICTRON_COLUMNS})
    assert set(snapshot) == {'ts', *VICTRON_COLUMNS}
    assert all(snapshot[col] is None for col in VICTRON_COLUMNS)


def test_publish_loop_emits_full_snapshot() -> None:
    """One fresh tick publishes a complete snapshot: ts + every column, all numeric."""
    client = StubClient()
    publish_loop(
        FakeSource(),
        client,
        'sensors/house/victron',
        'sensors/house/victron/status',
        once=True,
    )

    readings = [payload for topic, payload in client.published if topic == 'sensors/house/victron']
    assert len(readings) == 1
    payload = json.loads(readings[0])
    assert set(payload) == {'ts', *VICTRON_COLUMNS}
    assert all(isinstance(payload[col], int | float) for col in VICTRON_COLUMNS)


def test_publish_loop_flips_status_online_when_fresh() -> None:
    """A fresh source transitions the retained status from offline to online."""
    client = StubClient()
    publish_loop(
        FakeSource(),
        client,
        'sensors/house/victron',
        'sensors/house/victron/status',
        once=True,
    )

    statuses = [payload for topic, payload in client.published if topic.endswith('/status')]
    assert statuses == ['offline', 'online']
