"""Tests for the shared last-known-freq store (:mod:`radio.freqstate`)."""

from __future__ import annotations

import sqlite3

import pytest

from radio import freqstate


@pytest.fixture
def conn():
    """An in-memory DB with just the radio_freq_state row table."""
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute(
        'CREATE TABLE radio_freq_state ('
        'id INTEGER PRIMARY KEY CHECK (id = 1), last_freq_hz INTEGER, last_mode TEXT, '
        'staged_main_hz INTEGER, staged_other_hz INTEGER)'
    )
    return c


def test_empty_infers_nothing(conn):
    assert freqstate.infer_freq(conn) == (None, None, None)


def test_remember_main_then_infer(conn):
    freqstate.remember_main(conn, 146520000, 'FM')
    assert freqstate.infer_freq(conn) == (146520000, 'FM', None)


def test_remember_main_ignores_none_freq(conn):
    freqstate.remember_main(conn, None, None)
    assert freqstate.infer_freq(conn) == (None, None, None)


def test_staged_pair_shown_when_current(conn):
    freqstate.remember_main(conn, 146520000, 'FM')
    freqstate.remember_staged(conn, 146520000, 445000000)
    assert freqstate.infer_freq(conn) == (146520000, 'FM', 445000000)


def test_stale_pair_dropped_after_retune(conn):
    freqstate.remember_staged(conn, 146520000, 445000000)
    freqstate.remember_main(conn, 147000000, 'FM')  # retuned since staging
    assert freqstate.infer_freq(conn) == (147000000, 'FM', None)


def test_write_on_change_is_idempotent(conn):
    freqstate.remember_main(conn, 146520000, 'FM')
    freqstate.remember_main(conn, 146520000, 'FM')  # unchanged — no-op, no error
    assert freqstate.infer_freq(conn) == (146520000, 'FM', None)
