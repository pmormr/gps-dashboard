"""Tests for the van mediamtx.yml path generator (tools/gen_mediamtx_paths.py).

The load-bearing one is the drift guard: the committed generated block in
``deploy/mediamtx.yml`` must equal what the registry renders, so a ``feeds.py``
edit that isn't regenerated fails here. The rest pin the selection (which feeds
are generated) and that the splice leaves the hand-maintained Dahua block alone.
"""

from __future__ import annotations

import pytest

from tools.gen_mediamtx_paths import (
    DEFAULT_CONFIG,
    extract_block,
    generated_feeds,
    render_block,
    splice,
)


def test_committed_block_matches_registry_render() -> None:
    """The drift guard: deploy/mediamtx.yml's generated region == the render."""
    committed = DEFAULT_CONFIG.read_text()
    assert extract_block(committed) == render_block(), (
        'deploy/mediamtx.yml is stale — run `uv run tools/gen_mediamtx_paths.py`.'
    )


def test_generated_feeds_are_van_publishers_in_order() -> None:
    """Only van publisher/internal paths are generated, in registry order."""
    feeds = generated_feeds()
    assert [f.path for f in feeds] == [
        'top-1',
        'top-2',
        'finish-1',
        'saddle-1',
        'saddle-2',
        'saddle-3',
        'radio',
        'drone1',
        'drone2',
    ]
    assert all(f.hub == 'van' for f in feeds)
    assert all(f.role in ('publish', 'internal') for f in feeds)
    # The Dahua proxy block and every cloud path are excluded.
    assert not any(f.role == 'proxy' or f.hub == 'cloud' for f in feeds)


def test_render_block_is_pure_publisher_source() -> None:
    """The generated block declares publisher sources only — no secret material."""
    block = render_block()
    assert block.count('source: publisher') == len(generated_feeds())
    assert '${' not in block  # no env placeholders leak into the generated block
    assert 'rtsp://' not in block


def test_splice_is_idempotent_and_preserves_dahua_block() -> None:
    """Re-splicing the committed file is a no-op and never touches the Dahua block."""
    committed = DEFAULT_CONFIG.read_text()
    respliced = splice(committed, render_block())
    assert respliced == committed
    assert '${GPS_DAHUA_PASSWORD_URLENC}' in respliced
    assert 'cam-front-main:' in respliced


def test_splice_requires_markers() -> None:
    """A config missing the sentinel markers is a hard error, not a silent append."""
    with pytest.raises(ValueError, match='markers not found'):
        splice('paths:\n  radio:\n    source: publisher\n', render_block())
