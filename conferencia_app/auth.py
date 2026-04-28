from .models import ActiveSession
from .extensions import db
from flask import abort
import datetime
# Middleware para atualizar sessão ativa e forçar logout se necessário
def check_active_session():
    session_id = session.get("session_id")
    if not session_id:
        return
    sessao = ActiveSession.query.filter_by(session_id=session_id).first()
    if not sessao or not sessao.is_active:
        session.clear()
        abort(401, description="Sessão expirada ou removida pelo administrador.")
    # Atualiza last_activity
    sessao.last_activity = datetime.datetime.now()
    db.session.commit()

from functools import wraps

from flask import g, jsonify, redirect, render_template, request, session, url_for


PERMISSION_CATALOG = {
    "PAGE_CONFERENCIA": "Recebimento > Conferencia cega",
    "PAGE_PORTARIA": "Recebimento > Inclusao XML (Portaria)",
    "PAGE_FISCAL_LIBERADAS": "Recebimento > NF-e liberadas",
    "PAGE_ETIQUETAS": "Recebimento > Etiquetas",
    "PAGE_UPLOAD": "Compras > Pre-nota de entrada",
    "PAGE_XML_AUDITOR": "Compras > Auditor XML",
    "PAGE_LANCAMENTO": "Compras > Documento de entrada",
    "PAGE_WMS": "WMS > Enderecamento e relatorios",
    "PAGE_FINANCEIRO_CONTAS_RECEBER": "Financeiro > Contas a Receber",
    "PAGE_EXPEDICAO_CONFERENCIA": "Expedicao > Conferencia",
    "PAGE_EXPEDICAO_ADMIN": "Expedicao > Controle Admin",
    "PAGE_EXPEDICAO_ROMANEIO": "Expedicao > Romaneios",
    "PAGE_ADMIN_DASHBOARD": "Administração > Painel de controle",
    "PAGE_ADMIN_USUARIOS": "Administração > Gestão de acessos",
    "PAGE_ADMIN_HISTORICO": "Administração > Logs e auditoria",
    "PAGE_ADMIN_ACESSOS": "Administração > Auditoria de acessos",
    "PAGE_ADMIN_WMS_endereços": "Administração > Cadastro de endereços WMS",
    "PAGE_ADMIN_WMS_GOVERNANCA": "Administração > Governanca WMS",
    "PAGE_CONSERTO": "Conserto > Central de Conserto",
    "PAGE_LOGISTICA_AGENDAMENTO": "Logistica > Gestao de Rotas",
    "PAGE_LOGISTICA_SOLICITACAO": "Logistica > Solicitar Coleta/Entrega",
    "PAGE_LOGISTICA_MOTORISTA": "Logistica > Painel do Motorista",  # motorista
    "PAGE_LOGISTICA_RASTREAMENTO": "Logistica > Rastreamento de Veiculos",
    "PAGE_LOGISTICA_FROTA": "Logistica > Gestao de Frota",
    "PAGE_LOGISTICA_VIAGEM": "Logistica > Gestao de Viagens",
    "PAGE_FACILITIES_ADMIN": "Facilities > Painel de Gestao (Admin)",
}


BASE_ROLE_PERMISSIONS = {
    "Admin": set(PERMISSION_CATALOG.keys()),
    "Fiscal": {
        "PAGE_CONFERENCIA",
        "PAGE_FISCAL_LIBERADAS",
        "PAGE_XML_AUDITOR",
        "PAGE_LANCAMENTO",
        "PAGE_FINANCEIRO_CONTAS_RECEBER",
        "PAGE_EXPEDICAO_CONFERENCIA",
    },
    "Financeiro": {
        "PAGE_FISCAL_LIBERADAS",
        "PAGE_LANCAMENTO",
        "PAGE_FINANCEIRO_CONTAS_RECEBER",
    },
    "Logística": {
        "PAGE_CONFERENCIA",
        "PAGE_FISCAL_LIBERADAS",
        "PAGE_EXPEDICAO_CONFERENCIA",
        "PAGE_EXPEDICAO_ROMANEIO",
        "PAGE_LOGISTICA_AGENDAMENTO",
        "PAGE_LOGISTICA_SOLICITACAO",
        "PAGE_LOGISTICA_RASTREAMENTO",
        "PAGE_LOGISTICA_FROTA",
        "PAGE_LOGISTICA_VIAGEM",
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
        "PAGE_FISCAL_LIBERADAS",
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
            if request.path.startswith("/api") or request.path == "/validar":
                return jsonify({"error": "Login necessário"}), 401
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
                if request.path.startswith("/api") or request.path == "/validar":
                    return jsonify({"error": "Acesso negado", "msg": f"Requer role: {roles}"}), 403
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
            is_api = request.path.startswith("/api") or request.path == "/validar"
            if roles and session.get("role") not in roles:
                if is_api:
                    return jsonify({"error": "Acesso negado"}), 403
                return render_template("acesso_negado.html", user=session.get("username")), 403
            if not has_permission(permission_key):
                if is_api:
                    return jsonify({"error": "Acesso negado", "msg": f"Permissão necessária: {permission_key}"}), 403
                return render_template("acesso_negado.html", user=session.get("username")), 403

            _registrar_acesso_admin(path=request.path, method=request.method)
            return fn(*args, **kwargs)

        return decorated_view

    return wrapper
