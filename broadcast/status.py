"""Two-sides live status derivation for a broadcast feed (pure; B6).

Every feed is two independent halves the wall must show separately, because a
healthy-looking one can mask a dead one (the ``alwaysAvailable`` decoupling means
a dead camera keeps egress serving a STANDBY loop — last year's silent failure):

- **Ingest** — is a real publisher/source connected? Derived from the control
  API's ``source.id`` (:attr:`~common.mediamtx.PathState.source_connected`), not
  ``source.type`` (which reports the *configured* source even when idle). Three
  states: ``live`` (real source attached), ``standby`` (an ``alwaysAvailable``
  path that is ``ready`` but serving the loop with no real publisher), ``idle``
  (not serving — a non-STANDBY path waiting for its publisher, or an unpulled
  on-demand proxy).
- **Egress** — is anyone pulling (``readers``), and is data flowing
  (``bytes_sent``, via deltas the client computes across polls)?

The dangerous state — the whole point — is ``ingest == 'standby'`` **and**
``readers > 0``: OBS is pulling a placeholder while the real source is dead.

Hub-agnostic: works whether or not a path is ``alwaysAvailable``. Without STANDBY,
a dead ingest simply drops ``ready`` (and egress with it) — honest, never the
masked ``standby`` state. The ``standby`` state only arises on STANDBY feeds.
"""

from __future__ import annotations

from typing import Any

from broadcast.feeds import Feed
from common.mediamtx import PathState

#: Ingest half states (see module docstring).
INGEST_STATES = ('live', 'standby', 'idle')


def ingest_state(feed: Feed, state: PathState) -> str:
    """Classify the ingest half from the live path state (one of :data:`INGEST_STATES`)."""
    if state.source_connected:
        return 'live'
    if feed.standby and state.ready:
        return 'standby'
    return 'idle'


def codec_badge(expected: tuple[str, ...], actual: tuple[str, ...]) -> str:
    """Compare live vs expected codec-name sets → ``match`` | ``mismatch`` | ``unknown``.

    ``unknown`` when there are no live tracks yet (idle path — nothing to compare).
    """
    if not actual:
        return 'unknown'
    return 'match' if set(actual) == set(expected) else 'mismatch'


def feed_status(feed: Feed, state: PathState | None, self_readers: int = 0) -> dict[str, Any]:
    """The two-sides live status for one feed on a *reachable* hub.

    Args:
        feed: The registry entry (its ``standby`` flag + ``expected_tracks`` drive
            interpretation).
        state: The matching control-API path, or None when the hub is reachable
            but the path is absent (not configured on the hub).
        self_readers: The hub's own readers to discount — the monitor-wall
            snapshotter pulls over RTSP and the hub counts it as a reader, so
            subtract it (per path) to keep ``readers``/``pulling``/``danger``
            reflecting *real* consumers (OBS), not the wall previewing itself.

    Returns:
        A JSON dict. ``present: false`` when the path is absent; otherwise the
        ingest state, egress (readers/pulling), codec badge, cumulative bytes (for
        client-side throughput deltas), and the ``danger`` flag.
    """
    if state is None:
        return {'reachable': True, 'present': False}
    ing = ingest_state(feed, state)
    readers = max(0, state.readers - self_readers)
    return {
        'reachable': True,
        'present': True,
        'ready': state.ready,
        'ingest': ing,
        'source_type': state.source_type,
        'tracks': list(state.tracks),
        'codec': codec_badge(feed.expected_tracks, state.tracks),
        'readers': readers,
        'pulling': readers > 0,
        'bytes_received': state.bytes_received,
        'bytes_sent': state.bytes_sent,
        'danger': ing == 'standby' and readers > 0,
    }
