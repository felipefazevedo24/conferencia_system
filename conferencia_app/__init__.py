from flask import Flask
from flask import g, request, session
import os
import time
import warnings
from datetime import datetime, timedelta

# Silencia warning cosmético do openpyxl ("Workbook contains no default style")
# emitido ao ler XLSX gerados por ferramentas que não preenchem o style default.
warnings.filterwarnings(
    "ignore",
    message="Workbook contains no default style",
    category=UserWarning,
    module="openpyxl.styles.stylesheet",
)

from .bootstrap import initialize_database
from .config import Config
from .error_handlers import register_error_handlers
from .extensions import db, migrate
from .routes.api_routes import api_bp
from .routes.agendamento_routes import agendamento_bp
from .routes.auth_routes import auth_bp
from .routes.boleto_routes import boleto_bp
from .routes.conserto_routes import conserto_bp
from .routes.facilities_routes import facilities_bp
from .routes.nfe_email_routes import nfe_email_bp
from .routes.page_routes import page_bp
from .routes.rastreamento_routes import rastreamento_bp
from .routes.frota_routes import frota_bp
from .routes.viagem_routes import viagem_bp, motorista_bp
from .routes.wms_routes import wms_bp


def create_app(test_config=None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static", instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)

    app.register_blueprint(auth_bp)
    app.register_blueprint(page_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(agendamento_bp)
    app.register_blueprint(wms_bp)
    app.register_blueprint(conserto_bp)
    app.register_blueprint(boleto_bp)
    app.register_blueprint(facilities_bp)
    app.register_blueprint(nfe_email_bp)
    app.register_blueprint(rastreamento_bp)
    app.register_blueprint(frota_bp)
    app.register_blueprint(viagem_bp)
    app.register_blueprint(motorista_bp)

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
        # Garante charset=utf-8 explicito para text/html e application/json
        ctype = response.headers.get("Content-Type", "")
        if ctype.startswith("text/html") and "charset" not in ctype.lower():
            response.headers["Content-Type"] = "text/html; charset=utf-8"
        elif ctype.startswith("application/json") and "charset" not in ctype.lower():
            response.headers["Content-Type"] = "application/json; charset=utf-8"
        return response

    @app.context_processor
    def inject_access_control_helpers():
        from .auth import get_effective_permissions, has_permission

        perms = get_effective_permissions() if "username" in session else {}
        return {
            "can_access": lambda key: has_permission(key),
            "access_permissions": perms,
        }

    @app.context_processor
    def inject_asset_version():
        # Cache-busting baseado no mtime mais recente da pasta static/css.
        # Garante que deploys de produção (PythonAnywhere) sirvam a versão
        # atualizada de qualquer CSS sem depender de Ctrl+F5 dos usuários.
        try:
            css_dir = os.path.join(app.static_folder, "css")
            mtimes = []
            for root_, _dirs, files in os.walk(css_dir):
                for fn in files:
                    if fn.endswith(".css"):
                        mtimes.append(os.path.getmtime(os.path.join(root_, fn)))
            asset_version = str(int(max(mtimes))) if mtimes else str(int(time.time()))
        except Exception:
            asset_version = str(int(time.time()))
        return {"asset_version": asset_version}

    initialize_database(app)

    # Config persistida da automacao de e-mails de NF-e (sobrepoe env vars se salvo)
    try:
        from .services.nfe_email_config_store import carregar_persistido
        with app.app_context():
            carregar_persistido(app)
    except Exception:
        app.logger.exception("Falha ao carregar nfe_email_config.json")

    # Define data-corte default (hoje) se nao foi configurada ainda
    if not app.config.get("NFE_EMAIL_AUTO_DESDE"):
        from datetime import date
        app.config["NFE_EMAIL_AUTO_DESDE"] = date.today().isoformat()

    # Inicia scheduler somente fora de processo de teste
    if app.config.get("NFE_EMAIL_AUTO_ENABLED") and not app.config.get("TESTING"):
        try:
            from .services.nfe_email_scheduler import iniciar_scheduler
            iniciar_scheduler(app)
        except Exception:
            app.logger.exception("Falha ao iniciar scheduler NF-e")

    # Scheduler de sincronizacao ERP -> WMS (estoque + enderecos)
    if app.config.get("ERP_SYNC_AUTO_ENABLED") and not app.config.get("TESTING"):
        try:
            from .services.erp_sync_scheduler import iniciar_scheduler as iniciar_sync_erp
            iniciar_sync_erp(app)
        except Exception:
            app.logger.exception("Falha ao iniciar scheduler ERP Sync")

    return app
