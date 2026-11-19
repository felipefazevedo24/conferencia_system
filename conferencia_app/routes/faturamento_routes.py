from flask import Blueprint, jsonify, render_template, request, session

from ..auth import permission_required
from ..services import faturamento_service as faturamento


faturamento_bp = Blueprint("faturamento", __name__)
PERM = "PAGE_FATURAMENTO"


@faturamento_bp.route("/faturamento")
@permission_required(PERM)
def faturamento_page():
    return render_template(
        "faturamento.html",
        user=session.get("username", "Faturamento"),
        user_role=session.get("role", ""),
    )


@faturamento_bp.route("/api/faturamento/painel")
@permission_required(PERM)
def faturamento_painel():
    return jsonify(faturamento.painel(de=request.args.get("de"), ate=request.args.get("ate")))


@faturamento_bp.route("/api/faturamento/status")
@permission_required(PERM)
def faturamento_status():
    return jsonify(faturamento.status())


@faturamento_bp.route("/api/faturamento/atualizar", methods=["POST"])
@permission_required(PERM)
def faturamento_atualizar():
    return jsonify(faturamento.iniciar_atualizacao(session.get("username", "desconhecido")))
