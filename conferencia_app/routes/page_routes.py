from flask import Blueprint, render_template, session

from ..auth import login_required, permission_required, roles_required


page_bp = Blueprint("pages", __name__)


@page_bp.route("/")
@login_required
def home():
    return render_template("menu_principal.html", user=session.get("username", "Usuário"))


@page_bp.route("/conferencia")
@permission_required("PAGE_CONFERENCIA")
def conferencia_page():
    return render_template("conferente.html", user=session["username"])


@page_bp.route("/portaria")
@permission_required("PAGE_PORTARIA")
def portaria_page():
    return render_template("portaria.html", user=session["username"])


@page_bp.route("/admin")
@permission_required("PAGE_ADMIN_DASHBOARD")
def dashboard():
    return render_template("dashboard.html", user=session["username"])


@page_bp.route("/upload")
@permission_required("PAGE_UPLOAD")
def upload_page():
    return render_template("admin.html", user=session["username"])


@page_bp.route("/compras/auditor-xml")
@permission_required("PAGE_XML_AUDITOR")
def compras_auditor_xml_page():
    return render_template("auditor_xml.html", user=session["username"])


@page_bp.route("/lancamento")
@permission_required("PAGE_LANCAMENTO")
def lancamento_page():
    return render_template("lancamento.html", user=session.get("username", "Fiscal"))


@page_bp.route("/fiscal/liberadas")
@permission_required("PAGE_FISCAL_LIBERADAS")
def fiscal_liberadas_page():
    return render_template("notas_liberadas.html", user=session.get("username", "Fiscal"))


@page_bp.route("/recebimento/etiquetas")
@permission_required("PAGE_ETIQUETAS")
def etiquetas_page():
    return render_template("etiquetas.html", user=session.get("username", "Operacao"))


@page_bp.route("/historico")
@permission_required("PAGE_ADMIN_HISTORICO")
def historico_page():
    return render_template("historico.html", user=session["username"])


@page_bp.route("/wms")
@permission_required("PAGE_WMS")
def wms_page():
    return render_template("wms.html", user=session["username"])


@page_bp.route("/financeiro/faturamento")
@permission_required("PAGE_FINANCEIRO_FATURAMENTO")
def financeiro_faturamento_page():
    return render_template("faturamento.html", user=session["username"])


@page_bp.route("/financeiro/contas-receber")
@permission_required("PAGE_FINANCEIRO_CONTAS_RECEBER")
def financeiro_contas_receber_page():
    return render_template("contas_receber.html", user=session["username"])


@page_bp.route("/admin/wms-enderecos")
@permission_required("PAGE_ADMIN_WMS_ENDERECOS")
def wms_enderecos_admin_page():
    return render_template("admin_wms_enderecos.html", user=session["username"])


@page_bp.route("/admin/wms-governanca")
@permission_required("PAGE_ADMIN_WMS_GOVERNANCA")
def wms_governanca_admin_page():
    return render_template("admin_wms_governanca.html", user=session["username"])


@page_bp.route("/expedicao/conferencia")
@permission_required("PAGE_EXPEDICAO_CONFERENCIA")
def expedicao_conferencia_page():
    return render_template("expedicao_conferencia.html", user=session["username"])


@page_bp.route("/expedicao/admin")
@permission_required("PAGE_EXPEDICAO_ADMIN")
def expedicao_admin_page():
    return render_template("expedicao_admin.html", user=session["username"])


@page_bp.route("/expedicao/romaneio")
@permission_required("PAGE_EXPEDICAO_ROMANEIO")
def expedicao_romaneio_page():
    return render_template("expedicao_romaneio.html", user=session["username"])


@page_bp.route("/admin/usuarios")
@permission_required("PAGE_ADMIN_USUARIOS")
def usuarios_page():
    return render_template("usuarios.html", user=session["username"])


@page_bp.route("/admin/acessos")
@permission_required("PAGE_ADMIN_ACESSOS")
def acessos_admin_page():
    return render_template("admin_acessos.html", user=session["username"])
