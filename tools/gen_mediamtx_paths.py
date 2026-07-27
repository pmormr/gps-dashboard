#!/usr/bin/env python3
"""Render the van hub's publisher paths in ``deploy/mediamtx.yml`` from the registry.

B8 (``.claude/modules/broadcast.md``): ``broadcast/feeds.py`` is the single
source of truth for every MediaMTX path. The van hub's simple ``source: publisher``
paths — the hill-climb SRT cams, the DJI-RTMP drone slots, and the in-hub radio
audio — are mechanical to derive from it, so this tool writes them into the hub
config between sentinel markers instead of maintaining a second hand-kept copy.

Everything outside the markers is left byte-for-byte untouched: the hand-written
header and, crucially, the Dahua on-demand proxy block (cameras-owned, carries the
``${GPS_DAHUA_PASSWORD_URLENC}`` secret placeholder and per-camera source URLs the
registry does not model).

``tests/test_gen_mediamtx_paths.py`` asserts the committed block equals this render,
so a registry edit that isn't regenerated fails the suite. Regenerate with::

    uv run tools/gen_mediamtx_paths.py            # write in place
    uv run tools/gen_mediamtx_paths.py --check    # verify only; exit 1 on drift

The cloud hub's ``mediamtx.yml`` lives on a different box/repo and stays
hand-maintained (B8) — this tool is van-only.
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from broadcast.feeds import FEEDS, Feed
from common.cli import run_cli

#: The van hub config whose generated slice this tool owns.
DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / 'deploy' / 'mediamtx.yml'

#: Sentinel lines bounding the generated region. The two-space indent keeps them
#: valid inside the ``paths:`` mapping; the render replaces everything between
#: them (inclusive), so the markers themselves are part of the block.
BEGIN = '  # >>> BEGIN generated van paths — tools/gen_mediamtx_paths.py (do not edit) <<<'
END = '  # >>> END generated van paths <<<'


def _publish_hint(feed: Feed) -> str:
    """A short 'how it's fed' tag for a path's one-line comment (from the registry)."""
    if feed.send is not None and feed.transport == 'srt':
        return f'SRT publish :{feed.send.port}'
    if feed.send is not None and feed.transport == 'rtmp':
        return f'RTMP publish :{feed.send.port}'
    return 'in-hub publisher'


def generated_feeds() -> list[Feed]:
    """The van paths this tool renders: ``source: publisher`` feeds, registry order.

    Every van feed whose MediaMTX source is a live publisher — the SRT cams, the
    RTMP drone slots, and the internal radio path. Excludes the van Dahua proxy
    block (role ``proxy``), which is hand-maintained with its secret placeholder.

    Returns:
        The matching feeds, in ``FEEDS`` order.
    """
    return [f for f in FEEDS if f.hub == 'van' and f.role in ('publish', 'internal')]


def render_block() -> str:
    """Render the marker-to-marker generated block (no trailing newline).

    Returns:
        The block as it must appear in ``deploy/mediamtx.yml``, from the BEGIN
        marker line through the END marker line.
    """
    lines = [
        BEGIN,
        '  # Van publisher paths, generated from broadcast/feeds.py (B8). The Dahua',
        '  # on-demand proxy block below is hand-maintained (cameras-owned).',
    ]
    for feed in generated_feeds():
        lines.append('')
        lines.append(f'  # {feed.path} — {feed.label} ({_publish_hint(feed)})')
        lines.append(f'  {feed.path}:')
        lines.append('    source: publisher')
    lines.append(END)
    return '\n'.join(lines)


def _marker_bounds(lines: list[str]) -> tuple[int, int]:
    """Locate the BEGIN/END marker line indices, or raise if malformed."""
    try:
        i = lines.index(BEGIN)
        j = lines.index(END)
    except ValueError as exc:
        raise ValueError(
            f'sentinel markers not found in config (expected {BEGIN!r} … {END!r})'
        ) from exc
    if j < i:
        raise ValueError('END marker precedes BEGIN marker')
    return i, j


def extract_block(existing: str) -> str:
    """Return the current marker-bounded region of ``existing`` (for the drift test)."""
    lines = existing.splitlines()
    i, j = _marker_bounds(lines)
    return '\n'.join(lines[i : j + 1])


def splice(existing: str, block: str) -> str:
    """Replace the marker-bounded region of ``existing`` with ``block``.

    Args:
        existing: The full current config text.
        block: The replacement region (a :func:`render_block` result).

    Returns:
        The updated config text, newline-terminated. Everything outside the
        markers is preserved verbatim.

    Raises:
        ValueError: if either marker is missing or they are out of order.
    """
    lines = existing.splitlines()
    i, j = _marker_bounds(lines)
    return '\n'.join(lines[:i] + block.splitlines() + lines[j + 1 :]) + '\n'


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--config',
        type=Path,
        default=DEFAULT_CONFIG,
        help='hub config to render into (default: deploy/mediamtx.yml)',
    )
    parser.add_argument(
        '--check',
        action='store_true',
        help='verify only: print a diff and exit 1 on drift, exit 0 if up to date',
    )
    return parser.parse_args()


def main() -> int:
    """CLI entrypoint.

    Returns:
        A process exit code (1 under ``--check`` when the committed block drifts
        from the registry render, else 0).
    """
    args = parse_args()
    existing = args.config.read_text()
    updated = splice(existing, render_block())

    if existing == updated:
        print(f'{args.config}: generated van paths up to date.')
        return 0

    if args.check:
        diff = difflib.unified_diff(
            existing.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f'{args.config} (committed)',
            tofile=f'{args.config} (from registry)',
        )
        sys.stdout.writelines(diff)
        print(
            f'{args.config}: DRIFT — run `uv run tools/gen_mediamtx_paths.py`.',
            file=sys.stderr,
        )
        return 1

    args.config.write_text(updated)
    print(f'{args.config}: rewrote generated van paths.')
    return 0


if __name__ == '__main__':
    run_cli(main)
