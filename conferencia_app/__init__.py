from flask import Flask
from flask import g, request, session
import os
import subprocess
import time
import warnings
from datetime import datetime, timedelta


def _compute_build_info() -> str:
    # Le o commit atual do HEAD uma unica vez, no import do modulo (nao a
    # cada request) - serve pra confirmar visualmente (rodape) se um deploy
    # (ex.: PythonAnywhere) de fato reiniciou o processo apos um git pull,
    # ja que so atualizar os arquivos no disco nao troca o codigo em memoria.
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, stderr=subprocess.DEVNULL, timeout=3,
        ).decode().strip()
        commit_date = subprocess.check_output(
            ["git", "log", "-1", "--format=%cd", "--date=format:%d/%m %H:%M"],
            cwd=repo_root, stderr=subprocess.DEVNULL, timeout=3,
        ).decode().strip()
        return f"{commit} · {commit_date}"
    except Exception:
        return "dev"


_BUILD_INFO = _compute_build_info()

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
from .routes.atualizacao_cadastral_routes import atualizacao_cadastral_bp
from .routes.auth_routes import auth_bp
from .routes.boleto_routes import boleto_bp
from .routes.cadastro_workflow_routes import cadastro_workflow_bp
from .routes.compras_routes import compras_bp
from .routes.expedicao_avulso_routes import expedicao_avulso_bp
from .routes.expedicao_fat_routes import expedicao_fat_bp
from .routes.expedicao_st_routes import expedicao_st_bp
from .routes.expedicao_romaneio_routes import expedicao_romaneio_bp
from .routes.expedicao_auditoria_routes import expedicao_auditoria_bp
from .routes.expedicao_assistente_routes import expedicao_assistente_bp
from .routes.facilities_routes import facilities_bp
from .routes.nfe_email_routes import nfe_email_bp
from .routes.page_routes import page_bp
from .routes.painel_tv_routes import painel_tv_bp
from .routes.planner_routes import planner_bp
from .routes.qualidade_routes import qualidade_bp
from .routes.rastreamento_routes import rastreamento_bp
from .routes.frota_routes import frota_bp
from .routes.solicitacao_nf_routes import solicitacao_nf_bp
from .routes.logistica_inventario_routes import logistica_inventario_bp
from .routes.viagem_routes import viagem_bp, motorista_bp
from .routes.comex_routes import comex_bp


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
    app.register_blueprint(planner_bp)
    app.register_blueprint(painel_tv_bp)
    app.register_blueprint(atualizacao_cadastral_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(agendamento_bp)
    app.register_blueprint(boleto_bp)
    app.register_blueprint(cadastro_workflow_bp)
    app.register_blueprint(compras_bp)
    app.register_blueprint(expedicao_fat_bp)
    app.register_blueprint(expedicao_st_bp)
    app.register_blueprint(expedicao_avulso_bp)
    app.register_blueprint(expedicao_romaneio_bp)
    app.register_blueprint(expedicao_auditoria_bp)
    app.register_blueprint(expedicao_assistente_bp)
    app.register_blueprint(comex_bp)
    app.register_blueprint(facilities_bp)
    app.register_blueprint(nfe_email_bp)
    app.register_blueprint(rastreamento_bp)
    app.register_blueprint(frota_bp)
    app.register_blueprint(logistica_inventario_bp)
    app.register_blueprint(viagem_bp)
    app.register_blueprint(motorista_bp)
    app.register_blueprint(solicitacao_nf_bp)

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
    def inject_current_user_prefs():
        from .models import Usuario

        if "username" not in session:
            return {"current_user_display": None, "current_user_tema": "claro"}
        try:
            user = Usuario.query.filter_by(username=session["username"]).first()
        except Exception:
            user = None
        if not user:
            return {"current_user_display": None, "current_user_tema": "claro"}
        return {
            "current_user_display": user.nome_exibicao or user.username,
            "current_user_tema": user.tema or "claro",
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
        return {"asset_version": asset_version, "build_info": _BUILD_INFO}

    skip_bootstrap = str(os.environ.get("SKIP_APP_BOOTSTRAP", "")).strip().lower() in {"1", "true", "yes", "on"}
    if not skip_bootstrap:
        initialize_database(app)
    else:
        app.logger.warning("SKIP_APP_BOOTSTRAP ativo: inicializacao de bootstrap de banco foi ignorada.")

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

    # Em hospedagens WSGI como PythonAnywhere, background threads podem nascer em
    # mais de um worker e deixar a pagina lenta. Por padrao, rode NF-e por tarefa agendada.
    if (
        app.config.get("NFE_EMAIL_AUTO_ENABLED")
        and app.config.get("NFE_EMAIL_BACKGROUND_SCHEDULER_ENABLED")
        and not app.config.get("TESTING")
    ):
        try:
            from .services.nfe_email_scheduler import iniciar_scheduler
            iniciar_scheduler(app)
        except Exception:
            app.logger.exception("Falha ao iniciar scheduler NF-e")

    # Scheduler de lancamento automatico via ERP (Postgres tcompras)
    if app.config.get("ERP_LANCAMENTO_AUTO_ENABLED") and not app.config.get("TESTING"):
        try:
            from .services.erp_lancamento_scheduler import iniciar_scheduler as iniciar_erp_lancamento
            iniciar_erp_lancamento(app)
        except Exception:
            app.logger.exception("Falha ao iniciar scheduler ERP Lancamento")

    # Scheduler de sincronizacao das ordens de Conferencia de Expedicao
    # (Faturamento + ST). Solucao satelite: busca continua no servidor para
    # nao perder ordens no intervalo entre solicitacao e faturamento.
    if app.config.get("EXPEDICAO_SYNC_AUTO_ENABLED") and not app.config.get("TESTING"):
        try:
            from .services.expedicao_sync_scheduler import iniciar_scheduler as iniciar_expedicao_sync
            iniciar_expedicao_sync(app)
        except Exception:
            app.logger.exception("Falha ao iniciar scheduler Expedicao Sync")

    # Lembretes FOB: reenviar aviso de coleta a cada 2 dias enquanto o
    # romaneio nao estiver expedido.
    if app.config.get("ROMANEIO_FOB_REMINDER_ENABLED") and not app.config.get("TESTING"):
        try:
            from .services.romaneio_fob_reminder_scheduler import iniciar_scheduler as iniciar_romaneio_fob_reminder
            iniciar_romaneio_fob_reminder(app)
        except Exception:
            app.logger.exception("Falha ao iniciar scheduler Romaneio FOB")

    # Automacao de Solicitacoes Logisticas por frete CIF: Coleta (OC CIF) e
    # Entrega (Romaneio de saida CIF em Pronto/Expedido). Roda periodicamente
    # sem intervencao do usuario.
    if app.config.get("SOLICITACAO_CIF_AUTO_ENABLED") and not app.config.get("TESTING"):
        try:
            from .services.solicitacao_logistica_cif_scheduler import iniciar_scheduler as iniciar_solicitacao_cif
            iniciar_solicitacao_cif(app)
        except Exception:
            app.logger.exception("Falha ao iniciar scheduler Solicitacoes CIF")

    return app
