"""Shared pytest fixtures.

Two fixtures cover the whole suite's infrastructure needs:

* ``app_context`` — a minimal pushed Flask application context, needed by the
  ``api.params`` helpers because their error branches build responses with
  :func:`flask.jsonify`, which requires an active app context.
* ``client`` — a real :func:`api.app.create_app` wired to an isolated,
  file-backed SQLite database (the app's schema is created on startup), returned
  as a ``test_client`` for the points read-path tests.
"""

from __future__ import annotations

import pytest
from flask import Flask


@pytest.fixture
def app_context():
    """Push a minimal Flask app context so :func:`flask.jsonify` works.

    Yields:
        None — the context is active for the duration of the test.
    """
    app = Flask(__name__)
    with app.app_context():
        yield


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build the real app against a throwaway SQLite file and return its client.

    Patches ``api.db.DB_PATH`` before :func:`api.app.create_app` runs, so the
    schema is initialised in the temp database and every request-time
    :func:`api.db.get_connection` opens the same file.

    Args:
        tmp_path: pytest's per-test temp directory.
        monkeypatch: pytest's attribute-patching fixture.

    Returns:
        A Flask ``test_client`` bound to an isolated database.
    """
    import api.db as db

    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'test.db')
    from api.app import create_app

    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()
