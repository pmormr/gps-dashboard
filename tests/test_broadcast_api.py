"""Flask-client tests for the broadcast config-reference read.

The render logic is covered in ``test_broadcast_feeds.py``; these exercise the
route end-to-end, confirming it reads secrets from the process environment
server-side (present → interpolated; absent → reported, never leaked as a crash).
"""

from __future__ import annotations

import pytest

import api.routes.broadcast as broadcast_route
from broadcast.feeds import FEEDS
from common.mediamtx import PathState


def test_feeds_endpoint_shape(client) -> None:
    res = client.get('/api/broadcast/feeds')
    assert res.status_code == 200
    body = res.get_json()
    assert len(body['feeds']) == len(FEEDS)
    assert 'missing_secrets' in body
    for f in body['feeds']:
        for key in ('path', 'hub', 'slot_group', 'transport', 'role', 'expected_tracks'):
            assert key in f


def test_secrets_interpolated_from_environment(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('GPS_BROADCAST_PHONE_PUB', 'PHONEPUB')
    monkeypatch.setenv('GPS_BROADCAST_SRT_PASSPHRASE', 'SRTPASS')
    monkeypatch.setenv('GPS_BROADCAST_DRONE_PUB', 'DRONEPUB')
    monkeypatch.setenv('GPS_BROADCAST_OBS_READ', 'OBSREAD')
    body = client.get('/api/broadcast/feeds').get_json()
    assert body['missing_secrets'] == []
    phone1 = next(f for f in body['feeds'] if f['path'] == 'phone1')
    assert 'PHONEPUB' in phone1['send']['streamid']
    assert 'OBSREAD' in phone1['obs_read']


def test_missing_secrets_reported_not_fatal(client, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        'GPS_BROADCAST_PHONE_PUB',
        'GPS_BROADCAST_SRT_PASSPHRASE',
        'GPS_BROADCAST_DRONE_PUB',
        'GPS_BROADCAST_OBS_READ',
    ):
        monkeypatch.delenv(key, raising=False)
    res = client.get('/api/broadcast/feeds')
    assert res.status_code == 200
    body = res.get_json()
    assert set(body['missing_secrets']) == {
        'GPS_BROADCAST_PHONE_PUB',
        'GPS_BROADCAST_SRT_PASSPHRASE',
        'GPS_BROADCAST_DRONE_PUB',
        'GPS_BROADCAST_OBS_READ',
    }
    # Van feeds still fully resolve — the config reference works with no env file.
    cam1 = next(f for f in body['feeds'] if f['path'] == 'cam1' and f['hub'] == 'van')
    assert cam1['missing_secrets'] == []


# --- /api/broadcast/status ---


def test_status_van_unreachable_is_not_fatal(client) -> None:
    """No hub under test → van reachable false, cloud false, never a 500."""
    res = client.get('/api/broadcast/status')
    assert res.status_code == 200
    body = res.get_json()
    assert body['hubs']['van']['reachable'] is False
    assert body['hubs']['cloud']['reachable'] is False
    van = [f for f in body['feeds'] if f['hub'] == 'van']
    assert van and all(f['reachable'] is False for f in van)


def test_status_merges_live_van_paths(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """A live SRT publisher on cam1 → ingest live + codec match; radio too."""

    def fake_fetch(*_a, **_k):
        return [
            PathState('cam1', True, True, True, 'srtConn', 'uuid', ('H264',), 0, 4096, 0),
            PathState('radio', True, True, True, 'rtspSession', 'rid', ('Opus',), 1, 8, 4),
        ]

    monkeypatch.setattr(broadcast_route, 'fetch_paths', fake_fetch)
    body = client.get('/api/broadcast/status').get_json()
    assert body['hubs']['van']['reachable'] is True
    by_key = {f'{f["hub"]}/{f["path"]}': f for f in body['feeds']}
    cam1 = by_key['van/cam1']
    assert cam1['ingest'] == 'live' and cam1['codec'] == 'match' and cam1['present'] is True
    radio = by_key['van/radio']
    assert radio['ingest'] == 'live' and radio['readers'] == 1
    # A van path not reported by the hub is present:false, not a crash.
    assert by_key['van/cam2']['present'] is False
    # Cloud feeds remain unreachable until P3.
    assert by_key['cloud/phone1']['reachable'] is False


# --- /api/broadcast/snapshot/<name> ---


def test_snapshot_unknown_path_404(client) -> None:
    assert client.get('/api/broadcast/snapshot/not-a-feed').status_code == 404
    # radio is audio-only → not snapshottable
    assert client.get('/api/broadcast/snapshot/radio').status_code == 404


def test_snapshot_warming_up_returns_202(client, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMgr:
        def request(self, _name):
            return None

    monkeypatch.setattr(broadcast_route, 'get_manager', lambda: FakeMgr())
    assert client.get('/api/broadcast/snapshot/cam1').status_code == 202


def test_snapshot_serves_jpeg(client, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    jpeg = tmp_path / 'cam1.jpg'
    jpeg.write_bytes(b'\xff\xd8jpegbytes')

    class FakeMgr:
        def request(self, _name):
            return jpeg

    monkeypatch.setattr(broadcast_route, 'get_manager', lambda: FakeMgr())
    res = client.get('/api/broadcast/snapshot/cam1')
    assert res.status_code == 200
    assert res.mimetype == 'image/jpeg'
    assert res.data == b'\xff\xd8jpegbytes'


# --- /api/broadcast/logs ---


def test_logs_van_returns_lines(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(broadcast_route.proc, 'run', lambda *a, **k: (0, 'lineA\nlineB', ''))
    body = client.get('/api/broadcast/logs').get_json()
    assert body['hub'] == 'van' and body['reachable'] is True
    assert body['lines'] == ['lineA', 'lineB']


def test_logs_journal_unreadable_is_not_fatal(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(broadcast_route.proc, 'run', lambda *a, **k: (1, '', 'boom'))
    body = client.get('/api/broadcast/logs').get_json()
    assert body['reachable'] is False and body['lines'] == []


def test_logs_cloud_unreachable_until_p3(client) -> None:
    body = client.get('/api/broadcast/logs?hub=cloud').get_json()
    assert body['hub'] == 'cloud' and body['reachable'] is False
