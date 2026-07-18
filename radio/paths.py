"""Radio-audio storage root — shared by the recorder daemon and the API reads.

The recorder writes WAVs (and the relative ``audio_path`` it stores on each
``radio_transmissions`` row) under this root; the dashboard's audio route joins
those relative paths back against it. Both sides must resolve identically, so
the resolution lives here once. If ``GPS_RADIO_AUDIO_DIR`` is ever overridden,
it must be set in *both* units (``radio-recorder`` and ``gps-dashboard``).
"""

from __future__ import annotations

import os
from pathlib import Path

from api import db


def audio_dir() -> Path:
    """Resolve the audio root: ``GPS_RADIO_AUDIO_DIR``, else ``radio-audio`` beside the DB.

    Reads ``api.db.DB_PATH`` at call time (not import) so the beside-the-DB
    default follows wherever the DB points — including test isolation.

    Returns:
        The directory transmission WAVs live under.
    """
    return Path(os.environ.get('GPS_RADIO_AUDIO_DIR', str(db.DB_PATH.parent / 'radio-audio')))
