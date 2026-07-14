"""Build the unified POI sprite from the vendored Maki/Temaki SVGs.

The one icon language for the map (`plans/poi-map-unification-plan.md`): both
the basemap ``pois`` layer and the places-tier pin layers reference this
sprite (MapLibre multi-sprite id ``poi``), and the HTML UI renders the same
SVGs directly from ``static/vendor/poi-icons/svg/``.

Dev-time only — the generated sprite (``static/vendor/basemap/sprite-poi/``)
is committed, so the Pi never runs this. Requires the ``spreet`` binary on
PATH (``cargo install spreet`` or a GitHub release binary); the SVG sources
are vendored (CC0) — adding an icon = drop the SVG in ``svg/`` and re-run:

    uv run tools/build_poi_sprite.py

The kind/category → icon-name mapping lives in ``web/src/lib/icons.ts``; a
vitest there checks every referenced icon exists in the committed sprite JSON,
so a missing SVG fails the web tests, not the map at runtime.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from common.cli import run_cli

REPO = Path(__file__).resolve().parent.parent
SVG_DIR = REPO / 'static' / 'vendor' / 'poi-icons' / 'svg'
OUT_DIR = REPO / 'static' / 'vendor' / 'basemap' / 'sprite-poi'


def main() -> int:
    """Generate the 1x and @2x sprite sheets with spreet.

    Returns:
        A process exit code.
    """
    spreet = shutil.which('spreet')
    if spreet is None:
        print('spreet not found on PATH (cargo install spreet)', file=sys.stderr)
        return 1
    if not SVG_DIR.is_dir():
        print(f'SVG source dir missing: {SVG_DIR}', file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # SDF sprites so MapLibre's icon-color can tint one monochrome set per
    # category — the mechanism the whole one-icon-language design rides on.
    for args, out in (
        (['--unique', '--sdf'], OUT_DIR / 'poi'),
        (['--unique', '--sdf', '--retina'], OUT_DIR / 'poi@2x'),
    ):
        subprocess.run([spreet, *args, str(SVG_DIR), str(out)], check=True)
        print(f'wrote {out}.png / {out}.json', flush=True)
    n = len(list(SVG_DIR.glob('*.svg')))
    print(f'{n} SVGs → sprite', flush=True)
    return 0


if __name__ == '__main__':
    run_cli(main)
