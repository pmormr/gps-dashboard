"""Shared MediaMTX control-API client (both hubs).

One place to fetch + normalize ``/v3/paths/list`` from a hub's control API, used
by ``/api/mediamtx`` (Diagnostics, van hub) and ``/api/broadcast/status`` (the
van section now; the cloud section over the WG control tunnel — B7 — reuses the
same client pointed at a different base URL).

The normalize preserves the fields the broadcast two-sides status model needs
that a plain ready/tracks reduction drops — ``source.id``, ``available``,
``online``. Verified against the live van hub (MediaMTX v1.19.2): an on-demand or
``alwaysAvailable`` path reports its *configured* ``source.type`` even when
nothing is attached (e.g. an idle proxy shows ``rtspSource`` with an **empty**
``source.id``), so a non-empty ``source.id`` — not ``source.type`` — is the "a
real source/publisher is connected" signal (:attr:`PathState.source_connected`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

#: The van hub's localhost control API base (``api:`` / ``apiAddress:`` in
#: ``deploy/mediamtx.yml``). The cloud base (P3) is a WG-interface address.
DEFAULT_API_BASE = 'http://127.0.0.1:9997'

_PATHS_ENDPOINT = '/v3/paths/list'


@dataclass(frozen=True)
class PathState:
    """One control-API path, normalized to the fields both status surfaces use.

    Attributes:
        name: The path name (matches ``deploy/mediamtx.yml`` / the registry).
        ready: Whether the path is serving (a publisher connected, an on-demand
            source pulled, or a STANDBY loop for an ``alwaysAvailable`` path).
        available: The hub's own readiness flag (``alwaysAvailable`` nuance).
        online: Whether the path is configured/known to the hub.
        source_type: The source's ``type`` (``srtConn``/``rtmpConn``/
            ``rtspSession``/``webRTCSession`` for a live connection; the
            *configured* ``rtspSource`` for an idle on-demand proxy), or None
            when no source object exists.
        source_id: The source's connection id — non-empty **only** when a source
            is actually attached (empty string normalized to None). The live
            discriminator; see :attr:`source_connected`.
        tracks: Live codec names (``('H264',)``, ``('H265', 'MPEG4Audio')``).
        readers: Count of attached readers/consumers (OBS, browser, …).
        bytes_received: Cumulative inbound bytes (ingest throughput, via deltas).
        bytes_sent: Cumulative outbound bytes (egress throughput, via deltas).
    """

    name: str
    ready: bool
    available: bool
    online: bool
    source_type: str | None
    source_id: str | None
    tracks: tuple[str, ...]
    readers: int
    bytes_received: int
    bytes_sent: int

    @property
    def source_connected(self) -> bool:
        """Whether a real source/publisher is attached (non-empty ``source.id``).

        On-demand and ``alwaysAvailable`` paths carry a configured ``source_type``
        with no id when idle, so the id — not the type — is the live signal.
        """
        return bool(self.source_id)


def normalize_path(item: dict[str, Any]) -> PathState:
    """Reduce one raw ``/v3/paths/list`` item to a :class:`PathState`.

    Tolerant of missing fields (a bare ``{'name': ...}`` normalizes cleanly) so a
    control-API schema drift degrades softly rather than raising.
    """
    src = item.get('source') or {}
    return PathState(
        name=item.get('name') or '',
        ready=bool(item.get('ready')),
        available=bool(item.get('available')),
        online=bool(item.get('online')),
        source_type=src.get('type'),
        source_id=src.get('id') or None,  # '' (idle on-demand) → None
        tracks=tuple(item.get('tracks') or []),
        readers=len(item.get('readers') or []),
        bytes_received=int(item.get('bytesReceived') or 0),
        bytes_sent=int(item.get('bytesSent') or 0),
    )


def fetch_paths(base: str = DEFAULT_API_BASE, timeout: float = 4.0) -> list[PathState] | None:
    """Fetch + normalize a hub's path list, or None when its API is unreachable.

    Args:
        base: The control-API base URL (van localhost by default; a WG-interface
            base for the cloud hub — P3).
        timeout: Per-request timeout (seconds); a down/off-grid hub must fail fast
            rather than hang the caller.

    Returns:
        One :class:`PathState` per path, or None when the API can't be reached or
        returns unparseable data (hub down, API disabled, tunnel down).
    """
    try:
        resp = requests.get(f'{base}{_PATHS_ENDPOINT}', timeout=timeout)
        resp.raise_for_status()
        items = resp.json().get('items', [])
    except (requests.RequestException, ValueError):
        return None
    return [normalize_path(it) for it in items]
