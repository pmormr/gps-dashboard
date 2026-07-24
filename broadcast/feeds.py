"""The broadcast feed registry: every MediaMTX path on both hubs, declaratively.

Declarative source of truth (the ``updater/chunks.py`` / ``api/sensor_schema.py``
pattern): one :class:`Feed` per path across the **van** hub (``pmpi1``, trusted
LAN, no auth) and the **cloud** hub (``vps202051`` / ``ovh.pmormr.com``, public,
authed). It replaces the hand-maintained event quick-ref — the grab-and-go
config every send-side app + OBS needs on race day, plus the pins that drive the
live-status codec badge.

**Public-repo constraint (B3):** this file is committed to a *public* GitHub repo,
so it holds **only** ``${ENV_KEY}`` placeholders — never a secret literal. The
real values live in ``/etc/default/gps-broadcast`` on the Pi (root-600,
``EnvironmentFile`` on ``gps-dashboard.service``); :func:`render_feeds` reads them
server-side and interpolates finished, copy-ready strings for the trusted LAN.

The single-URL send forms and the set of referenced env keys are **derived**
(:func:`render_feeds` / :func:`env_keys`) rather than stored, so the fielded and
single-URL views can never drift and there is no hand-kept secret-ref list.

See ``plans/broadcast-dashboard-plan.md`` (B2/B3) and the mirror docs:
``paul-network-docs`` ``events/2026-hillclimb-quickref.md`` + the two device pages.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Hubs a feed can live on. ``van`` = trusted LAN (no auth); ``cloud`` = public
#: internet (authed, reached over the WG control tunnel for status — B7).
HUBS = ('van', 'cloud')

#: Transport a publisher/consumer uses to reach the path. ``internal`` = published
#: by a hub-local process (radio), nothing external to configure.
TRANSPORTS = ('srt', 'rtmp', 'rtsp-pull', 'internal')

#: A feed's role at the hub. ``publish`` = an external source sends to it;
#: ``proxy`` = the hub pulls it on-demand (Dahua B-roll); ``internal`` = a
#: hub-local publisher feeds it (radio).
ROLES = ('publish', 'proxy', 'internal')

#: Matches an ``${ENV_KEY}`` placeholder in a template string.
_PLACEHOLDER = re.compile(r'\$\{(\w+)\}')


@dataclass(frozen=True)
class SendSpec:
    """Publisher-side send config — the fields you paste into the sending app.

    Only the fields relevant to the feed's transport are set; :func:`render_feeds`
    interpolates the ``${ENV_KEY}`` placeholders and derives the single-URL form.

    Attributes:
        host: Publish host (LAN IP for the van, ``ovh.pmormr.com`` for the cloud).
        port: Publish port (SRT ``8890`` / RTMP ``1935``).
        streamid: SRT stream id template (``publish:…``); None for RTMP.
        passphrase: SRT passphrase template; None when unencrypted (van LAN).
        latency_ms: SRT latency buffer; None for RTMP.
        encryption: SRT encryption label (e.g. ``AES-128``); None when none.
        rtmp_query: RTMP credential query template (``?user=…&pass=…``); None for
            SRT and for the no-auth van RTMP.
    """

    host: str
    port: int
    streamid: str | None = None
    passphrase: str | None = None
    latency_ms: int | None = None
    encryption: str | None = None
    rtmp_query: str | None = None


@dataclass(frozen=True)
class Feed:
    """One MediaMTX path on one hub — its config and status expectations.

    Attributes:
        path: The MediaMTX path name (matches the control API's ``name`` and
            ``deploy/mediamtx.yml``). Not unique on its own — ``drone1`` exists on
            both hubs; ``(hub, path)`` is the identity.
        label: Human label for the card/tile.
        hub: One of :data:`HUBS`.
        slot_group: UI grouping key (``cameras``/``phones``/``drones``/``radio``/
            ``security``).
        transport: One of :data:`TRANSPORTS`.
        role: One of :data:`ROLES`.
        send: Publisher-side config, or None for pull/internal feeds.
        obs_read: The RTSP URL template OBS pulls (``${ENV}`` for cloud creds).
        browser_url: The hub's WebRTC page (H.264/Opus feeds only), or None where
            the browser can't decode the codec (cloud WebRTC off; H.265 mains).
        standby: Whether the path is ``alwaysAvailable`` — it stays ``ready`` and
            serves a STANDBY loop with no live publisher, so a dead ingest can hide
            behind a healthy egress (the B6 failure mode the status wall surfaces).
        expected_tracks: The pinned/expected codec-name list (``('H265',
            'MPEG4Audio')``), compared against the control API's live ``tracks`` for
            the codec badge.
        notes: Field gotchas surfaced verbatim on the card (streamid ``&``
            truncation, RTMP query-string auth, mute-drone-audio, …).
    """

    path: str
    label: str
    hub: str
    slot_group: str
    transport: str
    role: str
    obs_read: str | None
    standby: bool
    expected_tracks: tuple[str, ...]
    send: SendSpec | None = None
    browser_url: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


# --- Reusable hosts / env placeholders (structure only — no secret values) ---

_VAN = '192.168.42.178'
_CLOUD = 'ovh.pmormr.com'

#: Env keys resolved from /etc/default/gps-broadcast at render time (B3).
_PHONE_PUB = '${GPS_BROADCAST_PHONE_PUB}'  # cloud phone SRT publish key (user `publisher`)
_SRT_PASS = '${GPS_BROADCAST_SRT_PASSPHRASE}'  # cloud phone SRT passphrase
_DRONE_PUB = '${GPS_BROADCAST_DRONE_PUB}'  # cloud drone RTMP publish key (user `droneuser`)
_OBS_READ = '${GPS_BROADCAST_OBS_READ}'  # cloud OBS RTSP read key (user `obs`)


def _van_cameras() -> list[Feed]:
    """The four PtP course cameras (van hub, SRT publish, no auth)."""
    return [
        Feed(
            path=f'cam{n}',
            label=f'PtP cam {n}',
            hub='van',
            slot_group='cameras',
            transport='srt',
            role='publish',
            send=SendSpec(host=_VAN, port=8890, streamid=f'publish:cam{n}', latency_ms=200),
            obs_read=f'rtsp://{_VAN}:8554/cam{n}',
            browser_url=f'http://{_VAN}:8889/cam{n}',
            standby=False,
            expected_tracks=('H264',),
            notes=(
                'H.264 720p ~2.5 Mbps. cam1 = picam1, already running as the '
                'cam-stream@cam1 service; values here are for a manual re-publish.',
            ),
        )
        for n in (1, 2, 3, 4)
    ]


def _van_dahua_broll() -> list[Feed]:
    """The four van Dahua cams as OBS-pullable B-roll (van hub, on-demand proxy).

    Modelled per position at the full-res H.265 ``-main`` path (best quality for
    a cut-in); the lighter H.264 ``cam-<pos>`` / ``cam-<pos>-hd`` variants + their
    browser URLs ride in ``notes``. These are ``sourceOnDemand`` — idle
    (``ready:false``) until pulled, which is normal, not a fault.
    """
    positions = [
        ('cam-front-main', 'Cam · Front', 'front', '+ PCM audio'),
        ('cam-rear-main', 'Cam · Rear', 'rear', '+ G.711 audio'),
        ('cam-blind-left-main', 'Cam · Blind L', 'blind-left', 'video only'),
        ('cam-blind-right-main', 'Cam · Blind R', 'blind-right', 'video only'),
    ]
    feeds = []
    for path, label, pos, audio in positions:
        feeds.append(
            Feed(
                path=path,
                label=label,
                hub='van',
                slot_group='security',
                transport='rtsp-pull',
                role='proxy',
                send=None,
                obs_read=f'rtsp://{_VAN}:8554/{path}',
                browser_url=None,  # H.265 main — WebRTC can't decode it
                standby=False,
                expected_tracks=('H265',),
                notes=(
                    f'Full-res H.265 ({audio}) — RTSP/OBS only. Lighter H.264: '
                    f'cam-{pos} (D1) + cam-{pos}-hd (720p), browser '
                    f'http://{_VAN}:8889/cam-{pos} or …-hd.',
                    'On-demand: pulled direct from the camera only while viewed; '
                    'NVR recording is untouched.',
                ),
            )
        )
    return feeds


def _cloud_phones() -> list[Feed]:
    """The five phone slots (cloud hub, SRT publish, authed + AES-128)."""
    feeds = []
    for n in (1, 2, 3, 4, 5):
        feeds.append(
            Feed(
                path=f'phone{n}',
                label="Paul's phone" if n == 1 else f'Phone {n}',
                hub='cloud',
                slot_group='phones',
                transport='srt',
                role='publish',
                send=SendSpec(
                    host=_CLOUD,
                    port=8890,
                    streamid=f'publish:phone{n}:publisher:{_PHONE_PUB}',
                    passphrase=_SRT_PASS,
                    latency_ms=2000,
                    encryption='AES-128',
                ),
                obs_read=f'rtsp://obs:{_OBS_READ}@{_CLOUD}:8554/phone{n}',
                browser_url=None,  # cloud WebRTC off; preview is a snapshot (B9)
                standby=True,
                expected_tracks=('H265', 'MPEG4Audio'),
                notes=(
                    'App: IRL Pro (Android) / Moblin (iOS) → custom SRT (NOT SRTLA), '
                    'mode Caller. H.264/H.265 720p–1080p 30 fps ~2.5–4 Mbps.',
                    'Field apps: the Stream ID field is ONLY publish:phoneN:publisher:'
                    '<key> — a stray & truncates it and the server rejects the publish.',
                ),
            )
        )
    return feeds


def _drones() -> list[Feed]:
    """Both drone slots on both hubs (van RTMP no-auth · cloud RTMP authed)."""
    feeds = []
    for n in (1, 2):
        event = ' (event)' if n == 2 else ' (spare)'
        feeds.append(
            Feed(
                path=f'drone{n}',
                label=f'Drone {n} · van WiFi{event}',
                hub='van',
                slot_group='drones',
                transport='rtmp',
                role='publish',
                send=SendSpec(host=_VAN, port=1935),
                obs_read=f'rtsp://{_VAN}:8554/drone{n}',
                browser_url=f'http://{_VAN}:8889/drone{n}',
                standby=False,
                expected_tracks=('H264',),
                notes=(
                    'DJI app → Transmission / Live Streaming → Custom RTMP. On the van '
                    'LAN → this van hub, no auth. Mute the DJI live audio '
                    '(Transmission → audio off) so it publishes single-track H.264.',
                ),
            )
        )
        feeds.append(
            Feed(
                path=f'drone{n}',
                label=f'Drone {n} · cellular{event}',
                hub='cloud',
                slot_group='drones',
                transport='rtmp',
                role='publish',
                send=SendSpec(
                    host=_CLOUD, port=1935, rtmp_query=f'?user=droneuser&pass={_DRONE_PUB}'
                ),
                obs_read=f'rtsp://obs:{_OBS_READ}@{_CLOUD}:8554/drone{n}',
                browser_url=None,
                standby=True,
                expected_tracks=('H264',),
                notes=(
                    'Off the van LAN → cloud hub. RTMP creds ride the QUERY STRING '
                    '(?user=…&pass=…), NOT a user:pass@host userinfo prefix — userinfo '
                    'is rejected authentication failed.',
                    'Pinned VIDEO-ONLY [H264]: mute the DJI live audio or the publish is '
                    'rejected (wants to publish [MPEG-4 Audio]). ffmpeg exit code lies on '
                    'a reject — the hub journal is authoritative.',
                ),
            )
        )
    return feeds


def _radio() -> list[Feed]:
    """The van radio audio path (published in-hub by radio-stream; nothing to send)."""
    return [
        Feed(
            path='radio',
            label='Radio audio',
            hub='van',
            slot_group='radio',
            transport='internal',
            role='internal',
            send=None,
            obs_read=f'rtsp://{_VAN}:8554/radio',
            browser_url=f'http://{_VAN}:8889/radio',
            standby=False,
            expected_tracks=('Opus',),
            notes=('Icom radio audio mixed into the hub by gps-dashboard — nothing to publish.',),
        )
    ]


#: Every feed on both hubs, in display order (grouped by slot_group downstream).
FEEDS: tuple[Feed, ...] = tuple(
    _van_cameras() + _radio() + _cloud_phones() + _drones() + _van_dahua_broll()
)


def env_keys(feed: Feed) -> set[str]:
    """The ``${ENV_KEY}`` names a feed's templates reference (derived, not stored).

    Args:
        feed: The feed to scan.

    Returns:
        The set of env-var names appearing as ``${NAME}`` across the feed's
        ``obs_read`` and every :class:`SendSpec` template. Empty for a no-secret
        (van) feed.
    """
    keys: set[str] = set()
    templates = [feed.obs_read]
    if feed.send is not None:
        templates += [feed.send.streamid, feed.send.passphrase, feed.send.rtmp_query]
    for tmpl in templates:
        if tmpl:
            keys.update(_PLACEHOLDER.findall(tmpl))
    return keys


def _interp(tmpl: str | None, env: Mapping[str, str], missing: set[str]) -> str | None:
    """Interpolate ``${ENV_KEY}`` placeholders, recording any absent keys.

    A placeholder whose key is absent from ``env`` is left literal (so the UI shows
    the unresolved ``${…}`` as a visible "secret not set" signal) and its name is
    added to ``missing``.
    """
    if tmpl is None:
        return None

    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key in env:
            return env[key]
        missing.add(key)
        return m.group(0)

    return _PLACEHOLDER.sub(sub, tmpl)


def _single_url(
    feed: Feed, send: SendSpec, env: Mapping[str, str], missing: set[str]
) -> str | None:
    """Derive the one-field send URL for a feed (SRT/RTMP), interpolated.

    Kept derived so the fielded and single-URL views can never drift.
    """
    if feed.transport == 'srt':
        params = [f'streamid={_interp(send.streamid, env, missing)}']
        if send.latency_ms is not None:
            params.append(f'latency={send.latency_ms}')
        if send.passphrase is not None:
            params.append(f'passphrase={_interp(send.passphrase, env, missing)}')
        return f'srt://{send.host}:{send.port}?' + '&'.join(params)
    if feed.transport == 'rtmp':
        query = _interp(send.rtmp_query, env, missing) or ''
        return f'rtmp://{send.host}:{send.port}/{feed.path}{query}'
    return None


def render_feed(feed: Feed, env: Mapping[str, str]) -> dict[str, Any]:
    """Render one feed to its copy-ready config payload (pure; env dict in).

    Args:
        feed: The registry entry.
        env: Environment mapping (``os.environ`` in the route, a fake in tests).

    Returns:
        A JSON-serialisable dict: identity + grouping, the interpolated ``obs_read``
        and per-field/single-URL ``send`` block, the codec pins, notes, and a
        ``missing_secrets`` list naming any ``${ENV_KEY}`` this feed needs but ``env``
        lacks (empty = fully resolved / a no-secret van feed).
    """
    missing: set[str] = set()
    out: dict[str, Any] = {
        'path': feed.path,
        'label': feed.label,
        'hub': feed.hub,
        'slot_group': feed.slot_group,
        'transport': feed.transport,
        'role': feed.role,
        'standby': feed.standby,
        'expected_tracks': list(feed.expected_tracks),
        'obs_read': _interp(feed.obs_read, env, missing),
        'browser_url': feed.browser_url,
        'notes': list(feed.notes),
        'send': None,
    }
    if feed.send is not None:
        out['send'] = {
            'host': feed.send.host,
            'port': feed.send.port,
            'streamid': _interp(feed.send.streamid, env, missing),
            'passphrase': _interp(feed.send.passphrase, env, missing),
            'latency_ms': feed.send.latency_ms,
            'encryption': feed.send.encryption,
            'single_url': _single_url(feed, feed.send, env, missing),
        }
    out['missing_secrets'] = sorted(missing)
    return out


def render_feeds(env: Mapping[str, str]) -> dict[str, Any]:
    """Render the whole registry to the config-reference payload (pure).

    Args:
        env: Environment mapping (``os.environ`` in the route).

    Returns:
        ``{'feeds': [...], 'missing_secrets': [...]}`` — every feed rendered by
        :func:`render_feed`, plus the union of env keys referenced anywhere but
        absent (so the view can flag an unconfigured ``/etc/default/gps-broadcast``
        once, up top).
    """
    feeds = [render_feed(f, env) for f in FEEDS]
    missing: set[str] = set()
    for f in feeds:
        missing.update(f['missing_secrets'])
    return {'feeds': feeds, 'missing_secrets': sorted(missing)}
