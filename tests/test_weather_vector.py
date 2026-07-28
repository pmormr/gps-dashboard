"""Tests for the vector-layer fetch + keep-latest store (warnings, P4).

A MockTransport stands in for api.weather.gov so the fetch is hermetic; the
store is asserted to overwrite atomically and count features.
"""

import json

import httpx
import pytest

from weather import registry, vector
from weather.registry import WARNINGS


@pytest.fixture
def archive_root(tmp_path, monkeypatch):
    monkeypatch.setenv('GPS_WEATHER_ARCHIVE_DIR', str(tmp_path))
    return tmp_path


def _client(fc: dict) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda _req: httpx.Response(200, json=fc)))


def test_fetch_and_store_writes_snapshot(archive_root):
    fc = {
        'type': 'FeatureCollection',
        'features': [
            {'type': 'Feature', 'geometry': None, 'properties': {'event': 'Flood Warning'}},
            {'type': 'Feature', 'geometry': None, 'properties': {'event': 'Heat Advisory'}},
        ],
    }
    n = vector.fetch_and_store(WARNINGS, client=_client(fc))
    assert n == 2
    path = registry.vector_path(WARNINGS.id)
    assert path.exists()
    assert json.loads(path.read_text()) == fc


def test_fetch_and_store_overwrites(archive_root):
    vector.fetch_and_store(
        WARNINGS, client=_client({'type': 'FeatureCollection', 'features': [1, 2, 3]})
    )
    n = vector.fetch_and_store(
        WARNINGS, client=_client({'type': 'FeatureCollection', 'features': []})
    )
    assert n == 0
    assert json.loads(registry.vector_path(WARNINGS.id).read_text())['features'] == []


def test_fetch_and_store_leaves_no_tmp(archive_root):
    vector.fetch_and_store(WARNINGS, client=_client({'type': 'FeatureCollection', 'features': []}))
    assert list(registry.layer_dir(WARNINGS.id).glob('*.tmp')) == []


def test_fetch_and_store_raises_on_http_error(archive_root):
    client = httpx.Client(transport=httpx.MockTransport(lambda _req: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        vector.fetch_and_store(WARNINGS, client=client)
