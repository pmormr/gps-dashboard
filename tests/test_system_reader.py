"""Tests for the Pi host-metrics reader (``sensors/system_reader.py``).

The load-bearing contract is that the reader's snapshot columns stay in lockstep
with the shared ``system`` schema, so a snapshot maps cleanly onto
``system_readings``. Also covers the pure parse helpers (meminfo, throttled
bitmask, disk usage) and that every source degrades to None rather than raising
when it is absent (running off-Pi).
"""

from __future__ import annotations

from api.sensor_schema import READING_TABLES
from sensors.system_reader import (
    FakeSystemSensor,
    SystemSensor,
    disk_usage,
    parse_mem_used_pct,
    parse_throttled,
    read_cpu_temp_c,
    read_uptime_s,
    split_live_throttle,
)

SCHEMA_COLUMNS = set(READING_TABLES['system']['metrics'])


def test_reader_columns_match_schema() -> None:
    """Both the real and fake readers emit exactly the schema's columns."""
    real = SystemSensor().read()
    fake = FakeSystemSensor().read()
    assert real is not None and fake is not None
    assert set(real) == SCHEMA_COLUMNS
    assert set(fake) == SCHEMA_COLUMNS


def test_fake_reader_yields_every_column() -> None:
    """The synthetic reader returns a numeric value for every column (no NULLs)."""
    reading = FakeSystemSensor().read()
    assert reading is not None
    for column in SCHEMA_COLUMNS:
        assert isinstance(reading[column], int | float), column


def test_parse_mem_used_pct() -> None:
    """MemAvailable/MemTotal gives the used percent; missing fields → None."""
    meminfo = 'MemTotal:        8000000 kB\nMemAvailable:    2000000 kB\nMemFree: 1 kB\n'
    assert parse_mem_used_pct(meminfo) == 75.0
    assert parse_mem_used_pct('MemTotal: 8000000 kB\n') is None
    assert parse_mem_used_pct('') is None


def test_parse_throttled() -> None:
    """The vcgencmd bitmask parses from full or bare output; junk → None."""
    assert parse_throttled('throttled=0x0') == 0
    assert parse_throttled('throttled=0x50005') == 0x50005
    assert parse_throttled('0x0') == 0
    assert parse_throttled('') is None
    assert parse_throttled('throttled=nope') is None


def test_split_live_throttle() -> None:
    """Live bits map to their 0/1 channels; sticky bits are ignored; None passes through."""
    assert split_live_throttle(0) == {
        'undervolt_now': 0,
        'freq_capped_now': 0,
        'throttled_now': 0,
        'temp_limit_now': 0,
    }
    assert split_live_throttle(0x50005) == {
        'undervolt_now': 1,
        'freq_capped_now': 0,
        'throttled_now': 1,
        'temp_limit_now': 0,
    }
    # Sticky-only mask (post-event, condition cleared): every live channel reads 0.
    assert split_live_throttle(0xF0000) == {
        'undervolt_now': 0,
        'freq_capped_now': 0,
        'throttled_now': 0,
        'temp_limit_now': 0,
    }
    assert split_live_throttle(None) == {
        'undervolt_now': None,
        'freq_capped_now': None,
        'throttled_now': None,
        'temp_limit_now': None,
    }


def test_disk_usage_real() -> None:
    """Root filesystem returns a plausible percent and free space."""
    used_pct, free_gb = disk_usage('/')
    assert used_pct is not None and 0.0 <= used_pct <= 100.0
    assert free_gb is not None and free_gb >= 0.0


def test_missing_sources_degrade_to_none() -> None:
    """An absent source yields None for that metric rather than raising."""
    assert read_cpu_temp_c('/nonexistent/thermal') is None
    assert read_uptime_s('/nonexistent/uptime') is None
    assert disk_usage('/nonexistent/mount') == (None, None)
