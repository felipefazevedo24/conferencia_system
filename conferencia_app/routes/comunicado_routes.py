"""Mural de avisos da tela inicial: publicar, editar e remover.
A leitura é a própria home (page_routes.home). Regra em
services/comunicado_service.py."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, session

from ..auth import permission_required
from ..services import comunicado_service as svc

comunicado_bp = Blueprint("comunicado", __name__)

PERM_PUBLICAR = "MANAGE_COMUNICADOS"


@comunicado_bp.route("/api/comunicados", methods=["POST"])
@permission_required(PERM_PUBLICAR)
def api_publicar():
    payload = request.get_json(silent=True) or {}
    try:
        aviso = svc.publicar(session["username"], payload.get("titulo"), payload.get("texto"), payload.get("expira_em"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(aviso)


@comunicado_bp.route("/api/comunicados/<int:comunicado_id>", methods=["POST"])
@permission_required(PERM_PUBLICAR)
def api_editar(comunicado_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        aviso = svc.editar(comunicado_id, session["username"],
                           payload.get("titulo"), payload.get("texto"), payload.get("expira_em"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if aviso is None:
        return jsonify({"error": "Aviso não encontrado."}), 404
    return jsonify(aviso)


@comunicado_bp.route("/api/comunicados/<int:comunicado_id>/remover", methods=["POST"])
@permission_required(PERM_PUBLICAR)
def api_remover(comunicado_id: int):
    if not svc.remover(comunicado_id, session["username"]):
        return jsonify({"error": "Aviso não encontrado."}), 404
    return jsonify({"ok": True})
