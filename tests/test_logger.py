"""Tests for the GPS logger's pure SKY-observation row builder."""

from typing import Any

from logger.gps_logger import sky_observation_rows


def test_keeps_only_positioned_sats() -> None:
    """Sats missing az or el carry no angular info and are dropped."""
    sats: list[dict[str, Any]] = [
        {'gnssid': 0, 'svid': 1, 'az': 10.0, 'el': 20.0, 'ss': 30.0, 'used': True, 'health': 1},
        {'gnssid': 6, 'svid': 5, 'ss': 25.0, 'used': False},  # no az/el
        {'gnssid': 2, 'svid': 9, 'az': None, 'el': 40.0},  # az missing
        {'gnssid': 3, 'svid': 7, 'az': 100.0},  # el missing
    ]
    rows = sky_observation_rows(sats, 'TS')
    assert rows == [('TS', 0, 1, 10.0, 20.0, 30.0, 1, 1)]


def test_el_zero_is_positioned() -> None:
    """A horizon-grazing sat (el==0.0) is positioned, not filtered as falsy."""
    rows = sky_observation_rows([{'gnssid': 0, 'svid': 2, 'az': 0.0, 'el': 0.0}], 'TS')
    assert len(rows) == 1


def test_used_coerced_and_optional_fields_default_none() -> None:
    """used becomes 0/1; absent snr/health stay None."""
    rows = sky_observation_rows([{'gnssid': 3, 'svid': 7, 'az': 100.0, 'el': 5.0}], 'TS')
    assert rows == [('TS', 3, 7, 100.0, 5.0, None, 0, None)]


def test_empty_array() -> None:
    """No satellites yields no rows."""
    assert sky_observation_rows([], 'TS') == []
