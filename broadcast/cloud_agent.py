"""Cloud-hub broadcast agent — a WG-bound snapshot + log service (B9/B11).

The cloud MediaMTX hub (``vps202051``) mirrors the van hub but sits on the public
internet, reached for status only over the WG control tunnel (B7). Its *status*
comes from MediaMTX's own control API; this tiny stdlib HTTP service supplies the
two things MediaMTX doesn't:

- ``GET /snapshot/<path>`` — a rolling downscaled JPEG for the monitor wall (B9),
  produced by the shared :class:`~broadcast.snapshots.SnapshotManager`. The cloud
  hub 401s an anonymous RTSP read, so the manager pulls with the ``obs`` credential
  baked into ``rtsp_base`` (``BROADCAST_AGENT_RTSP_BASE``) — never the open van base.
- ``GET /logs?lines=N`` — the raw ``journalctl -u mediamtx`` tail (B11).
- ``GET /active`` — the paths with a live snapshot worker, so the van's status
  route can discount the agent's own RTSP pulls from each feed's reader count
  (the wall previewing itself must not read as a consumer — the per-hub discount).
- ``GET /health`` — a liveness ping.

Bound to the WG interface (default ``10.9.9.1:9998``) so there is no public status
surface — the tunnel is the trust boundary (B7). The van's Flask app is the only
client; it *proxies* these to LAN browsers (which can't reach the WG address), so
the JSON shapes here are internal to that proxy, not the browser-facing API.

**Off-repo deploy.** The vps is not the Pi deploy target, so this file plus the two
stdlib-only modules it imports (:mod:`broadcast.snapshots`, :mod:`broadcast.feeds`)
are rsync'd to the vps and run as ``python3 -m broadcast.cloud_agent`` under a
systemd unit (``broadcast/cloud_agent.service``). See the CLAUDE.md manual-install
carve-out. Stdlib-only by design: no Flask, no requests, nothing from ``uv sync``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlparse

from broadcast.feeds import FEEDS
from broadcast.snapshots import DEFAULT_RTSP_BASE, SnapshotManager

#: Cloud paths that carry video and so can be snapshotted (radio is van-only/audio).
SNAPSHOTTABLE = frozenset(f.path for f in FEEDS if f.hub == 'cloud' and f.slot_group != 'radio')

DEFAULT_HOST = '10.9.9.1'
DEFAULT_PORT = 9998
DEFAULT_UNIT = 'mediamtx'
_LOG_LINES_DEFAULT = 200
_LOG_LINES_MAX = 1000
_JOURNAL_TIMEOUT_S = 6.0


def _journal_lines(unit: str, n: int) -> list[str]:
    """Recent journal lines for ``unit`` (``-o cat``), or [] if unreadable.

    Mirrors the van's ``journalctl -u mediamtx`` read; the agent's service user
    must be in ``systemd-journal`` to read another unit's journal without sudo.
    """
    try:
        result = subprocess.run(
            ['journalctl', '-u', unit, '-n', str(n), '--no-pager', '-o', 'cat'],
            capture_output=True,
            text=True,
            timeout=_JOURNAL_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


class _AgentHandler(BaseHTTPRequestHandler):
    """Routes the four GET endpoints against the server's shared manager + unit."""

    server_version = 'BroadcastCloudAgent/1'

    def log_message(self, *_args: Any) -> None:
        """Silence per-request logging (systemd captures our explicit prints)."""

    def _json(self, obj: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler dispatch name
        srv = cast('_AgentServer', self.server)
        parsed = urlparse(self.path)
        parts = [unquote(p) for p in parsed.path.strip('/').split('/') if p]

        if parts == ['health']:
            self._json({'ok': True, 'service': 'broadcast-cloud-agent'})
            return
        if parts == ['active']:
            self._json({'paths': sorted(srv.manager.active_paths())})
            return
        if parts == ['logs']:
            n = _clamp_lines(parse_qs(parsed.query).get('lines', [None])[0])
            self._json({'lines': _journal_lines(srv.unit, n)})
            return
        if len(parts) == 2 and parts[0] == 'snapshot':
            self._serve_snapshot(srv, parts[1])
            return
        self._json({'error': 'not found'}, status=404)

    def _serve_snapshot(self, srv: _AgentServer, name: str) -> None:
        """Serve a feed's rolling JPEG: 200 bytes / 202 warming / 404 unknown."""
        if name not in SNAPSHOTTABLE:
            self._json({'error': 'unknown path'}, status=404)
            return
        jpeg = srv.manager.request(name)
        if jpeg is None:
            self.send_response(202)  # warming up — the tile keeps polling
            self.end_headers()
            return
        try:
            data = jpeg.read_bytes()
        except OSError:
            self.send_response(202)  # reaped between check and read — treat as warming
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/jpeg')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)


class _AgentServer(ThreadingHTTPServer):
    """The threading HTTP server carrying the shared snapshot manager + unit name."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], manager: SnapshotManager, unit: str) -> None:
        super().__init__(address, _AgentHandler)
        self.manager = manager
        self.unit = unit


def _clamp_lines(raw: str | None) -> int:
    """Parse + clamp the ``lines`` query to ``[1, _LOG_LINES_MAX]``."""
    try:
        n = int(raw) if raw is not None else _LOG_LINES_DEFAULT
    except (TypeError, ValueError):
        n = _LOG_LINES_DEFAULT
    return max(1, min(n, _LOG_LINES_MAX))


def serve(host: str, port: int, rtsp_base: str, unit: str) -> int:
    """Run the agent until SIGTERM/SIGINT, reaping ffmpeg workers on exit."""
    manager = SnapshotManager(rtsp_base=rtsp_base)
    server = _AgentServer((host, port), manager, unit)

    def _terminate(_signum: int, _frame: object) -> None:
        # shutdown() must run off the serve_forever thread or it deadlocks.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _terminate)
    # Redact the obs credential from the base before logging.
    print(
        f'broadcast cloud agent on http://{host}:{port} '
        f'(rtsp {rtsp_base.rsplit("@", 1)[-1]}, unit {unit})',
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nInterrupted.')
        return 130
    finally:
        manager.shutdown()
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry: resolve host/port/rtsp-base/unit from flags or env, then serve."""
    env = os.environ
    parser = argparse.ArgumentParser(description='Broadcast cloud-hub snapshot + log agent.')
    parser.add_argument('--host', default=env.get('BROADCAST_AGENT_HOST', DEFAULT_HOST))
    parser.add_argument(
        '--port', type=int, default=int(env.get('BROADCAST_AGENT_PORT', DEFAULT_PORT))
    )
    parser.add_argument(
        '--rtsp-base', default=env.get('BROADCAST_AGENT_RTSP_BASE', DEFAULT_RTSP_BASE)
    )
    parser.add_argument('--unit', default=env.get('BROADCAST_AGENT_UNIT', DEFAULT_UNIT))
    args = parser.parse_args(argv)
    return serve(args.host, args.port, args.rtsp_base, args.unit)


if __name__ == '__main__':
    raise SystemExit(main())
