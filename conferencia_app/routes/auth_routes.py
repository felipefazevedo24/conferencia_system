from ..models import ActiveSession
from ..extensions import db
import uuid
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta
from flask import current_app
from sqlalchemy import func

from ..models import Usuario
from ..schemas.api_schemas import LoginSchema


auth_bp = Blueprint("auth", __name__)
login_schema = LoginSchema()
_login_attempts = {}


def _attempt_key(username: str, ip: str) -> str:
    return f"{username.lower()}::{ip}"


def _password_matches_and_upgrade(user: Usuario | None, password: str | None) -> bool:
    if not user:
        return False

    stored_password = str(user.password or "")
    password = str(password or "")
    if not stored_password or not password:
        return False

    try:
        if check_password_hash(stored_password, password):
            return True
    except (TypeError, ValueError):
        pass

    # Compatibilidade com bancos antigos que ainda tenham senha em texto puro.
    if stored_password == password:
        user.password = generate_password_hash(password)
        return True

    return False


@auth_bp.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "GET" and "username" in session:
        return redirect(url_for("pages.home"))

    if request.method == "POST":
        data = login_schema.load(request.json or {})
        username = (data.get("username") or "").strip().upper()
        password = data.get("password")
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "local")

        key = _attempt_key(username, ip)
        rec = _login_attempts.get(key)
        if rec and rec.get("blocked_until") and datetime.now() < rec["blocked_until"]:
            segundos_restantes = max(int((rec["blocked_until"] - datetime.now()).total_seconds()), 1)
            return jsonify({
                "sucesso": False,
                "code": "RATE_LIMIT",
                "msg": "Muitas tentativas. Aguarde alguns minutos antes de tentar novamente.",
                "retry_after_seconds": segundos_restantes,
            }), 429

        user = Usuario.query.filter_by(username=username).first()

        # Usuário sem senha definida — precisa se cadastrar
        if user and not user.password:
            return jsonify({
                "sucesso": False,
                "code": "FIRST_LOGIN",
                "msg": "Primeiro acesso detectado. Cadastre sua senha.",
                "email_hint": _mask_email(user.email),
            }), 403

        if _password_matches_and_upgrade(user, password):
            ultimo_acesso = (
                ActiveSession.query
                .filter(ActiveSession.username == user.username)
                .order_by(ActiveSession.last_activity.desc())
                .first()
            )
            session["username"] = user.username
            session["role"] = user.role
            session.permanent = True
            session["last_activity"] = datetime.now().isoformat()
            # Gerar um session_id único e registrar sessão ativa
            session_id = str(uuid.uuid4())
            session["session_id"] = session_id
            db.session.add(ActiveSession(
                username=user.username,
                session_id=session_id,
                created_at=datetime.now(),
                last_activity=datetime.now(),
                is_active=True
            ))
            db.session.commit()
            _login_attempts.pop(key, None)
            redirect_to = "/"
            return jsonify({
                "sucesso": True,
                "redirect_to": redirect_to,
                "user": {
                    "username": user.username,
                    "role": user.role,
                },
                "previous_access_at": ultimo_acesso.last_activity.isoformat() if ultimo_acesso and ultimo_acesso.last_activity else None,
            })

        max_attempts = current_app.config.get("LOGIN_MAX_ATTEMPTS", 5)
        lock_minutes = current_app.config.get("LOGIN_LOCK_MINUTES", 10)
        if not rec:
            rec = {"count": 0, "blocked_until": None}
        rec["count"] += 1
        if rec["count"] >= max_attempts:
            rec["blocked_until"] = datetime.now() + timedelta(minutes=lock_minutes)
            rec["count"] = 0
        _login_attempts[key] = rec

        tentativas_restantes = max_attempts - rec["count"] if not rec.get("blocked_until") else 0
        return jsonify({
            "sucesso": False,
            "code": "INVALID_CREDENTIALS",
            "msg": "Usuário ou senha incorretos.",
            "attempts_left": max(tentativas_restantes, 0),
        }), 401

    return render_template(
        "login.html",
        login_message=request.args.get("msg") or "",
        login_message_type=request.args.get("type") or "",
    )


def _mask_email(email):
    """Mask email for security: fe***@col***.com"""
    if not email or "@" not in email:
        return "***@***.com"
    local, domain = email.split("@", 1)
    parts = domain.rsplit(".", 1)
    masked_local = local[:2] + "***" if len(local) > 2 else local[0] + "***"
    masked_domain = parts[0][:3] + "***" if len(parts[0]) > 3 else parts[0]
    return f"{masked_local}@{masked_domain}.{parts[1]}" if len(parts) > 1 else f"{masked_local}@{masked_domain}"


@auth_bp.route("/cadastrar-senha", methods=["POST"])
def cadastrar_senha():
    """First-time password registration: user provides email + new password."""
    data = request.json or {}
    email = (data.get("email") or "").strip().lower()
    nova_senha = (data.get("senha") or "").strip()

    if not email or not nova_senha:
        return jsonify({"sucesso": False, "msg": "E-mail e senha são obrigatórios."}), 400
    if len(nova_senha) < 4:
        return jsonify({"sucesso": False, "msg": "A senha deve ter pelo menos 4 caracteres."}), 400

    user = Usuario.query.filter(func.lower(Usuario.email) == email).first()
    if not user:
        return jsonify({"sucesso": False, "msg": "E-mail não encontrado no sistema. Fale com um administrador."}), 404
    if user.password:
        return jsonify({"sucesso": False, "msg": "Este usuário já possui senha definida. Faça login normalmente ou peça a um admin para resetar."}), 409

    user.password = generate_password_hash(nova_senha)
    db.session.commit()
    return jsonify({"sucesso": True, "msg": "Senha cadastrada com sucesso! Faça login com seu usuário e a senha definida.", "username": user.username})


@auth_bp.route("/logout")
def logout():
    # Marcar sessão como inativa se existir
    session_id = session.get("session_id")
    if session_id:
        sessao = ActiveSession.query.filter_by(session_id=session_id, is_active=True).first()
        if sessao:
            sessao.is_active = False
            db.session.commit()
    session.clear()
    return redirect(url_for("auth.login_page", msg="Sessão encerrada com sucesso.", type="info"))


# Teste de logo estática
@auth_bp.route('/test-logo')
def test_logo():
    import os
    static_folder = os.path.join(current_app.root_path, '..', 'static')
    return send_from_directory(static_folder, 'columbia_logo.png')
