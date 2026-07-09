import os

from flask import Flask, abort, send_file

from api.db import get_connection, init_db
from api.routes.annotations import annotations_bp
from api.routes.docs import docs_bp
from api.routes.drone import drone_bp
from api.routes.globe import globe_bp
from api.routes.obd import obd_bp
from api.routes.passes import passes_bp
from api.routes.phone import phone_bp
from api.routes.places import places_bp
from api.routes.points import points_bp
from api.routes.radio import radio_bp
from api.routes.sensors import sensors_bp
from api.routes.status import status_bp
from api.routes.status_gpsd import status_gpsd_bp
from api.routes.status_ntp import status_ntp_bp
from api.routes.tiles import tiles_bp


def create_app():
    app = Flask(__name__, static_folder='../static')

    conn = get_connection()
    init_db(conn)

    app.register_blueprint(points_bp)
    app.register_blueprint(sensors_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(annotations_bp)
    app.register_blueprint(docs_bp)
    app.register_blueprint(drone_bp)
    app.register_blueprint(globe_bp)
    app.register_blueprint(passes_bp)
    app.register_blueprint(obd_bp)
    app.register_blueprint(phone_bp)
    app.register_blueprint(places_bp)
    app.register_blueprint(radio_bp)
    app.register_blueprint(tiles_bp)
    app.register_blueprint(status_gpsd_bp)
    app.register_blueprint(status_ntp_bp)

    dist_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'static', 'dist'))

    def spa_index():
        """Serve the built Van OS SPA shell (static/dist/index.html)."""
        index_path = os.path.join(dist_dir, 'index.html')
        if not os.path.exists(index_path):
            abort(503, description='Frontend not built — run `npm run build` in web/.')
        return send_file(index_path)

    @app.get('/')
    def index():
        return spa_index()

    @app.get('/<path:path>')
    def spa_catchall(path):
        """Serve the SPA shell for client-side routes; never shadow api/tiles/static."""
        if path.startswith(('api/', 'tiles/', 'static/')):
            abort(404)
        return spa_index()

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=5000, debug=False, threaded=True)
