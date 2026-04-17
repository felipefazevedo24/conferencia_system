import datetime
import logging
from functools import wraps

from flask import abort, g, redirect, render_template, request, session, url_for
from sqlalchemy.exc import OperationalError

from .extensions import db
from .models import ActiveSession


logger = logging.getLogger(__name__)


def _recover_db_connection() -> None:
    try:
        db.session.rollback()
    except Exception:
        pass
    try:
        db.session.remove()
    except Exception:
        pass
    try:
        db.engine.dispose()
    except Exception:
        pass


def _load_and_touch_active_session(session_id: str) -> ActiveSession | None:
    sessao = ActiveSession.query.filter_by(session_id=session_id).first()
    if not sessao or not sessao.is_active:
        return sessao

    agora = datetime.datetime.now()
    ultimo_acesso = sessao.last_activity
    if not ultimo_acesso or (agora - ultimo_acesso).total_seconds() >= 60:
        sessao.last_activity = agora
        db.session.commit()

    return sessao

# Middleware para atualizar sessão ativa e forçar logout se necessário
def check_active_session():
    session_id = session.get("session_id")
    if not session_id:
        return

    try:
        sessao = _load_and_touch_active_session(session_id)
    except OperationalError:
        logger.warning("Falha ao consultar sessão ativa; tentando restabelecer conexão com o banco.", exc_info=True)
        _recover_db_connection()
        sessao = _load_and_touch_active_session(session_id)

    if not sessao or not sessao.is_active:
        session.clear()
        abort(401, description="Sessão expirada ou removida pelo administrador.")


PERMISSION_CATALOG = {
    "PAGE_CONFERENCIA": "Recebimento > Conferência cega",
    "PAGE_PORTARIA": "Recebimento > Inclusão XML (Portaria)",
    "PAGE_FISCAL_LIBERADAS": "Recebimento > NF-e liberadas",
    "PAGE_ETIQUETAS": "Recebimento > Etiquetas",
    "PAGE_UPLOAD": "Compras > Pre-nota de entrada",
    "PAGE_XML_AUDITOR": "Compras > Auditor XML",
    "PAGE_LANCAMENTO": "Compras > Documento de entrada",
    "PAGE_WMS": "WMS > Endereçamento e relatórios",
    "PAGE_FINANCEIRO_CONTAS_RECEBER": "Financeiro > Contas a Receber",
    "PAGE_EXPEDICAO_CONFERENCIA": "Expedição > Conferência",
    "PAGE_EXPEDICAO_ADMIN": "Expedição > Controle Admin",
    "PAGE_EXPEDICAO_ROMANEIO": "Expedição > Romaneios",
    "PAGE_ADMIN_DASHBOARD": "Administração > Painel de controle",
    "PAGE_ADMIN_USUARIOS": "Administração > Gestão de acessos",
    "PAGE_ADMIN_HISTORICO": "Administração > Logs e auditoria",
    "PAGE_ADMIN_ACESSOS": "Administração > Auditoria de acessos",
    "PAGE_ADMIN_WMS_ENDERECOS": "Administração > Cadastro de endereços WMS",
    "PAGE_ADMIN_WMS_GOVERNANCA": "Administração > Governança WMS",
    "PAGE_CONSERTO": "Conserto > Central de Conserto",
    "PAGE_LOGISTICA_AGENDAMENTO": "Logística > Gestão de Rotas",
    "PAGE_LOGISTICA_SOLICITACAO": "Logística > Solicitar Coleta/Entrega",
    "PAGE_LOGISTICA_MOTORISTA": "Logística > Painel do Motorista",
}


