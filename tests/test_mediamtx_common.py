"""Tests for the shared MediaMTX control-API client (common/mediamtx.py).

Uses the real per-path shapes observed on the live van hub (v1.19.2): a live
publisher, an idle on-demand proxy, and an unpublished slot — the three cases the
two-sides status model discriminates on.
"""

from __future__ import annotations

import requests

from common.mediamtx import PathState, fetch_paths, normalize_path

# Real shapes captured from the van hub's /v3/paths/list.
_LIVE_SRT = {
    'name': 'cam1',
    'ready': True,
    'available': True,
    'online': True,
    'source': {'type': 'srtConn', 'id': '929306a0-0ec2-4e37-87a1-bbad57fdf786'},
    'tracks': ['H264'],
    'readers': [],
    'bytesReceived': 4096,
    'bytesSent': 0,
}
_IDLE_ONDEMAND = {
    'name': 'cam-front-main',
    'ready': False,
    'available': False,
    'online': True,
    'source': {'type': 'rtspSource', 'id': ''},  # configured type, empty id
    'tracks': [],
    'readers': [],
    'bytesReceived': 0,
    'bytesSent': 0,
}
_UNPUBLISHED = {
    'name': 'drone1',
    'ready': False,
    'available': False,
    'online': False,
    'source': None,
    'tracks': [],
    'readers': [],
    'bytesReceived': 0,
    'bytesSent': 0,
}


def test_live_publisher_is_source_connected() -> None:
    s = normalize_path(_LIVE_SRT)
    assert s.ready and s.source_type == 'srtConn'
    assert s.source_id == '929306a0-0ec2-4e37-87a1-bbad57fdf786'
    assert s.source_connected is True
    assert s.tracks == ('H264',)


def test_idle_ondemand_is_not_connected_despite_configured_source_type() -> None:
    """The key discriminator: configured source.type but EMPTY source.id."""
    s = normalize_path(_IDLE_ONDEMAND)
    assert s.source_type == 'rtspSource'  # the configured type is present…
    assert s.source_id is None  # …but no id, so nothing is attached
    assert s.source_connected is False
    assert s.ready is False


def test_unpublished_slot_has_no_source() -> None:
    s = normalize_path(_UNPUBLISHED)
    assert s.source_type is None
    assert s.source_id is None
    assert s.source_connected is False


def test_normalize_tolerates_missing_fields() -> None:
    s = normalize_path({'name': 'radio'})
    assert s == PathState(
        name='radio',
        ready=False,
        available=False,
        online=False,
        source_type=None,
        source_id=None,
        tracks=(),
        readers=0,
        bytes_received=0,
        bytes_sent=0,
    )


def test_fetch_paths_unreachable_returns_none(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise requests.ConnectionError('down')

    monkeypatch.setattr(requests, 'get', boom)
    assert fetch_paths(base='http://127.0.0.1:59997') is None


def test_fetch_paths_parses_items(monkeypatch) -> None:
    class _Resp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {'items': [_LIVE_SRT, _IDLE_ONDEMAND]}

    monkeypatch.setattr(requests, 'get', lambda *a, **k: _Resp())
    states = fetch_paths()
    assert states is not None
    assert [s.name for s in states] == ['cam1', 'cam-front-main']
    assert states[0].source_connected and not states[1].source_connected
