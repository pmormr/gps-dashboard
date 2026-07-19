"""Tests for canonical timestamp normalization.

The load-bearing invariant is that every column stores a *fixed-width*
millisecond-UTC string so lexical ordering equals chronological ordering — the
``'.'`` < ``'Z'`` hazard the module docstring warns about. These tests pin the
width, the UTC conversion, and the lexical==chronological property.
"""

from datetime import UTC, datetime

import pytest

from common.timefmt import (
    age_seconds,
    canonical_timestamp,
    epoch_seconds,
    format_canonical,
    now_canonical,
    parse_iso,
)

_CANONICAL_LEN = len('2026-06-09T14:55:55.200Z')


def test_canonical_fixed_width():
    assert len(canonical_timestamp('2026-06-09T14:55:55Z')) == _CANONICAL_LEN


def test_canonical_appends_milliseconds_and_z():
    assert canonical_timestamp('2026-06-09T14:55:55Z') == '2026-06-09T14:55:55.000Z'


def test_canonical_treats_naive_as_utc():
    # A naive timestamp (no offset, no Z) is treated as already UTC.
    assert canonical_timestamp('2026-06-09T14:55:55') == '2026-06-09T14:55:55.000Z'


def test_canonical_normalizes_explicit_offset_to_utc():
    assert canonical_timestamp('2026-06-09T08:55:55-06:00') == '2026-06-09T14:55:55.000Z'


def test_canonical_truncates_sub_millisecond_fraction():
    # microsecond // 1000 truncates toward zero, never rounds up.
    assert canonical_timestamp('2026-06-09T14:55:55.999999Z') == '2026-06-09T14:55:55.999Z'
    assert canonical_timestamp('2026-06-09T14:55:55.2Z') == '2026-06-09T14:55:55.200Z'


def test_canonical_raises_on_garbage():
    with pytest.raises(ValueError):
        canonical_timestamp('not-a-timestamp')


def test_lexical_order_matches_chronological_order():
    # The crux: sorting the canonical strings must agree with sorting the
    # datetimes — including a whole-second value adjacent to a sub-second one.
    moments = [
        datetime(2026, 6, 9, 14, 55, 55, 0, tzinfo=UTC),
        datetime(2026, 6, 9, 14, 55, 55, 200_000, tzinfo=UTC),
        datetime(2026, 6, 9, 14, 55, 56, 0, tzinfo=UTC),
        datetime(2026, 6, 9, 14, 55, 54, 999_000, tzinfo=UTC),
        datetime(2026, 6, 10, 0, 0, 0, 0, tzinfo=UTC),
    ]
    canon = [format_canonical(m) for m in moments]
    by_time = [c for _, c in sorted(zip(moments, canon, strict=True))]
    assert sorted(canon) == by_time


def test_now_canonical_is_canonical_width():
    assert len(now_canonical()) == _CANONICAL_LEN


def test_parse_iso_roundtrips_canonical():
    # parse_iso is the inverse of format_canonical: formatting its result must
    # reproduce the original canonical string.
    ts = '2026-06-09T14:55:55.200Z'
    assert format_canonical(parse_iso(ts)) == ts


def test_parse_iso_returns_utc_aware():
    dt = parse_iso('2026-06-09T08:55:55-06:00')
    assert dt.tzinfo is UTC
    assert (dt.hour, dt.minute) == (14, 55)


def test_parse_iso_treats_naive_as_utc():
    dt = parse_iso('2026-06-09T14:55:55')
    assert dt == datetime(2026, 6, 9, 14, 55, 55, tzinfo=UTC)


def test_epoch_seconds_matches_datetime_timestamp():
    ts = '2026-06-09T14:55:55.200Z'
    assert epoch_seconds(ts) == datetime(2026, 6, 9, 14, 55, 55, 200_000, tzinfo=UTC).timestamp()


def test_age_seconds_positive_for_past():
    now = datetime(2026, 6, 9, 15, 0, 0, tzinfo=UTC)
    assert age_seconds('2026-06-09T14:55:00.000Z', now) == 300.0


def test_age_seconds_negative_for_future():
    now = datetime(2026, 6, 9, 14, 55, 0, tzinfo=UTC)
    assert age_seconds('2026-06-09T15:00:00.000Z', now) == -300.0


def test_parse_iso_raises_on_garbage():
    with pytest.raises(ValueError):
        parse_iso('not-a-timestamp')
