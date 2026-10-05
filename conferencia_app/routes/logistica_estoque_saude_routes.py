"""Saúde do estoque (Logística > Inventário). Regra e SQL em
services/estoque_saude_service.py."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import permission_required
from ..services import estoque_saude_service as svc

logistica_estoque_saude_bp = Blueprint("logistica_estoque_saude", __name__)

# Mesmo público do Cardex: quem cuida da valorização do estoque.
PERMISSION = "PAGE_LOGISTICA_CARDEX"


@logistica_estoque_saude_bp.route("/logistica/saude-estoque")
@permission_required(PERMISSION)
def pagina_saude_estoque():
    return render_template("logistica_saude_estoque.html", user=session["username"], checagens=svc.metadados())


@logistica_estoque_saude_bp.route("/api/logistica/saude-estoque/<chave>", methods=["GET"])
@permission_required(PERMISSION)
def api_checagem(chave: str):
    dados = svc.buscar_checagem(chave, forcar=request.args.get("forcar") == "1")
    if dados is None:
        return jsonify({"error": "Checagem não encontrada."}), 404
    return jsonify(dados)
