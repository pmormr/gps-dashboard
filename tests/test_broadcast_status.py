"""Tests for the two-sides status derivation (broadcast/status.py).

Covers the ingest classifier (live / standby / idle), the codec badge, and the
danger flag — the "egress serving STANDBY while ingest is dead" case that is the
whole reason the status wall exists (B6).
"""

from __future__ import annotations

from broadcast.feeds import FEEDS, Feed
from broadcast.status import codec_badge, feed_status, ingest_state
from common.mediamtx import PathState, normalize_path

# Trimmed from the live cloud hub (vps202051, MediaMTX v1.19.2) captured over the
# WG tunnel in P3 — the empirical answer to the carried-over STANDBY-source
# question: an alwaysAvailable path with no live publisher reports source:null.
_CLOUD_CAPTURE = [
    {
        'name': 'phone1',
        'ready': True,
        'available': True,
        'online': False,
        'source': None,
        'tracks': ['H265', 'MPEG-4 Audio'],
        'readers': [],
        'bytesReceived': 2088454,
        'bytesSent': 0,
    },
    {
        'name': 'drone2',
        'ready': True,
        'available': True,
        'online': False,
        'source': None,
        'tracks': ['H264', 'MPEG-4 Audio'],
        'readers': [{'type': 'rtspSession', 'id': 'b494'}],
        'bytesReceived': 2508291,
        'bytesSent': 2292893,
    },
]


def _cloud_feed(path: str) -> Feed:
    return next(f for f in FEEDS if f.hub == 'cloud' and f.path == path)


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
    assert codec_badge(('H265', 'MPEG-4 Audio'), ('MPEG-4 Audio', 'H265')) == 'match'


def test_codec_mismatch() -> None:
    assert codec_badge(('H264',), ('H265',)) == 'mismatch'


def test_codec_match_tolerates_extra_live_tracks() -> None:
    # On-demand Dahua -main proxy: the camera's audio track rides alongside the
    # H265 video the video-only pin enumerates — not a mismatch.
    assert codec_badge(('H265',), ('H265', 'G711')) == 'match'


def test_codec_mismatch_when_expected_track_missing() -> None:
    assert codec_badge(('H265', 'MPEG-4 Audio'), ('H265',)) == 'mismatch'


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


def test_snapshotter_reader_is_discounted() -> None:
    """The wall's own RTSP snapshot pull must not read as a consumer or as danger."""
    s = _state(ready=True, source_id=None, readers=1)  # the only reader is our snapshotter
    st = feed_status(_feed(standby=True), s, self_readers=1)
    assert st['readers'] == 0
    assert st['pulling'] is False
    assert st['danger'] is False


def test_real_reader_survives_snapshotter_discount() -> None:
    """A real OBS reader alongside the snapshotter still counts (and is dangerous)."""
    s = _state(ready=True, source_id=None, readers=2)  # snapshotter + one real consumer
    st = feed_status(_feed(standby=True), s, self_readers=1)
    assert st['readers'] == 1
    assert st['danger'] is True


# --- codec badge is gated to a live source (standby loops carry their own tracks) ---


def test_codec_unknown_on_standby_even_with_tracks() -> None:
    """A STANDBY loop serves tracks, but they aren't the pinned real source."""
    s = _state(ready=True, source_id=None, tracks=('H265', 'MPEG-4 Audio'))
    st = feed_status(_feed(standby=True, expected=('H265', 'MPEG-4 Audio')), s)
    assert st['ingest'] == 'standby'
    assert st['codec'] == 'unknown'


def test_codec_match_on_live_phone_with_corrected_aac_string() -> None:
    """The live badge matches only with MediaMTX's real 'MPEG-4 Audio' string."""
    s = _state(ready=True, source_type='srtConn', source_id='u', tracks=('H265', 'MPEG-4 Audio'))
    st = feed_status(_feed(standby=True, expected=('H265', 'MPEG-4 Audio')), s)
    assert st['ingest'] == 'live'
    assert st['codec'] == 'match'


# --- live cloud-hub capture (the P3 STANDBY-source finding) ---


def test_live_capture_standby_source_is_null_not_a_publisher() -> None:
    """alwaysAvailable + no publisher → source:null → 'standby', not a false 'live'."""
    state = normalize_path(_CLOUD_CAPTURE[0])
    assert state.source_connected is False
    st = feed_status(_cloud_feed('phone1'), state)
    assert st['ingest'] == 'standby'
    assert st['codec'] == 'unknown'
    assert st['danger'] is False


def test_live_capture_standby_with_reader_is_danger() -> None:
    """The masked failure the wall exists to show: a reader pulling the STANDBY loop."""
    state = normalize_path(_CLOUD_CAPTURE[1])
    st = feed_status(_cloud_feed('drone2'), state)
    assert st['ingest'] == 'standby'
    assert st['readers'] == 1
    assert st['danger'] is True
