import os
from pathlib import Path

import requests
from flask import Blueprint, abort, send_file

tiles_bp = Blueprint('tiles', __name__)

TILE_CACHE_DIR = Path(
    os.environ.get('GPS_TILE_CACHE_DIR', Path.home() / '.cache' / 'gps-dashboard' / 'tiles')
)

OSM_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
USER_AGENT = 'gps-dashboard/1.0 (pmormr@gmail.com)'


@tiles_bp.get('/tiles/<int:z>/<int:x>/<int:y>.png')
def tile(z, x, y):
    if not (0 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        abort(400)

    cache_path = TILE_CACHE_DIR / str(z) / str(x) / f'{y}.png'

    if cache_path.exists():
        return send_file(cache_path, mimetype='image/png')

    try:
        resp = requests.get(
            OSM_URL.format(z=z, x=x, y=y),
            timeout=5,
            headers={'User-Agent': USER_AGENT},
        )
        resp.raise_for_status()
    except Exception:
        abort(503)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)
    return send_file(cache_path, mimetype='image/png')
