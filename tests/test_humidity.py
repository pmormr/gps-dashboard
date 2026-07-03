"""Tests for the derived moisture channels (``common/humidity.py``).

Anchors the Magnus math to textbook values, exercises the None/out-of-range
guards, and covers the two integration surfaces: the payload enricher the
ingest uses and the one-shot historical backfill in ``api.db.migrate``.
"""

from __future__ import annotations

import sqlite3

import pytest

from common.humidity import (
    absolute_humidity_gm3,
    dew_point_c,
    saturation_vapor_pressure_hpa,
    with_derived_humidity,
)


def test_saturation_vapor_pressure_reference_points() -> None:
    """es matches standard tables: ~6.1 hPa at 0 °C, ~23.4 hPa at 20 °C."""
    assert saturation_vapor_pressure_hpa(0.0) == pytest.approx(6.112, abs=0.01)
    assert saturation_vapor_pressure_hpa(20.0) == pytest.approx(23.4, abs=0.2)


def test_dew_point_textbook_values() -> None:
    """20 °C / 50 % RH → ~9.3 °C; saturation (100 %) → dew point = temperature."""
    assert dew_point_c(20.0, 50.0) == pytest.approx(9.26, abs=0.05)
    assert dew_point_c(20.0, 100.0) == pytest.approx(20.0, abs=0.01)
    assert dew_point_c(0.0, 80.0) == pytest.approx(-2.9, abs=0.2)


def test_absolute_humidity_textbook_values() -> None:
    """20 °C / 50 % RH → ~8.6 g/m³; 30 °C / 100 % → ~30 g/m³."""
    assert absolute_humidity_gm3(20.0, 50.0) == pytest.approx(8.6, abs=0.1)
    assert absolute_humidity_gm3(30.0, 100.0) == pytest.approx(30.4, abs=0.5)


def test_missing_or_unphysical_inputs_yield_none() -> None:
    """None inputs and RH outside (0, 100] return None rather than raising."""
    assert dew_point_c(None, 50.0) is None
    assert dew_point_c(20.0, None) is None
    assert dew_point_c(20.0, 0.0) is None
    assert dew_point_c(20.0, 120.0) is None
    assert absolute_humidity_gm3(None, None) is None
    assert absolute_humidity_gm3(20.0, -5.0) is None


def test_with_derived_humidity_enriches_payload() -> None:
    """The enricher adds both derived keys and leaves the source payload untouched."""
    payload = {'ts': 'x', 'temp_c': 20.0, 'humidity_pct': 50.0}
    out = with_derived_humidity(payload)
    assert out['dew_point_c'] == pytest.approx(9.26, abs=0.05)
    assert out['abs_humidity_gm3'] == pytest.approx(8.6, abs=0.1)
    assert 'dew_point_c' not in payload


def test_with_derived_humidity_handles_absent_or_bad_inputs() -> None:
    """Missing or non-numeric temp/RH derive to None; node-sent values win."""
    assert with_derived_humidity({'temp_c': 20.0})['dew_point_c'] is None
    assert with_derived_humidity({'temp_c': 'hot', 'humidity_pct': 50.0})['dew_point_c'] is None
    sent = with_derived_humidity({'temp_c': 20.0, 'humidity_pct': 50.0, 'dew_point_c': 1.0})
    assert sent['dew_point_c'] == 1.0


def test_migration_backfills_existing_rows() -> None:
    """A pre-migration DB gets its bme680 rows backfilled exactly once."""
    from api.db import init_db, migrate

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    init_db(conn)
    migrate(conn)
    # Simulate a pre-migration DB: drop the derived columns, insert history.
    conn.execute('ALTER TABLE bme680_readings DROP COLUMN dew_point_c')
    conn.execute('ALTER TABLE bme680_readings DROP COLUMN abs_humidity_gm3')
    conn.execute(
        'INSERT INTO bme680_readings (sensor_id, timestamp, temp_c, humidity_pct) '
        "VALUES (1, '2026-07-01T00:00:00.000Z', 20.0, 50.0), "
        "(1, '2026-07-01T00:00:30.000Z', NULL, 50.0)"
    )
    migrate(conn)

    filled, empty = conn.execute(
        'SELECT dew_point_c, abs_humidity_gm3 FROM bme680_readings ORDER BY id'
    ).fetchall()
    assert filled['dew_point_c'] == pytest.approx(9.26, abs=0.05)
    assert filled['abs_humidity_gm3'] == pytest.approx(8.6, abs=0.1)
    assert empty['dew_point_c'] is None
    conn.close()
