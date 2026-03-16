from flask import Blueprint, render_template, session

from ..auth import login_required, roles_required


page_bp = Blueprint("pages", __name__)


@page_bp.route("/")
@roles_required("Conferente", "Fiscal", "Admin")
def home():
    return render_template("conferente.html", user=session["username"])


@page_bp.route("/portaria")
@roles_required("Portaria", "Admin")
def portaria_page():
    return render_template("portaria.html", user=session["username"])


@page_bp.route("/admin")
@roles_required("Admin")
def dashboard():
    return render_template("dashboard.html", user=session["username"])


@page_bp.route("/upload")
@roles_required("Admin")
def upload_page():
    return render_template("admin.html", user=session["username"])


@page_bp.route("/lancamento")
@roles_required("Fiscal", "Admin")
def lancamento_page():
    return render_template("lancamento.html", user=session.get("username", "Fiscal"))


@page_bp.route("/fiscal/liberadas")
@login_required
def fiscal_liberadas_page():
    return render_template("notas_liberadas.html", user=session.get("username", "Fiscal"))


@page_bp.route("/historico")
@roles_required("Admin")
def historico_page():
    return render_template("historico.html", user=session["username"])


@page_bp.route("/expedicao/conferencia")
@roles_required("Conferente", "Admin", "Fiscal")
def expedicao_conferencia_page():
    return render_template("expedicao_conferencia.html", user=session["username"])


@page_bp.route("/expedicao/admin")
@roles_required("Admin")
def expedicao_admin_page():
    return render_template("expedicao_admin.html", user=session["username"])


@page_bp.route("/expedicao/romaneio")
@roles_required("Conferente", "Admin", "Fiscal")
def expedicao_romaneio_page():
    return render_template("expedicao_romaneio.html", user=session["username"])


@page_bp.route("/admin/usuarios")
@roles_required("Admin")
def usuarios_page():
    return render_template("usuarios.html", user=session["username"])


@page_bp.route("/admin/acessos")
@roles_required("Admin")
def acessos_admin_page():
    return render_template("admin_acessos.html", user=session["username"])
