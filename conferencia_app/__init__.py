from flask import Flask
from flask import g, request, session
import time
from datetime import datetime, timedelta

from .bootstrap import initialize_database
from .config import Config
from .error_handlers import register_error_handlers
from .extensions import db, migrate
from .routes.api_routes import api_bp
from .routes.auth_routes import auth_bp
from .routes.page_routes import page_bp


def create_app(test_config=None) -> Flask:
    app = Flask(__name__, template_folder="../templates", instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    migrate.init_app(app, db)

    app.register_blueprint(auth_bp)
    app.register_blueprint(page_bp)
    app.register_blueprint(api_bp)

    register_error_handlers(app)

    @app.before_request
    def before_request_logging():
        g.start_time = time.perf_counter()
        if "username" in session:
            now = datetime.now()
            last_activity_raw = session.get("last_activity")
            if last_activity_raw:
                last_activity = datetime.fromisoformat(last_activity_raw)
                timeout = timedelta(minutes=app.config.get("SESSION_TIMEOUT_MINUTES", 30))
                if now - last_activity > timeout:
                    session.clear()
            session["last_activity"] = now.isoformat()
            session.permanent = True

    @app.after_request
    def after_request_logging(response):
        elapsed_ms = (time.perf_counter() - g.start_time) * 1000
        app.logger.info("%s %s %s %.2fms", request.method, request.path, response.status_code, elapsed_ms)
        return response

    initialize_database(app)
    return app