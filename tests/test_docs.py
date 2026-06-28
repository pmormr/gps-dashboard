"""Tests for the network-docs reader route (`api/routes/docs.py`).

Covers the file tree (structure + default pick), raw markdown fetch, and the
two rejection paths that matter for a file server: path traversal and non-`.md`
requests. A throwaway vault is wired via ``GPS_NETWORK_DOCS_PATH``.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def docs_root(tmp_path, monkeypatch):
    """Build a throwaway docs vault and point ``GPS_NETWORK_DOCS_PATH`` at it.

    Returns:
        The vault root path (also used to plant an out-of-tree secret in the
        traversal test).
    """
    root = tmp_path / 'vault'
    (root / 'devices').mkdir(parents=True)
    (root / 'README.md').write_text('# Index\n[pmpi1](devices/pmpi1.md)\n')
    (root / 'topology.md').write_text('# Topology\n')
    (root / 'devices' / 'pmpi1.md').write_text('# pmpi1\n')
    obsidian = root / '.obsidian'
    obsidian.mkdir()
    (obsidian / 'app.json').write_text('{}')
    monkeypatch.setenv('GPS_NETWORK_DOCS_PATH', str(root))
    return root


def test_docs_tree_structure_and_default(client, docs_root):
    res = client.get('/api/docs/tree')
    assert res.status_code == 200
    data = res.get_json()
    assert data['available'] is True
    assert data['default'] == 'README.md'

    # Directories sort before files; hidden .obsidian is excluded.
    names = [n['name'] for n in data['tree']]
    assert names[0] == 'devices'
    assert data['tree'][0]['type'] == 'dir'
    assert '.obsidian' not in names
    assert {'README.md', 'topology.md'} <= set(names)

    child = data['tree'][0]['children'][0]
    assert child == {'name': 'pmpi1.md', 'path': 'devices/pmpi1.md', 'type': 'file'}


def test_docs_file_returns_markdown(client, docs_root):
    res = client.get('/api/docs/file?path=devices/pmpi1.md')
    assert res.status_code == 200
    assert res.mimetype == 'text/markdown'
    assert '# pmpi1' in res.get_data(as_text=True)


def test_docs_file_rejects_traversal(client, docs_root, tmp_path):
    (tmp_path / 'secret.md').write_text('top secret')
    res = client.get('/api/docs/file?path=../secret.md')
    assert res.status_code == 404


def test_docs_file_rejects_non_md(client, docs_root):
    res = client.get('/api/docs/file?path=devices/pmpi1.txt')
    assert res.status_code == 400


def test_docs_tree_unconfigured(client, monkeypatch):
    monkeypatch.delenv('GPS_NETWORK_DOCS_PATH', raising=False)
    res = client.get('/api/docs/tree')
    data = res.get_json()
    assert data['available'] is False
    assert data['tree'] == []
