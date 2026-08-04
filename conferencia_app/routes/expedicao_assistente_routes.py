"""Rotas do Assistente de Expedição (inteligência operacional offline).

Expõe o serviço expedicao_assistente_service para a página de Conferência de
Expedição: panorama de pendências priorizadas e chat por intenção (offline).
"""
from flask import Blueprint, jsonify, request, session

from ..auth import permission_required, is_admin_session
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


@expedicao_assistente_bp.route("/api/expedicao/assistente/aprender", methods=["POST"])
@permission_required(PERMISSION)
def aprender():
    """Ensina um fato novo à Bia (base de conhecimento). Só administradores."""
    if not is_admin_session():
        return jsonify({"error": "Apenas administradores podem ensinar a Bia."}), 403
    payload = request.get_json(silent=True) or {}
    texto = str(payload.get("texto") or "").strip()
    if not texto:
        return jsonify({"error": "Informe o que a Bia deve aprender."}), 400
    autor = str(session.get("username") or session.get("role") or "").strip()
    if svc.registrar_aprendizado(texto, autor=autor):
        return jsonify({"ok": True, "mensagem": "Aprendizado registrado."})
    return jsonify({"error": "Não foi possível salvar o aprendizado."}), 500
