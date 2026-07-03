"""Tests for the network-docs reader/editor routes (`api/routes/docs.py`).

Covers the file tree (structure + default pick), raw markdown fetch, the
rejection paths that matter for a file server (path traversal, non-`.md`),
and the edit PUT: If-Match concurrency, atomic save, and the auto-commit in
both repo layouts (a normal `.git` clone and the Pi's bare-repo detached work
tree). A throwaway vault is wired via ``GPS_NETWORK_DOCS_PATH``.
"""

from __future__ import annotations

import subprocess

import pytest


@pytest.fixture
def docs_root(tmp_path, monkeypatch):
    """Build a throwaway docs vault and point ``GPS_NETWORK_DOCS_PATH`` at it.

    Returns:
        The vault root path (also used to plant an out-of-tree secret in the
        traversal test). No git repo — the uncommitted-save case by default.
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
    monkeypatch.delenv('GPS_NETWORK_DOCS_GIT_DIR', raising=False)
    return root


def _git(*args: str) -> str:
    """Run a git command for test setup/assertions, failing loudly."""
    return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def docs_repo(docs_root):
    """Turn the vault into a normal clone (a `.git` inside the root)."""
    _git('-C', str(docs_root), 'init', '-q')
    _git(
        '-C',
        str(docs_root),
        '-c',
        'user.name=t',
        '-c',
        'user.email=t@t',
        'commit',
        '-q',
        '--allow-empty',
        '-m',
        'init',
    )
    return docs_root


@pytest.fixture
def docs_bare_repo(docs_root, tmp_path, monkeypatch):
    """Mirror the Pi layout: a bare git dir with the vault as detached work tree."""
    git_dir = tmp_path / 'vault.git'
    _git('init', '-q', '--bare', str(git_dir))
    monkeypatch.setenv('GPS_NETWORK_DOCS_GIT_DIR', str(git_dir))
    return git_dir


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


def _put(client, path: str, body: str, etag: str | None):
    headers = {'If-Match': f'"{etag}"'} if etag else {}
    return client.put(f'/api/docs/file?path={path}', data=body, headers=headers)


def _get_etag(client, path: str) -> str:
    res = client.get(f'/api/docs/file?path={path}')
    assert res.status_code == 200
    etag, _ = res.get_etag()
    assert etag
    return etag


def test_docs_file_sets_etag(client, docs_root):
    etag = _get_etag(client, 'devices/pmpi1.md')
    assert len(etag) == 64  # sha256 hex


def test_put_saves_and_commits_normal_clone(client, docs_repo):
    etag = _get_etag(client, 'devices/pmpi1.md')
    res = _put(client, 'devices/pmpi1.md', '# pmpi1\nedited\n', etag)
    assert res.status_code == 200
    assert res.get_json()['committed'] is True
    assert (docs_repo / 'devices' / 'pmpi1.md').read_text() == '# pmpi1\nedited\n'
    log = _git('-C', str(docs_repo), 'log', '-1', '--format=%s %an')
    assert log.strip() == 'docs: edit devices/pmpi1.md via dashboard van-dashboard'


def test_put_commits_into_bare_repo_work_tree(client, docs_root, docs_bare_repo):
    etag = _get_etag(client, 'topology.md')
    res = _put(client, 'topology.md', '# Topology\nedited\n', etag)
    assert res.status_code == 200
    assert res.get_json()['committed'] is True
    log = _git(f'--git-dir={docs_bare_repo}', 'log', '-1', '--format=%s')
    assert log.strip() == 'docs: edit topology.md via dashboard'


def test_put_without_repo_saves_uncommitted(client, docs_root):
    etag = _get_etag(client, 'topology.md')
    res = _put(client, 'topology.md', '# Topology\nedited\n', etag)
    assert res.status_code == 200
    assert res.get_json()['committed'] is False
    assert (docs_root / 'topology.md').read_text() == '# Topology\nedited\n'


def test_put_identical_content_reports_committed(client, docs_repo):
    etag = _get_etag(client, 'topology.md')
    res = _put(client, 'topology.md', '# Topology\n', etag)
    assert res.status_code == 200
    assert res.get_json()['committed'] is True  # nothing to commit == already in a commit


def test_put_stale_etag_conflicts(client, docs_root):
    etag = _get_etag(client, 'topology.md')
    (docs_root / 'topology.md').write_text('# Topology\nchanged elsewhere\n')
    res = _put(client, 'topology.md', '# Topology\nmy edit\n', etag)
    assert res.status_code == 409
    assert (docs_root / 'topology.md').read_text() == '# Topology\nchanged elsewhere\n'


def test_put_requires_if_match(client, docs_root):
    res = _put(client, 'topology.md', '# Topology\nedited\n', None)
    assert res.status_code == 428


def test_put_rejects_new_files(client, docs_root):
    res = _put(client, 'devices/new-device.md', '# new\n', 'deadbeef')
    assert res.status_code == 404


def test_put_rejects_traversal(client, docs_root, tmp_path):
    (tmp_path / 'secret.md').write_text('top secret')
    res = _put(client, '../secret.md', 'overwrite', 'deadbeef')
    assert res.status_code == 404
    assert (tmp_path / 'secret.md').read_text() == 'top secret'


def test_put_rejects_non_md(client, docs_root):
    res = _put(client, 'devices/pmpi1.txt', 'x', 'deadbeef')
    assert res.status_code == 400
