"""Flask-client tests for the broadcast config-reference read.

The render logic is covered in ``test_broadcast_feeds.py``; these exercise the
route end-to-end, confirming it reads secrets from the process environment
server-side (present → interpolated; absent → reported, never leaked as a crash).
"""

from __future__ import annotations

import pytest

from broadcast.feeds import FEEDS


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
