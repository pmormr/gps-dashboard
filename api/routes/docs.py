"""Network-docs reader/editor: serves the synced `paul-network-docs` Obsidian vault.

The vault is checked out on the Pi (a sibling bare repo + post-receive hook,
mirroring the gps-dashboard deploy) at the path named by ``GPS_NETWORK_DOCS_PATH``.
This blueprint is a thin file server over that tree: a markdown file tree, raw
markdown bodies, and an edit-in-place PUT. The SPA Docs view renders the markdown
client-side.

Writes are auto-committed. The Pi checkout is a detached work tree of the bare
repo (``GPS_NETWORK_DOCS_GIT_DIR``), so commits land directly on its ``main`` —
a stale laptop ``git push pi main`` is then rejected as non-fast-forward instead
of clobbering web edits; pull before pushing. (The no-commits-on-the-Pi rule is
gps-dashboard's, not this vault's.) Locally the vault is a normal clone, so the
fallback git dir is ``<root>/.git``; with neither, saves still work uncommitted.
"""

import hashlib
import logging
import os
import tempfile
import threading

from flask import Blueprint, Response, abort, jsonify, request

from common.proc import run

docs_bp = Blueprint('docs', __name__)

log = logging.getLogger(__name__)

_GIT_AUTHOR = ['-c', 'user.name=van-dashboard', '-c', 'user.email=dashboard@van.pmormr.com']

# Serializes write+commit so two rapid saves can't interleave `git add`/`commit`.
_write_lock = threading.Lock()


def _root() -> str | None:
    """Return the realpath of the docs vault, or None if unset/missing.

    Returns:
        The resolved docs root directory, or None when ``GPS_NETWORK_DOCS_PATH``
        is unset or does not point at a directory (the Docs tab then shows an
        empty state rather than erroring).
    """
    path = os.environ.get('GPS_NETWORK_DOCS_PATH')
    if not path:
        return None
    resolved = os.path.realpath(path)
    return resolved if os.path.isdir(resolved) else None


def _resolve(root: str, rel: str) -> str | None:
    """Resolve a request-relative path to an absolute path confined to ``root``.

    Args:
        root: The realpath'd docs root.
        rel: The client-supplied relative path (e.g. ``devices/pmpi1.md``).

    Returns:
        The absolute realpath when it stays within ``root`` (symlink- and
        ``..``-safe via realpath comparison), else None.
    """
    candidate = os.path.realpath(os.path.join(root, rel))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    return candidate


def _build_tree(abs_dir: str, root: str) -> list[dict]:
    """Recursively build the markdown tree under ``abs_dir``.

    Hidden entries (``.git``, ``.obsidian``, …) are skipped; only ``.md`` files
    and directories that (transitively) contain them are included. Directories
    sort before files, each alphabetically.

    Args:
        abs_dir: The directory to walk.
        root: The docs root, for computing relative paths.

    Returns:
        A list of nodes ``{name, path, type, children?}`` where ``type`` is
        ``'dir'`` or ``'file'`` and ``path`` is relative to ``root``.
    """
    entries: list[dict] = []
    for name in sorted(os.listdir(abs_dir)):
        if name.startswith('.'):
            continue
        abs_path = os.path.join(abs_dir, name)
        rel = os.path.relpath(abs_path, root)
        if os.path.isdir(abs_path):
            children = _build_tree(abs_path, root)
            if children:
                entries.append({'name': name, 'path': rel, 'type': 'dir', 'children': children})
        elif name.endswith('.md'):
            entries.append({'name': name, 'path': rel, 'type': 'file'})
    entries.sort(key=lambda e: (e['type'] != 'dir', e['name'].lower()))
    return entries


def _first_file(tree: list[dict]) -> str | None:
    """Return the path of the first file in a depth-first walk of ``tree``."""
    for node in tree:
        if node['type'] == 'file':
            return node['path']
        found = _first_file(node.get('children', []))
        if found:
            return found
    return None


def _default_doc(root: str, tree: list[dict]) -> str | None:
    """Pick the doc to open by default: root ``README.md`` if present, else first file."""
    if os.path.isfile(os.path.join(root, 'README.md')):
        return 'README.md'
    return _first_file(tree)


@docs_bp.get('/api/docs/tree')
def docs_tree() -> Response:
    """Markdown file tree of the network-docs vault (empty when unconfigured)."""
    root = _root()
    if root is None:
        return jsonify({'available': False, 'default': None, 'tree': []})
    tree = _build_tree(root, root)
    return jsonify({'available': True, 'default': _default_doc(root, tree), 'tree': tree})


