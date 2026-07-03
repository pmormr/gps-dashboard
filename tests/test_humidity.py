"""Tests for the derived moisture channels (``common/humidity.py``).

Anchors the Magnus math to textbook values, exercises the None/out-of-range
guards, and covers the payload enricher the ingest uses.
"""

from __future__ import annotations

import pytest

from common.humidity import (
    absolute_humidity_gm3,
    dew_point_c,
    heat_index_c,
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


def test_heat_index_nws_reference_points() -> None:
    """90 °F/70 % → ~106 °F and 86 °F/100 % → ~112 °F, matching the NWS table."""
    assert heat_index_c(32.22, 70.0) == pytest.approx(41.1, abs=0.2)
    assert heat_index_c(30.0, 100.0) == pytest.approx(44.4, abs=0.3)


def test_heat_index_below_regression_threshold_tracks_temp() -> None:
    """Under 80 °F the simple formula applies and stays near air temperature."""
    assert heat_index_c(25.0, 50.0) == pytest.approx(24.9, abs=0.1)


def test_heat_index_low_rh_adjustment() -> None:
    """The dry-air adjustment kicks in (100 °F/10 % → ~94.1 °F, not 94.8)."""
    assert heat_index_c(37.78, 10.0) == pytest.approx(34.5, abs=0.2)


def test_missing_or_unphysical_inputs_yield_none() -> None:
    """None inputs and RH outside (0, 100] return None rather than raising."""
    assert dew_point_c(None, 50.0) is None
    assert dew_point_c(20.0, None) is None
    assert dew_point_c(20.0, 0.0) is None
    assert dew_point_c(20.0, 120.0) is None
    assert absolute_humidity_gm3(None, None) is None
    assert absolute_humidity_gm3(20.0, -5.0) is None
    assert heat_index_c(None, 50.0) is None
    assert heat_index_c(20.0, 120.0) is None


def test_with_derived_humidity_enriches_payload() -> None:
    """The enricher adds both derived keys and leaves the source payload untouched."""
    payload = {'ts': 'x', 'temp_c': 20.0, 'humidity_pct': 50.0}
    out = with_derived_humidity(payload)
    assert out['dew_point_c'] == pytest.approx(9.26, abs=0.05)
    assert out['abs_humidity_gm3'] == pytest.approx(8.6, abs=0.1)
    assert out['heat_index_c'] == pytest.approx(19.4, abs=0.1)
    assert 'dew_point_c' not in payload


def test_with_derived_humidity_handles_absent_or_bad_inputs() -> None:
    """Missing or non-numeric temp/RH derive to None; node-sent values win."""
    assert with_derived_humidity({'temp_c': 20.0})['dew_point_c'] is None
    assert with_derived_humidity({'temp_c': 'hot', 'humidity_pct': 50.0})['dew_point_c'] is None
    sent = with_derived_humidity({'temp_c': 20.0, 'humidity_pct': 50.0, 'dew_point_c': 1.0})
    assert sent['dew_point_c'] == 1.0
