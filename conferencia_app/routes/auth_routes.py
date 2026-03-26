from ..models import ActiveSession
from ..extensions import db
import uuid
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for, send_from_directory
from werkzeug.security import check_password_hash
from datetime import datetime, timedelta
from flask import current_app

from ..models import Usuario
from ..schemas.api_schemas import LoginSchema


auth_bp = Blueprint("auth", __name__)
login_schema = LoginSchema()
_login_attempts = {}


def _attempt_key(username: str, ip: str) -> str:
    return f"{username.lower()}::{ip}"


@auth_bp.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        data = login_schema.load(request.json or {})
        username = data.get("username")
        password = data.get("password")
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "local")

        key = _attempt_key(username, ip)
        rec = _login_attempts.get(key)
        if rec and rec.get("blocked_until") and datetime.now() < rec["blocked_until"]:
            return jsonify({"sucesso": False, "msg": "Muitas tentativas. Tente novamente em alguns minutos."}), 429

        user = Usuario.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
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
            return jsonify({"sucesso": True, "redirect_to": redirect_to})

        max_attempts = current_app.config.get("LOGIN_MAX_ATTEMPTS", 5)
        lock_minutes = current_app.config.get("LOGIN_LOCK_MINUTES", 10)
        if not rec:
            rec = {"count": 0, "blocked_until": None}
        rec["count"] += 1
        if rec["count"] >= max_attempts:
            rec["blocked_until"] = datetime.now() + timedelta(minutes=lock_minutes)
            rec["count"] = 0
        _login_attempts[key] = rec

        return jsonify({"sucesso": False, "msg": "Usuário ou senha incorretos"}), 401

    return render_template("login.html")


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
    return redirect(url_for("auth.login_page"))


# Teste de logo estática
@auth_bp.route('/test-logo')
def test_logo():
    import os
    static_folder = os.path.join(current_app.root_path, '..', 'static')
    return send_from_directory(static_folder, 'columbia_logo.png')
