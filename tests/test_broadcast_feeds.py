"""Tests for the broadcast feed registry (broadcast/feeds.py).

Three surfaces: registry integrity (well-formed entries), the public-repo secret
guard (no secret literals in the committed module), and render interpolation (the
pure env-in → copy-ready-payload transform, including the derived single-URL forms).
"""

from __future__ import annotations

import re
from pathlib import Path

import broadcast.feeds as feeds_mod
from broadcast.feeds import (
    FEEDS,
    HUBS,
    ROLES,
    TRANSPORTS,
    Feed,
    SendSpec,
    env_keys,
    render_feeds,
)

#: A fake env resolving every referenced key to a recognisable, non-secret token.
FAKE_ENV = {
    'GPS_BROADCAST_PHONE_PUB': 'PHONEPUB',
    'GPS_BROADCAST_SRT_PASSPHRASE': 'SRTPASS',
    'GPS_BROADCAST_DRONE_PUB': 'DRONEPUB',
    'GPS_BROADCAST_OBS_READ': 'OBSREAD',
}


def _feed(hub: str, path: str) -> Feed:
    return next(f for f in FEEDS if f.hub == hub and f.path == path)


# --- Registry integrity ---


def test_feeds_present_and_grouped() -> None:
    """Every advertised path is in the registry, on the right hub."""
    van = {f.path for f in FEEDS if f.hub == 'van'}
    cloud = {f.path for f in FEEDS if f.hub == 'cloud'}
    assert {'cam1', 'cam2', 'saddle-1', 'saddle-2', 'radio'} <= van
    assert {'cam-front-main', 'cam-rear-main'} <= van
    assert {'phone1', 'phone2', 'phone3', 'phone4', 'phone5'} <= cloud
    assert {'drone1', 'drone2'} <= van and {'drone1', 'drone2'} <= cloud


def test_identity_is_hub_plus_path_unique() -> None:
    """`(hub, path)` is unique — drone1 legitimately appears on both hubs."""
    keys = [(f.hub, f.path) for f in FEEDS]
    assert len(keys) == len(set(keys))


def test_enums_and_required_fields() -> None:
    """Every feed uses valid enums and carries a non-empty codec pin."""
    for f in FEEDS:
        assert f.hub in HUBS
        assert f.transport in TRANSPORTS
        assert f.role in ROLES
        assert f.label
        assert f.expected_tracks, f'{f.hub}/{f.path} has no expected_tracks'
        # A publish feed sends; a proxy/internal feed does not.
        if f.role == 'publish':
            assert f.send is not None
        else:
            assert f.send is None


def test_cloud_feeds_are_authed_van_feeds_are_not() -> None:
    """Cloud feeds reference secrets; van feeds are secret-free (trusted LAN)."""
    for f in FEEDS:
        if f.hub == 'van':
            assert env_keys(f) == set(), f'van feed {f.path} must be secret-free'
        else:
            assert env_keys(f), f'cloud feed {f.path} should reference a secret'


def test_standby_matches_current_hub_reality() -> None:
    """STANDBY (alwaysAvailable) is cloud-only today; van paths are direct."""
    assert all(not f.standby for f in FEEDS if f.hub == 'van')
    assert all(f.standby for f in FEEDS if f.hub == 'cloud')


# --- Public-repo secret guard (B3) ---


def test_module_holds_no_secret_literals() -> None:
    """No secret-shaped token may be committed — the repo is public.

    Heuristic: the registry must contain only ``${ENV_KEY}`` placeholders, never a
    resolved secret, so no long hex run (the stream-key/passphrase shape) may
    appear. Deliberately does *not* embed the real secrets to check against — that
    would leak them into the public test instead.
    """
    src = Path(feeds_mod.__file__).read_text()
    hexish = re.findall(r'[0-9a-fA-F]{16,}', src)
    assert not hexish, f'possible secret literal(s) in feeds.py: {hexish}'


def test_referenced_env_keys_are_all_broadcast_scoped() -> None:
    """Every placeholder is a GPS_BROADCAST_* key (namespaced to this env file)."""
    for f in FEEDS:
        for key in env_keys(f):
            assert key.startswith('GPS_BROADCAST_'), key


# --- Render interpolation ---


def test_render_interpolates_and_reports_no_missing_when_env_complete() -> None:
    payload = render_feeds(FAKE_ENV)
    assert payload['missing_secrets'] == []
    assert len(payload['feeds']) == len(FEEDS)
    for f in payload['feeds']:
        assert f['missing_secrets'] == []
        assert '${' not in (f['obs_read'] or '')


def test_render_reports_missing_secrets_when_env_empty() -> None:
    payload = render_feeds({})
    # All four cloud creds surface as missing; van feeds contribute none.
    assert set(payload['missing_secrets']) == set(FAKE_ENV)
    van_cam = next(f for f in payload['feeds'] if f['path'] == 'cam1')
    assert van_cam['missing_secrets'] == []


def test_srt_single_url_van_camera() -> None:
    """Van SRT: streamid + latency, no passphrase (unencrypted LAN)."""
    payload = render_feeds(FAKE_ENV)
    cam1 = next(f for f in payload['feeds'] if f['path'] == 'cam1' and f['hub'] == 'van')
    assert cam1['send']['single_url'] == (
        'srt://192.168.42.178:8890?streamid=publish:cam1&latency=200'
    )
    assert cam1['send']['passphrase'] is None


def test_srt_single_url_cloud_phone_carries_passphrase() -> None:
    """Cloud SRT: streamid (with pub key) + latency + passphrase, interpolated."""
    payload = render_feeds(FAKE_ENV)
    p1 = next(f for f in payload['feeds'] if f['path'] == 'phone1')
    url = p1['send']['single_url']
    assert url == (
        'srt://ovh.pmormr.com:8890?'
        'streamid=publish:phone1:publisher:PHONEPUB&latency=2000&passphrase=SRTPASS'
    )
    assert p1['send']['streamid'] == 'publish:phone1:publisher:PHONEPUB'
    assert p1['send']['encryption'] == 'AES-128'


def test_rtmp_single_url_van_is_authless() -> None:
    """Van RTMP drone: bare path URL, no query creds."""
    payload = render_feeds(FAKE_ENV)
    d1 = next(f for f in payload['feeds'] if f['path'] == 'drone1' and f['hub'] == 'van')
    assert d1['send']['single_url'] == 'rtmp://192.168.42.178:1935/drone1'


def test_rtmp_single_url_cloud_carries_query_creds() -> None:
    """Cloud RTMP drone: creds in the query string (not userinfo), interpolated."""
    payload = render_feeds(FAKE_ENV)
    d1 = next(f for f in payload['feeds'] if f['path'] == 'drone1' and f['hub'] == 'cloud')
    assert d1['send']['single_url'] == (
        'rtmp://ovh.pmormr.com:1935/drone1?user=droneuser&pass=DRONEPUB'
    )


def test_obs_read_interpolated_for_cloud() -> None:
    payload = render_feeds(FAKE_ENV)
    p1 = next(f for f in payload['feeds'] if f['path'] == 'phone1')
    assert p1['obs_read'] == 'rtsp://obs:OBSREAD@ovh.pmormr.com:8554/phone1'


def test_sendspec_and_feed_are_frozen() -> None:
    """Registry entries are immutable (frozen dataclasses)."""
    import dataclasses

    import pytest

    f = FEEDS[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.label = 'x'  # type: ignore[misc]
    s = SendSpec(host='h', port=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.port = 2  # type: ignore[misc]
