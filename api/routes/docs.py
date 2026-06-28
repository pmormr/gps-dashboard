"""Network-docs reader: serves the synced `paul-network-docs` Obsidian vault.

The vault is checked out on the Pi (a sibling bare repo + post-receive hook,
mirroring the gps-dashboard deploy) at the path named by ``GPS_NETWORK_DOCS_PATH``.
This blueprint is a thin, read-only file server over that tree: a markdown file
tree plus raw markdown bodies. The SPA Docs view renders the markdown client-side.
"""

import os

from flask import Blueprint, Response, abort, jsonify, request

docs_bp = Blueprint('docs', __name__)


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


@docs_bp.get('/api/docs/file')
def docs_file() -> Response:
    """Raw markdown body of one vault file, confined to the docs root."""
    root = _root()
    if root is None:
        abort(404)
    rel = request.args.get('path', '')
    if not rel.endswith('.md'):
        abort(400, description='Only .md files are served.')
    abs_path = _resolve(root, rel)
    if abs_path is None or not os.path.isfile(abs_path):
        abort(404)
    with open(abs_path, encoding='utf-8') as fh:
        content = fh.read()
    return Response(content, content_type='text/markdown; charset=utf-8')
