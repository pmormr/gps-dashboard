"""Tests for canonical timestamp normalization.

The load-bearing invariant is that every column stores a *fixed-width*
millisecond-UTC string so lexical ordering equals chronological ordering — the
``'.'`` < ``'Z'`` hazard the module docstring warns about. These tests pin the
width, the UTC conversion, and the lexical==chronological property.
"""

from datetime import datetime, timezone

import pytest

from api.db import _canonical, canonical_timestamp, now_canonical

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
        datetime(2026, 6, 9, 14, 55, 55, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 9, 14, 55, 55, 200_000, tzinfo=timezone.utc),
        datetime(2026, 6, 9, 14, 55, 56, 0, tzinfo=timezone.utc),
        datetime(2026, 6, 9, 14, 55, 54, 999_000, tzinfo=timezone.utc),
        datetime(2026, 6, 10, 0, 0, 0, 0, tzinfo=timezone.utc),
    ]
    canon = [_canonical(m) for m in moments]
    by_time = [c for _, c in sorted(zip(moments, canon))]
    assert sorted(canon) == by_time


def test_now_canonical_is_canonical_width():
    assert len(now_canonical()) == _CANONICAL_LEN
