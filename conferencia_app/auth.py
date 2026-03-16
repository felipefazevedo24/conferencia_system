from functools import wraps

from flask import redirect, render_template, request, session, url_for


def login_required(fn):
    @wraps(fn)
    def decorated_function(*args, **kwargs):
        if "username" not in session:
            return redirect(url_for("auth.login_page"))
        return fn(*args, **kwargs)

    return decorated_function


def roles_required(*roles):
    def wrapper(fn):
        @wraps(fn)
        @login_required
        def decorated_view(*args, **kwargs):
            if session.get("role") not in roles:
                return render_template("acesso_negado.html", user=session.get("username")), 403

            role = session.get("role")
            path = request.path
            if role == "Admin" and (path.startswith("/admin") or path.startswith("/api/admin")):
                db = None
                try:
                    from .extensions import db
                    from .models import LogAcessoAdministrativo

                    db.session.add(
                        LogAcessoAdministrativo(
                            usuario=session.get("username", "admin"),
                            rota=path,
                            metodo=request.method,
                        )
                    )
                    db.session.commit()
                except Exception:
                    if db is not None:
                        db.session.rollback()

            return fn(*args, **kwargs)

        return decorated_view

    return wrapper
