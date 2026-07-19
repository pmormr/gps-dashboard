"""Tests for the Victron reader's pure logic (``sensors/victron_reader.py``).

The load-bearing contract: the reader's snapshot columns stay in lockstep with the
shared ``victron`` schema, the Venus topic→column map only targets real columns, and a
publish tick emits a complete snapshot. Also covers the instance-wildcard topic
matching, the ``{"value": …}`` payload parsing, and the synthetic source.
"""

from __future__ import annotations

import json

from sensors.victron_reader import (
    TOPIC_MAP,
    VICTRON_COLUMNS,
    FakeSource,
    build_snapshot,
    column_for_topic,
    parse_value,
    read_snapshot,
)


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


def test_read_snapshot_emits_full_snapshot() -> None:
    """A fresh source yields a serialized snapshot: ts + every column, all numeric.

    The status-flip loop this feeds is tested once in ``test_runner`` — here we pin
    only the reader's freshness→snapshot mapping.
    """
    raw = read_snapshot(FakeSource())
    assert raw is not None
    payload = json.loads(raw)
    assert set(payload) == {'ts', *VICTRON_COLUMNS}
    assert all(isinstance(payload[col], int | float) for col in VICTRON_COLUMNS)
