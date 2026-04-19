from flask import Flask

from api.db import get_connection, init_db, migrate
from api.routes.points import points_bp
from api.routes.tiles import tiles_bp
from api.routes.trips import trips_bp


def create_app():
    app = Flask(__name__, static_folder='../static', template_folder='../templates')

    conn = get_connection()
    init_db(conn)
    migrate(conn)

    app.register_blueprint(points_bp)
    app.register_blueprint(trips_bp)
    app.register_blueprint(tiles_bp)

    return app


if __name__ == '__main__':
    create_app().run(host='0.0.0.0', port=5000, debug=False)
