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


def soundboard_dir() -> Path:
    """Resolve the transmit soundboard root: ``GPS_RADIO_SOUNDBOARD_DIR``, else beside the DB.

    Staged clips the operator drops here (scp) are transmitted by the console.
    Deliberately outside :func:`audio_dir` so the recorder's retention pruner
    never eats them, and resolved at call time like ``audio_dir`` so tests and
    ``GPS_DB_PATH`` overrides follow the DB.

    Returns:
        The directory soundboard clips live under.
    """
    return Path(
        os.environ.get(
            'GPS_RADIO_SOUNDBOARD_DIR', str(db.DB_PATH.parent / 'radio-tx' / 'soundboard')
        )
    )
