from flask import Blueprint, jsonify

tiles_bp = Blueprint('tiles', __name__)


@tiles_bp.get('/tiles/<int:z>/<int:x>/<int:y>.png')
def tile(z, x, y):
    # Stub — full implementation in Phase 4
    return jsonify({'error': 'Tile proxy not yet implemented'}), 503
