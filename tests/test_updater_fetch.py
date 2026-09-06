"""Tests for the runner's download layer (``updater.fetch``): atomic staging
downloads against a faked ``requests`` session, and staged-file detection.
"""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
import requests

from updater import fetch


@pytest.fixture
def staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A hermetic staging dir via the env override."""
    path = tmp_path / 'staging'
    monkeypatch.setenv('GPS_STAGING_DIR', str(path))
    return path


class _FakeResponse:
    """The slice of a streaming ``requests`` response the downloader touches."""

    def __init__(self, blocks: list[bytes], status: int = 200, length: str | None = None) -> None:
        self._blocks = blocks
        self.status_code = status
        self.headers = {'Content-Length': length} if length is not None else {}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f'{self.status_code}')

    def iter_content(self, chunk_size: int) -> Any:
        yield from self._blocks


def test_download_atomic_rename(
    staging: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    resp = _FakeResponse([b'abc', b'def'], length='6')
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: resp)
    dest = staging / 'export.zip'
    assert fetch.download('https://example.test/x.zip', dest) == dest
    assert dest.read_bytes() == b'abcdef'
    assert not dest.with_name('export.zip.part').exists()
    out = capsys.readouterr().out
    assert 'Saved' in out


def test_download_failure_leaves_no_final_file(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = _FakeResponse([], status=503)
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: resp)
    dest = staging / 'export.zip'
    with pytest.raises(requests.HTTPError):
        fetch.download('https://example.test/x.zip', dest)
    assert not dest.exists()


def test_download_mid_stream_error_keeps_existing_file(
    staging: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = staging / 'export.zip'
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b'old good copy')

    class _Broken(_FakeResponse):
        def iter_content(self, chunk_size: int) -> Any:
            yield b'partial'
            raise requests.ConnectionError('reset')

    monkeypatch.setattr(requests, 'get', lambda *a, **kw: _Broken([]))
    with pytest.raises(requests.ConnectionError):
        fetch.download('https://example.test/x.zip', dest)
    assert dest.read_bytes() == b'old good copy'


def test_staged_detection(staging: Path) -> None:
    assert fetch.staged_file('osm') is None
    assert fetch.staged_detail('osm') is None
    assert fetch.staged_file('satcat') is None, 'non-staging chunks never stage'

    staging.mkdir(parents=True)
    (staging / 'wiki-cache.db').write_bytes(b'x' * 10)
    path = fetch.staged_file('wiki')
    assert path is not None and path.name == 'wiki-cache.db'
    detail = fetch.staged_detail('wiki')
    assert detail is not None
    assert detail['size_bytes'] == 10
    assert set(detail) == {'path', 'size_bytes', 'mtime_unix'}