BASE_ROLE_PERMISSIONS = {
    "Admin": set(PERMISSION_CATALOG.keys()),
    "Fiscal": {
        "PAGE_CONFERENCIA",
        "PAGE_FISCAL_LIBERADAS",
        "PAGE_ETIQUETAS",
        "PAGE_XML_AUDITOR",
        "PAGE_LANCAMENTO",
        "PAGE_WMS",
        "PAGE_FINANCEIRO_CONTAS_RECEBER",
        "PAGE_EXPEDICAO_CONFERENCIA",
        "PAGE_EXPEDICAO_ROMANEIO",
    },
    "Financeiro": {
        "PAGE_FISCAL_LIBERADAS",
        "PAGE_LANCAMENTO",
        "PAGE_FINANCEIRO_CONTAS_RECEBER",
    },
    "Portaria": {
        "PAGE_PORTARIA",
        "PAGE_FISCAL_LIBERADAS",
    },
    "Motorista": {
        "PAGE_LOGISTICA_MOTORISTA",
    },
    "Compras": {
        "PAGE_UPLOAD",
        "PAGE_XML_AUDITOR",
        "PAGE_LANCAMENTO",
        "PAGE_FISCAL_LIBERADAS",
        "PAGE_LOGISTICA_SOLICITACAO",
    },
    "Conferente": {
        "PAGE_CONFERENCIA",
        "PAGE_FISCAL_LIBERADAS",
        "PAGE_ETIQUETAS",
        "PAGE_EXPEDICAO_CONFERENCIA",
        "PAGE_EXPEDICAO_ROMANEIO",
    },
    "Logística": {
        "PAGE_CONFERENCIA",
        "PAGE_FISCAL_LIBERADAS",
        "PAGE_ETIQUETAS",
        "PAGE_EXPEDICAO_CONFERENCIA",
        "PAGE_EXPEDICAO_ROMANEIO",
        "PAGE_LOGISTICA_AGENDAMENTO",
        "PAGE_LOGISTICA_SOLICITACAO",
    },
}


def get_permission_catalog() -> dict:
    return dict(PERMISSION_CATALOG)


def get_base_role_permissions(role: str) -> dict:
    role = (role or "").strip()
    allowed = BASE_ROLE_PERMISSIONS.get(role, set())
    return {key: key in allowed for key in PERMISSION_CATALOG.keys()}


def _resolve_permissions(username: str | None = None, role: str | None = None) -> dict:
    role = (role or session.get("role") or "").strip()
    username = (username or session.get("username") or "").strip()

    # Admin sempre tem acesso total para evitar lockout operacional.
    if role == "Admin":
        return {key: True for key in PERMISSION_CATALOG.keys()}

    effective = get_base_role_permissions(role)
    try:
        from .models import PermissaoAcesso

        for row in PermissaoAcesso.query.filter_by(scope_type="ROLE", scope_id=role).all():
            if row.permission_key in PERMISSION_CATALOG:
                effective[row.permission_key] = bool(row.allow)

        for row in PermissaoAcesso.query.filter_by(scope_type="USER", scope_id=username).all():
            if row.permission_key in PERMISSION_CATALOG:
                effective[row.permission_key] = bool(row.allow)
    except Exception:
        # Em caso de falha de banco/configuracao, cai para o baseline por role.
        return effective

    return effective


def get_effective_permissions(username: str | None = None, role: str | None = None) -> dict:
    cache_key = f"{username or session.get('username', '')}::{role or session.get('role', '')}"
    cached = getattr(g, "_perm_cache", {})
    if cache_key in cached:
        return cached[cache_key]

    resolved = _resolve_permissions(username=username, role=role)
    if not hasattr(g, "_perm_cache"):
        g._perm_cache = {}
    g._perm_cache[cache_key] = resolved
    return resolved


def has_permission(permission_key: str, username: str | None = None, role: str | None = None) -> bool:
    permission_key = (permission_key or "").strip()
    if not permission_key:
        return False
    perms = get_effective_permissions(username=username, role=role)
    return bool(perms.get(permission_key, False))


def _registrar_acesso_admin(path: str, method: str) -> None:
    if session.get("role") != "Admin":
        return
    if not (path.startswith("/admin") or path.startswith("/api/admin")):
        return

    db = None
    try:
        from .extensions import db
        from .models import LogAcessoAdministrativo

        db.session.add(
            LogAcessoAdministrativo(
                usuario=session.get("username", "admin"),
                rota=path,
                metodo=method,
            )
        )
        db.session.commit()
    except Exception:
        if db is not None:
            db.session.rollback()


def login_required(fn):
    @wraps(fn)
    def decorated_function(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("auth.login_page"))
        check_active_session()
        return fn(*args, **kwargs)

    return decorated_function


def roles_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        @login_required
        def decorated_view(*args, **kwargs):
            if session.get("role") not in roles:
                return render_template("acesso_negado.html", user=session.get("username")), 403

            _registrar_acesso_admin(path=request.path, method=request.method)

            return fn(*args, **kwargs)

        return decorated_view

    return wrapper


def permission_required(permission_key: str, *roles):
    def wrapper(fn):
        @wraps(fn)
        @login_required
        def decorated_view(*args, **kwargs):
            if roles and session.get("role") not in roles:
                return render_template("acesso_negado.html", user=session.get("username")), 403
            if not has_permission(permission_key):
                return render_template("acesso_negado.html", user=session.get("username")), 403

            _registrar_acesso_admin(path=request.path, method=request.method)
            return fn(*args, **kwargs)

        return decorated_view

    return wrapper