def _target(root: str) -> tuple[str, str]:
    """Validate the request's ``path`` param to an existing in-vault ``.md`` file.

    Args:
        root: The realpath'd docs root.

    Returns:
        ``(rel, abs_path)`` for the requested file. Aborts 400 on a non-``.md``
        path and 404 when the file is missing or escapes the root.
    """
    rel = request.args.get('path', '')
    if not rel.endswith('.md'):
        abort(400, description='Only .md files are served.')
    abs_path = _resolve(root, rel)
    if abs_path is None or not os.path.isfile(abs_path):
        abort(404)
    return rel, abs_path


def _git_dirs(root: str) -> tuple[str, str] | None:
    """Locate the git dir / work tree to commit vault edits into.

    Returns:
        ``(git_dir, work_tree)`` — ``GPS_NETWORK_DOCS_GIT_DIR`` with the vault as
        a detached work tree (the Pi's bare-repo layout) when set, else the
        vault's own ``.git`` (a normal local clone), else None (no repo: saves
        proceed uncommitted).
    """
    git_dir = os.environ.get('GPS_NETWORK_DOCS_GIT_DIR')
    if git_dir and os.path.isdir(git_dir):
        return os.path.realpath(git_dir), root
    local = os.path.join(root, '.git')
    if os.path.isdir(local):
        return local, root
    return None


def _commit(root: str, rel: str) -> bool:
    """Commit one edited vault file, if the vault has a repo to commit into.

    Args:
        root: The docs root (work tree).
        rel: The vault-relative path that was written.

    Returns:
        True when the edit is in a commit (including the identical-content case,
        where there is nothing to commit); False when no repo is configured or
        any git step failed (the save itself has already succeeded).
    """
    dirs = _git_dirs(root)
    if dirs is None:
        return False
    git = ['git', f'--git-dir={dirs[0]}', f'--work-tree={dirs[1]}']
    code, _, err = run([*git, 'add', '--', rel], timeout=15)
    if code != 0:
        log.warning('docs commit: git add failed for %s: %s', rel, err.strip())
        return False
    code, _, _ = run([*git, 'diff', '--cached', '--quiet', '--', rel], timeout=15)
    if code == 0:
        return True
    msg = f'docs: edit {rel} via dashboard'
    code, _, err = run([*git, *_GIT_AUTHOR, 'commit', '-m', msg, '--', rel], timeout=15)
    if code != 0:
        log.warning('docs commit: git commit failed for %s: %s', rel, err.strip())
        return False
    return True


def _etag(content: bytes) -> str:
    """Content hash used as the ETag for optimistic-concurrency checks."""
    return hashlib.sha256(content).hexdigest()


@docs_bp.get('/api/docs/file')
def docs_file() -> Response:
    """Raw markdown body of one vault file, confined to the docs root.

    The ``ETag`` is a content hash; the editor echoes it back via ``If-Match``
    on PUT so a file changed underneath an open editor is a 409, not data loss.
    """
    root = _root()
    if root is None:
        abort(404)
    _, abs_path = _target(root)
    with open(abs_path, 'rb') as fh:
        content = fh.read()
    resp = Response(content, content_type='text/markdown; charset=utf-8')
    resp.set_etag(_etag(content))
    return resp


@docs_bp.put('/api/docs/file')
def docs_file_put() -> Response:
    """Overwrite one existing vault file and auto-commit the edit.

    Edit-only by design: the target must already exist (file creation stays a
    laptop/Obsidian operation). Requires ``If-Match`` (428 without it, 409 on a
    stale hash). The write is atomic (temp file + rename) and serialized with
    the commit under a lock.
    """
    root = _root()
    if root is None:
        abort(404)
    rel, abs_path = _target(root)
    if not request.if_match:
        abort(428, description='If-Match (the ETag from GET) is required.')
    body = request.get_data()
    with _write_lock:
        with open(abs_path, 'rb') as fh:
            current = fh.read()
        if _etag(current) not in request.if_match:
            abort(409, description='File changed since it was loaded — reload and re-apply.')
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(abs_path), suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as fh:
                fh.write(body)
            os.replace(tmp, abs_path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        committed = _commit(root, rel)
    resp = jsonify({'path': rel, 'committed': committed})
    resp.set_etag(_etag(body))
    return resp
