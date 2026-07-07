"""Tests for the DB-backup tool's pure helpers (retention selection + naming)."""

from __future__ import annotations

from datetime import date, timedelta

from tools.backup_db import dated_name, parse_dated_name, prune_keep


def test_dated_name_round_trip() -> None:
    """A built name parses back to the same day."""
    day = date(2026, 7, 7)
    assert dated_name(day) == 'gps_history-2026-07-07.db'
    assert parse_dated_name(dated_name(day)) == day


def test_parse_dated_name_rejects_foreign_files() -> None:
    """Non-dated entries (and impossible dates) are ignored, not pruned."""
    assert parse_dated_name('gps_history.snap.db') is None
    assert parse_dated_name('gps_history-2026-07-07.db.tmp') is None
    assert parse_dated_name('notes.txt') is None
    assert parse_dated_name('gps_history-2026-13-40.db') is None


def test_prune_keeps_recent_dailies() -> None:
    """The N most recent days survive outright."""
    days = [date(2026, 7, 7) - timedelta(days=i) for i in range(10)]
    keep = prune_keep(days, keep_daily=7, keep_weekly=0)
    assert keep == set(days[:7])


def test_prune_keeps_latest_per_week() -> None:
    """Each represented ISO week keeps its latest day."""
    # Two days in each of three consecutive weeks (Mon + Thu).
    days = []
    for week_start in (date(2026, 6, 15), date(2026, 6, 22), date(2026, 6, 29)):
        days += [week_start, week_start + timedelta(days=3)]
    keep = prune_keep(days, keep_daily=0, keep_weekly=2)
    # The two most recent weeks, each represented by its Thursday.
    assert keep == {date(2026, 6, 25), date(2026, 7, 2)}


def test_prune_weekly_counts_represented_weeks_not_calendar() -> None:
    """An off-grid gap doesn't age weekly history out."""
    days = [date(2026, 3, 2), date(2026, 7, 6)]  # months apart
    keep = prune_keep(days, keep_daily=0, keep_weekly=8)
    assert keep == set(days)


def test_prune_union_of_daily_and_weekly() -> None:
    """Daily and weekly keeps union; duplicates collapse."""
    days = [date(2026, 7, 7) - timedelta(days=i) for i in range(30)]
    keep = prune_keep(days, keep_daily=7, keep_weekly=8)
    assert set(days[:7]) <= keep
    # Every kept day beyond the daily window is its week's latest.
    for day in keep - set(days[:7]):
        week = day.isocalendar()[:2]
        assert day == max(d for d in days if d.isocalendar()[:2] == week)


def test_prune_empty_history() -> None:
    """No copies → nothing to keep, nothing to prune."""
    assert prune_keep([], keep_daily=7, keep_weekly=8) == set()
