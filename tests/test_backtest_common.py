"""Tests for the shared satellite-backtest helpers."""

from __future__ import annotations

from tools.backtest_common import percentile, split_holdout


def test_percentile_bounds_and_median() -> None:
    values = [5.0, 1.0, 4.0, 2.0, 3.0]
    assert percentile(values, 0.0) == 1.0
    assert percentile(values, 1.0) == 5.0
    assert percentile(values, 0.5) == 3.0


def test_split_holdout_partitions_earlier_and_later() -> None:
    # hold_frac=0.4 over 10 samples: 6 fit, 4 holdout, in original order. split_holdout
    # only slices/counts, so plain ints stand in for Sample (typed list[Any] here).
    samples: list = list(range(10))
    fit, hold = split_holdout(samples, hold_frac=0.4, min_fit=1)
    assert fit == list(range(6))
    assert hold == list(range(6, 10))


def test_split_holdout_too_short_returns_empty_fit() -> None:
    # Fewer fit samples than min_fit ⇒ empty split (skip this satellite).
    short: list = [1, 2, 3]
    fit, hold = split_holdout(short, hold_frac=0.3, min_fit=20)
    assert fit == []
    assert hold == []
