"""On-demand localhost-RTSP → tmpfs JPEG snapshotter for the monitor wall (B9).

Decode where the video already lives, ship only pixels-as-JPEG: one ffmpeg per
*actively-viewed* feed pulls ``rtsp://127.0.0.1:8554/<path>``, downscales, and
writes a rolling latest-frame JPEG (~0.5 fps) to tmpfs. A tiny endpoint serves
that file to the wall's self-scheduled per-tile poll.

Workers are lazily started when a tile first asks and reaped after an idle TTL, so
ffmpeg processes are bounded to feeds on screen — not to poll rate (the Cameras
"self-schedule + cache" lesson). A dead worker is respawned on the next request
(with a short backoff so a never-ready feed can't thrash ffmpeg). Generalizes the
Cameras server-side snapshot-downscale pattern to the hub's RTSP paths, and works
for H.265 B-roll too (ffmpeg decodes it fine — only the browser can't).

The manager is pure-ish and injectable (``spawn``/``clock``) so the lifecycle is
unit-tested without real ffmpeg; the module singleton wires the real ffmpeg spawn.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

#: tmpfs directory for the rolling JPEGs (always present on the Pi; RAM-backed).
DEFAULT_SNAPSHOT_DIR = Path('/dev/shm/gps-broadcast-snapshots')

#: Kill a worker whose JPEG hasn't been requested in this long (a few missed polls).
IDLE_TTL_S = 15.0

#: Don't respawn a dead worker more often than this (a never-ready feed mustn't thrash).
RESPAWN_BACKOFF_S = 3.0

#: Reaper sweep cadence.
REAP_INTERVAL_S = 5.0

#: Downscale width (px); height auto (even). ~320 wide ≈ 20 kB — HaLow-friendly.
THUMB_WIDTH = 320


class Handle:
    """Minimal process handle protocol the manager needs (``subprocess.Popen`` fits)."""

    def poll(self) -> int | None:  # pragma: no cover - protocol only
        ...

    def terminate(self) -> None:  # pragma: no cover - protocol only
        ...


@dataclass
class _Worker:
    """A running ffmpeg for one path plus its access/spawn bookkeeping."""

    handle: Handle
    last_access: float
    last_spawn: float


def ffmpeg_cmd(path: str, out: Path) -> list[str]:
    """The ffmpeg argv for a rolling downscaled JPEG off a localhost RTSP path.

    ``-update 1 -y`` overwrites the single output file each frame; ``-r 0.5`` caps
    it to a frame every 2 s; ``-an`` drops audio; TCP transport avoids UDP loss on
    the local pull.
    """
    return [
        'ffmpeg',
        '-nostdin',
        '-loglevel',
        'error',
        '-rtsp_transport',
        'tcp',
        '-i',
        f'rtsp://127.0.0.1:8554/{path}',
        '-an',
        '-r',
        '0.5',
        '-vf',
        f'scale={THUMB_WIDTH}:-2',
        '-q:v',
        '7',
        '-update',
        '1',
        '-y',
        str(out),
    ]


def _spawn_ffmpeg(path: str, out: Path) -> Handle:
    """Start a detached ffmpeg writing the rolling JPEG (stdio → /dev/null)."""
    return subprocess.Popen(  # type: ignore[return-value]
        ffmpeg_cmd(path, out),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class SnapshotManager:
    """Lifecycle for the per-feed snapshot ffmpeg workers (thread-safe).

    Args:
        out_dir: Directory for the rolling JPEGs (created if absent).
        spawn: ``(path, out_path) -> Handle`` — injected for tests; defaults to
            the real ffmpeg spawn.
        clock: Monotonic clock source — injected for tests.
        ttl_s: Idle TTL before a worker is reaped.
        backoff_s: Minimum interval between respawns of a dead worker.
    """

    def __init__(
        self,
        out_dir: Path = DEFAULT_SNAPSHOT_DIR,
        spawn: Callable[[str, Path], Handle] = _spawn_ffmpeg,
        clock: Callable[[], float] = time.monotonic,
        ttl_s: float = IDLE_TTL_S,
        backoff_s: float = RESPAWN_BACKOFF_S,
    ) -> None:
        self._out_dir = out_dir
        self._spawn = spawn
        self._clock = clock
        self._ttl_s = ttl_s
        self._backoff_s = backoff_s
        self._workers: dict[str, _Worker] = {}
        self._lock = threading.Lock()
        self._reaper_started = False

    def jpeg_path(self, path: str) -> Path:
        """The rolling JPEG path for a feed path."""
        return self._out_dir / f'{path}.jpg'

    def request(self, path: str) -> Path | None:
        """Mark a feed viewed, (re)start its worker as needed, and return its JPEG.

        Returns the JPEG path if a frame has been written, else None (still
        warming up — the route answers 202 so the tile keeps polling).
        """
        self._ensure_reaper()
        now = self._clock()
        with self._lock:
            self._ensure_worker(path, now)
        jpeg = self.jpeg_path(path)
        return jpeg if jpeg.exists() else None

    def _ensure_worker(self, path: str, now: float) -> None:
        """Spawn (or respawn a dead, backoff-elapsed) worker for a path. Lock held."""
        worker = self._workers.get(path)
        if worker is not None:
            worker.last_access = now
            if worker.handle.poll() is None:
                return  # alive
            if now - worker.last_spawn < self._backoff_s:
                return  # dead but backing off — don't thrash
        self._out_dir.mkdir(parents=True, exist_ok=True)
        handle = self._spawn(path, self.jpeg_path(path))
        self._workers[path] = _Worker(handle=handle, last_access=now, last_spawn=now)

    def sweep(self, now: float | None = None) -> None:
        """Terminate workers idle longer than the TTL (idempotent)."""
        if now is None:
            now = self._clock()
        with self._lock:
            for path, worker in list(self._workers.items()):
                if now - worker.last_access > self._ttl_s:
                    self._stop(worker)
                    del self._workers[path]

    def shutdown(self) -> None:
        """Terminate every worker (process teardown)."""
        with self._lock:
            for worker in self._workers.values():
                self._stop(worker)
            self._workers.clear()

    @staticmethod
    def _stop(worker: _Worker) -> None:
        try:
            worker.handle.terminate()
        except Exception:  # noqa: BLE001 - best-effort teardown of a maybe-dead proc
            pass

    def _ensure_reaper(self) -> None:
        """Start the background idle-sweep thread once (daemon)."""
        with self._lock:
            if self._reaper_started:
                return
            self._reaper_started = True
        thread = threading.Thread(target=self._reap_loop, name='bcast-snap-reaper', daemon=True)
        thread.start()

    def _reap_loop(self) -> None:  # pragma: no cover - thread wrapper over sweep()
        while True:
            time.sleep(REAP_INTERVAL_S)
            self.sweep()


_MANAGER: SnapshotManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> SnapshotManager:
    """The process-wide snapshot manager (real ffmpeg spawn), created on first use."""
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = SnapshotManager()
        return _MANAGER
