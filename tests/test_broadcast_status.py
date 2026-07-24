"""Tests for the two-sides status derivation (broadcast/status.py).

Covers the ingest classifier (live / standby / idle), the codec badge, and the
danger flag — the "egress serving STANDBY while ingest is dead" case that is the
whole reason the status wall exists (B6).
"""

from __future__ import annotations

from broadcast.feeds import Feed
from broadcast.status import codec_badge, feed_status, ingest_state
from common.mediamtx import PathState


def _state(**kw) -> PathState:
    base = dict(
        name='x',
        ready=False,
        available=False,
        online=True,
        source_type=None,
        source_id=None,
        tracks=(),
        readers=0,
        bytes_received=0,
        bytes_sent=0,
    )
    base.update(kw)
    return PathState(**base)  # type: ignore[arg-type]


def _feed(standby: bool, expected: tuple[str, ...] = ('H264',)) -> Feed:
    return Feed(
        path='p',
        label='P',
        hub='cloud' if standby else 'van',
        slot_group='phones',
        transport='srt',
        role='publish',
        obs_read=None,
        standby=standby,
        expected_tracks=expected,
    )


# --- ingest classifier ---


def test_ingest_live_when_source_connected() -> None:
    s = _state(ready=True, source_type='srtConn', source_id='uuid', tracks=('H264',))
    assert ingest_state(_feed(standby=True), s) == 'live'


def test_ingest_standby_when_ready_but_no_publisher_on_standby_feed() -> None:
    """The masked case: alwaysAvailable path ready, but no real source attached."""
    s = _state(ready=True, source_type='redirect', source_id=None)
    assert ingest_state(_feed(standby=True), s) == 'standby'


def test_ingest_idle_when_not_ready() -> None:
    s = _state(ready=False)
    assert ingest_state(_feed(standby=True), s) == 'idle'


def test_non_standby_ready_but_disconnected_is_idle_not_standby() -> None:
    """Without alwaysAvailable there is no STANDBY state — dead ingest is idle."""
    s = _state(ready=True, source_id=None)
    assert ingest_state(_feed(standby=False), s) == 'idle'


# --- codec badge ---


def test_codec_match_is_order_independent() -> None:
    assert codec_badge(('H265', 'MPEG4Audio'), ('MPEG4Audio', 'H265')) == 'match'


def test_codec_mismatch() -> None:
    assert codec_badge(('H264',), ('H265',)) == 'mismatch'


def test_codec_unknown_when_no_live_tracks() -> None:
    assert codec_badge(('H264',), ()) == 'unknown'


# --- feed_status (the merged payload) ---


def test_status_absent_path_on_reachable_hub() -> None:
    st = feed_status(_feed(standby=False), None)
    assert st == {'reachable': True, 'present': False}


def test_status_danger_when_standby_with_readers() -> None:
    s = _state(ready=True, source_id=None, readers=2, bytes_sent=9999)
    st = feed_status(_feed(standby=True), s)
    assert st['ingest'] == 'standby'
    assert st['pulling'] is True
    assert st['danger'] is True


def test_status_no_danger_when_live_with_readers() -> None:
    s = _state(ready=True, source_type='srtConn', source_id='u', readers=2, tracks=('H264',))
    st = feed_status(_feed(standby=True), s)
    assert st['ingest'] == 'live'
    assert st['danger'] is False
    assert st['codec'] == 'match'


def test_status_no_danger_when_standby_but_no_readers() -> None:
    s = _state(ready=True, source_id=None, readers=0)
    st = feed_status(_feed(standby=True), s)
    assert st['ingest'] == 'standby'
    assert st['danger'] is False
