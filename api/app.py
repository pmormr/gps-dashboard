from flask import Flask, render_template

from api.db import get_connection, init_db, migrate
from api.routes.annotations import annotations_bp
from api.routes.drone import drone_bp
from api.routes.obd import obd_bp
from api.routes.points import points_bp
from api.routes.sensors import sensors_bp
from api.routes.status_gpsd import status_gpsd_bp
from api.routes.status_ntp import status_ntp_bp
from api.routes.tiles import tiles_bp


def create_app():
    app = Flask(__name__, static_folder='../static', template_folder='../templates')

    conn = get_connection()
    init_db(conn)
    migrate(conn)

    app.register_blueprint(points_bp)
    app.register_blueprint(sensors_bp)
    app.register_blueprint(annotations_bp)
    app.register_blueprint(drone_bp)
    app.register_blueprint(obd_bp)
    app.register_blueprint(tiles_bp)
    app.register_blueprint(status_gpsd_bp)
    app.register_blueprint(status_ntp_bp)

    @app.get('/')
    def index():
        return render_template('index.html')

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=5000, debug=False, threaded=True)
