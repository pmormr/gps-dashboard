"""Integration tests for the cloud-hub agent (broadcast/cloud_agent.py).

Spins the real ``ThreadingHTTPServer`` on an ephemeral localhost port with an
injected fake ffmpeg spawn, so the four GET routes + the snapshottable gate are
exercised end-to-end without a real cloud hub or ffmpeg.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from broadcast import cloud_agent
from broadcast.snapshots import Handle, SnapshotManager


class _FakeHandle(Handle):
    """A never-dying process handle for the injected spawn."""

    def poll(self) -> None:
        return None

    def terminate(self) -> None: ...


@pytest.fixture
def agent(tmp_path: Path) -> Iterator[cloud_agent._AgentServer]:
    def spawn(_path: str, out: Path) -> _FakeHandle:
        out.write_bytes(b'\xff\xd8jpeg')  # simulate ffmpeg's first frame
        return _FakeHandle()

    mgr = SnapshotManager(out_dir=tmp_path, spawn=spawn)
    mgr._reaper_started = True  # don't start the background reaper in tests
    srv = cloud_agent._AgentServer(('127.0.0.1', 0), mgr, 'mediamtx')
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        mgr.shutdown()


def _get(srv: cloud_agent._AgentServer, path: str) -> tuple[int, bytes, str | None]:
    addr = srv.socket.getsockname()  # (host, port) for the bound AF_INET socket
    try:
        with urllib.request.urlopen(f'http://{addr[0]}:{addr[1]}{path}', timeout=3) as r:
            return r.status, r.read(), r.headers.get_content_type()
    except urllib.error.HTTPError as e:
        return e.code, e.read(), None


def test_health(agent: cloud_agent._AgentServer) -> None:
    status, body, _ = _get(agent, '/health')
    assert status == 200 and json.loads(body)['ok'] is True


def test_snapshot_serves_jpeg_for_gated_path(agent: cloud_agent._AgentServer) -> None:
    status, body, ctype = _get(agent, '/snapshot/phone1')
    assert status == 200 and ctype == 'image/jpeg' and body == b'\xff\xd8jpeg'


def test_snapshot_rejects_non_cloud_paths(agent: cloud_agent._AgentServer) -> None:
    assert _get(agent, '/snapshot/cam1')[0] == 404  # a van path
    assert _get(agent, '/snapshot/radio')[0] == 404  # audio-only
    assert _get(agent, '/snapshot/nope')[0] == 404  # unknown


def test_active_reflects_requested_paths(agent: cloud_agent._AgentServer) -> None:
    _get(agent, '/snapshot/phone1')  # spawns a worker
    status, body, _ = _get(agent, '/active')
    assert status == 200 and 'phone1' in json.loads(body)['paths']


def test_logs_returns_journal_lines(
    agent: cloud_agent._AgentServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cloud_agent, '_journal_lines', lambda _unit, _n: ['x', 'y'])
    status, body, _ = _get(agent, '/logs?lines=5')
    assert status == 200 and json.loads(body)['lines'] == ['x', 'y']


def test_unknown_route_404(agent: cloud_agent._AgentServer) -> None:
    assert _get(agent, '/nope')[0] == 404
