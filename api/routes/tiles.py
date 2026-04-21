import os
import threading
from pathlib import Path

import requests
from flask import Blueprint, abort, request, send_file

tiles_bp = Blueprint('tiles', __name__)

TILE_CACHE_DIR = Path(
    os.environ.get('GPS_TILE_CACHE_DIR', Path.home() / '.cache' / 'gps-dashboard' / 'tiles')
)

OSM_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
USER_AGENT = 'gps-dashboard/1.0 (pmormr@gmail.com)'


def _etag_path(cache_path):
    return cache_path.with_suffix('.etag')


def _fetch_osm(z, x, y, etag=None):
    headers = {'User-Agent': USER_AGENT}
    if etag:
        headers['If-None-Match'] = etag
    return requests.get(OSM_URL.format(z=z, x=x, y=y), timeout=5, headers=headers)


def _save_tile(cache_path, content, etag=None):
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(content)
    if etag:
        _etag_path(cache_path).write_text(etag)


def _background_refresh(z, x, y, cache_path):
    etag_file = _etag_path(cache_path)
    etag = etag_file.read_text().strip() if etag_file.exists() else None
    try:
        resp = _fetch_osm(z, x, y, etag=etag)
        if resp.status_code == 200:
            _save_tile(cache_path, resp.content, resp.headers.get('ETag'))
    except Exception:
        pass


@tiles_bp.get('/tiles/<int:z>/<int:x>/<int:y>.png')
def tile(z, x, y):
    if not (0 <= z <= 19 and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        abort(400)

    cache_path = TILE_CACHE_DIR / str(z) / str(x) / f'{y}.png'
    refresh = request.args.get('refresh') == '1'

    if cache_path.exists():
        if refresh:
            threading.Thread(
                target=_background_refresh, args=(z, x, y, cache_path), daemon=True
            ).start()
        return send_file(cache_path, mimetype='image/png')

    try:
        resp = _fetch_osm(z, x, y)
        resp.raise_for_status()
    except Exception:
        abort(503)

    _save_tile(cache_path, resp.content, resp.headers.get('ETag'))
    return send_file(cache_path, mimetype='image/png')
