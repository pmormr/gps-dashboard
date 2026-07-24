"""Tests for the monitor-wall snapshot manager (broadcast/snapshots.py).

The ffmpeg lifecycle is driven with an injected fake spawn + clock, so the
spawn-once / reuse / backoff-respawn / idle-reap state machine is exercised
without real ffmpeg. Real ffmpeg output is verified on the Pi.
"""

from __future__ import annotations

from pathlib import Path

from broadcast.snapshots import SnapshotManager, ffmpeg_cmd


class FakeHandle:
    """A stand-in process handle: alive until terminate() (or forced dead)."""

    def __init__(self) -> None:
        self.alive = True
        self.terminated = False

    def poll(self) -> int | None:
        return None if self.alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False


def _mgr(tmp_path: Path, spawn) -> SnapshotManager:
    m = SnapshotManager(out_dir=tmp_path, spawn=spawn, ttl_s=15.0, backoff_s=3.0)
    m._reaper_started = True  # don't start the background thread in tests
    return m


def test_ensure_worker_spawns_once_and_reuses(tmp_path: Path) -> None:
    handles: list[FakeHandle] = []

    def spawn(_p: str, _o: Path) -> FakeHandle:
        h = FakeHandle()
        handles.append(h)
        return h

    m = _mgr(tmp_path, spawn)
    m._ensure_worker('cam1', 0.0)
    m._ensure_worker('cam1', 1.0)
    assert len(handles) == 1
    assert m._workers['cam1'].last_access == 1.0


def test_dead_worker_respawns_only_after_backoff(tmp_path: Path) -> None:
    handles: list[FakeHandle] = []

    def spawn(_p: str, _o: Path) -> FakeHandle:
        h = FakeHandle()
        handles.append(h)
        return h

    m = _mgr(tmp_path, spawn)
    m._ensure_worker('cam1', 0.0)
    handles[0].alive = False  # ffmpeg died
    m._ensure_worker('cam1', 1.0)  # within 3 s backoff → no respawn
    assert len(handles) == 1
    m._ensure_worker('cam1', 4.0)  # backoff elapsed → respawn
    assert len(handles) == 2


def test_sweep_reaps_idle_workers(tmp_path: Path) -> None:
    handles: list[FakeHandle] = []

    def spawn(_p: str, _o: Path) -> FakeHandle:
        h = FakeHandle()
        handles.append(h)
        return h

    m = _mgr(tmp_path, spawn)
    m._ensure_worker('cam1', 0.0)
    m.sweep(now=10.0)  # within TTL
    assert 'cam1' in m._workers
    m.sweep(now=20.0)  # idle > 15 s TTL
    assert 'cam1' not in m._workers
    assert handles[0].terminated is True


def test_request_returns_path_when_frame_written(tmp_path: Path) -> None:
    def spawn(_p: str, out: Path) -> FakeHandle:
        out.write_bytes(b'\xff\xd8jpeg')  # simulate ffmpeg's first frame
        return FakeHandle()

    m = _mgr(tmp_path, spawn)
    assert m.request('cam1') == m.jpeg_path('cam1')


def test_request_none_while_warming_up(tmp_path: Path) -> None:
    m = _mgr(tmp_path, lambda _p, _o: FakeHandle())  # no file written yet
    assert m.request('cam1') is None


def test_shutdown_terminates_all(tmp_path: Path) -> None:
    handles: list[FakeHandle] = []

    def spawn(_p: str, _o: Path) -> FakeHandle:
        h = FakeHandle()
        handles.append(h)
        return h

    m = _mgr(tmp_path, spawn)
    m._ensure_worker('cam1', 0.0)
    m._ensure_worker('drone1', 0.0)
    m.shutdown()
    assert all(h.terminated for h in handles)
    assert not m._workers


def test_ffmpeg_cmd_is_localhost_scoped_and_downscaled() -> None:
    cmd = ffmpeg_cmd('cam-front-main', Path('/dev/shm/x.jpg'))
    assert 'rtsp://127.0.0.1:8554/cam-front-main' in cmd
    assert '-update' in cmd and 'scale=320:-2' in cmd
