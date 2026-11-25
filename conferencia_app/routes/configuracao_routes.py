"""Administração > Configuração: cadastros que alimentam vários módulos
(hoje, os departamentos). Regra em services/departamento_service.py."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import permission_required
from ..services import departamento_service as dep_svc

configuracao_bp = Blueprint("configuracao", __name__)

PERM = "PAGE_ADMIN_CONFIGURACAO"


@configuracao_bp.route("/admin/configuracao")
@permission_required(PERM)
def configuracao_page():
    return render_template("admin_configuracao.html", user=session["username"])


@configuracao_bp.route("/api/admin/departamentos")
@permission_required(PERM)
def api_listar_departamentos():
    return jsonify({"departamentos": dep_svc.listar()})


@configuracao_bp.route("/api/admin/departamentos", methods=["POST"])
@permission_required(PERM)
def api_criar_departamento():
    payload = request.get_json(silent=True) or {}
    try:
        dep_svc.criar(payload.get("nome"), session["username"])
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400
    return jsonify({"departamentos": dep_svc.listar()})


@configuracao_bp.route("/api/admin/departamentos/<int:dep_id>", methods=["PATCH"])
@permission_required(PERM)
def api_ativar_departamento(dep_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        dep = dep_svc.definir_ativo(dep_id, bool(payload.get("ativo")))
    except ValueError as exc:
        return jsonify({"msg": str(exc)}), 400
    if not dep:
        return jsonify({"msg": "Departamento não encontrado."}), 404
    return jsonify({"departamentos": dep_svc.listar()})
