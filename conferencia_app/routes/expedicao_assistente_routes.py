"""Rotas do Assistente de Expedição (inteligência operacional offline).

Expõe o serviço expedicao_assistente_service para a página de Conferência de
Expedição: panorama de pendências priorizadas e chat por intenção (offline).
"""
from flask import Blueprint, jsonify, request

from ..auth import permission_required
from ..services import expedicao_assistente_service as svc


expedicao_assistente_bp = Blueprint("expedicao_assistente", __name__)

PERMISSION = "PAGE_EXPEDICAO_CONF_CEGA"


@expedicao_assistente_bp.route("/api/expedicao/assistente/pendencias", methods=["GET"])
@permission_required(PERMISSION)
def pendencias():
    """Panorama priorizado das pendências de expedição."""
    return jsonify(svc.analisar())


@expedicao_assistente_bp.route("/api/expedicao/assistente/perguntar", methods=["POST"])
@permission_required(PERMISSION)
def perguntar():
    """Responde a uma pergunta em linguagem natural (offline, por intenção)."""
    payload = request.get_json(silent=True) or {}
    pergunta = str(payload.get("pergunta") or "").strip()
    if not pergunta:
        return jsonify({"error": "Informe uma pergunta."}), 400
    return jsonify(svc.responder(pergunta))
