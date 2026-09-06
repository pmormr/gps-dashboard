"""Filesystem locations the update runner works in (staging + run logs).

Both directories derive beside the main DB (the same derived-default pattern
as ``api.db.places_db_path``: every process resolves the same directories by
construction), so on the Pi they land on the NVMe next to the data —
``/mnt/nvme/data/staging`` and ``/mnt/nvme/data/update-logs``. Env overrides
(``GPS_STAGING_DIR`` / ``GPS_UPDATE_LOG_DIR``) exist for tests and unusual
layouts. Resolution happens at call time so tests that monkeypatch
``api.db.DB_PATH`` are followed.
"""

from __future__ import annotations

import os
from pathlib import Path

import api.db


def staging_dir() -> Path:
    """Where downloads land and staged transfer files are looked for."""
    override = os.environ.get('GPS_STAGING_DIR')
    return Path(override) if override else api.db.DB_PATH.parent / 'staging'


def log_dir() -> Path:
    """Where the runner writes per-run log files."""
    override = os.environ.get('GPS_UPDATE_LOG_DIR')
    return Path(override) if override else api.db.DB_PATH.parent / 'update-logs'
