"""The runner's download layer + staging-dir management.

Downloads stream into the staging directory (``updater.paths.staging_dir``)
as ``<name>.part`` and atomically rename on success, so a killed or failed
download can never be mistaken for a staged file. Progress prints as plain
lines (the runner's output is a log file, not a TTY — no carriage-return
tricks).

Staged transfer files (the OSM/wiki transfer DBs built off-Pi, a phone
Takeout export) are detected by their conventional filenames in the same
directory — the producing tools' default output names, documented per chunk
in :data:`STAGED_NAMES`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

from updater.paths import staging_dir

#: Upstream sources for the Phase 2 direct-download chunks.
RIDB_URL = 'https://ridb.recreation.gov/downloads/RIDBFullExport_V1_CSV.zip'
GNIS_URL = (
    'https://prd-tnm.s3.amazonaws.com/StagedProducts/GeographicNames/'
    'DomesticNames/DomesticNames_National_Text.zip'
)

#: Conventional staged filename per staged-import chunk: the producing tool's
#: default output name (``tools/build_osm_pois.py`` / ``tools/fetch_wikipedia.py``)
#: or the Takeout export's own name. Detection is by exact name in the staging dir.
STAGED_NAMES: dict[str, str] = {
    'osm': 'osm-places.db',
    'wiki': 'wiki-cache.db',
    'phone': 'Timeline.json',
}

_CHUNK_BYTES = 1 << 20
_PROGRESS_EVERY_BYTES = 25 * (1 << 20)


def staged_file(chunk_id: str) -> Path | None:
    """The staged transfer file waiting for ``chunk_id``, or None.

    Args:
        chunk_id: A key of :data:`STAGED_NAMES`; other ids never have staged
            files and always return None.

    Returns:
        The file's path when present in the staging dir, else None.
    """
    name = STAGED_NAMES.get(chunk_id)
    if name is None:
        return None
    path = staging_dir() / name
    return path if path.is_file() else None


def staged_detail(chunk_id: str) -> dict[str, Any] | None:
    """Status-payload facts about a chunk's staged file (path, size, mtime).

    Args:
        chunk_id: The chunk to look up.

    Returns:
        ``{'path', 'size_bytes', 'mtime_unix'}`` when a staged file exists,
        else None (also for chunks that never stage).
    """
    path = staged_file(chunk_id)
    if path is None:
        return None
    st = path.stat()
    return {'path': str(path), 'size_bytes': st.st_size, 'mtime_unix': st.st_mtime}


def _progress_line(done: int, total: int | None) -> str:
    """One log line of download progress, with or without a known total."""
    mb = done / 1e6
    if not total:
        return f'  {mb:,.1f} MB'
    return f'  {mb:,.1f} / {total / 1e6:,.1f} MB ({done * 100 // total}%)'


def download(url: str, dest: Path, timeout: float = 60.0) -> Path:
    """Stream ``url`` to ``dest`` via a ``.part`` file + atomic rename.

    Prints a progress line every ~25 MB so the run log shows a stalled
    download as a stopped counter rather than silence.

    Args:
        url: The source URL.
        dest: Final path (its parent is created; an existing file is replaced
            only after the new download completes).
        timeout: Per-read socket timeout in seconds.

    Returns:
        ``dest``.

    Raises:
        requests.HTTPError: On a non-2xx response.
        OSError: On filesystem errors.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + '.part')
    print(f'Downloading {url}', flush=True)
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        header = resp.headers.get('Content-Length')
        total = int(header) if header and header.isdigit() else None
        done = 0
        next_report = _PROGRESS_EVERY_BYTES
        with part.open('wb') as out:
            for block in resp.iter_content(chunk_size=_CHUNK_BYTES):
                out.write(block)
                done += len(block)
                if done >= next_report:
                    print(_progress_line(done, total), flush=True)
                    next_report += _PROGRESS_EVERY_BYTES
    part.replace(dest)
    print(f'Saved {dest} ({done / 1e6:,.1f} MB)', flush=True)
    return dest


def download_ridb() -> Path:
    """Fetch the RIDB full CSV export (~245 MB) into staging."""
    return download(RIDB_URL, staging_dir() / 'RIDBFullExport_V1_CSV.zip')


def download_gnis() -> Path:
    """Fetch the USGS GNIS Domestic Names national zip (~37 MB) into staging."""
    return download(GNIS_URL, staging_dir() / 'DomesticNames_National_Text.zip')
