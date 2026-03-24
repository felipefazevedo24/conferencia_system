from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
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
    session.clear()
    return redirect(url_for("auth.login_page"))
