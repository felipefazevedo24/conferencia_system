"""Rotas do modulo Intralog > Picking Almoxarifado.

Lista de material a separar, vinda AO VIVO da API do ERP, organizada como
LISTA PAI: cada OS Pai (servico_raiz) com TODAS as OS filhas que a
compoem e os materiais de cada uma. O almoxarifado confirma a separacao
material a material (guardado no banco do Sync - ver
intralog_picking_service)."""
from __future__ import annotations

import requests
from flask import Blueprint, current_app, jsonify, render_template, request, session

from ..auth import permission_required
from ..services import intralog_picking_service as svc

intralog_picking_bp = Blueprint("intralog_picking", __name__)

PERMISSION = "PAGE_INTRALOG_PICKING"


def _erro_api(exc: Exception):
    current_app.logger.exception("Falha ao consultar a API de material a separar")
    if isinstance(exc, requests.Timeout):
        return jsonify({"error": "A API do ERP demorou demais para responder. Tente novamente."}), 504
    if isinstance(exc, requests.RequestException):
        return jsonify({"error": f"Não foi possível falar com a API do ERP: {exc}"}), 502
    return jsonify({"error": f"Falha ao carregar a lista de separação: {exc}"}), 502


@intralog_picking_bp.route("/intralog/picking")
@permission_required(PERMISSION)
def picking_page():
    return render_template(
        "intralog_picking.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@intralog_picking_bp.route("/api/intralog/picking", methods=["GET"])
@permission_required(PERMISSION)
def api_listar_picking():
    try:
        dados = svc.montar_arvore(
            busca=request.args.get("busca") or "",
            status=request.args.get("status") or "",
            familia=request.args.get("familia") or "",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # rede/ERP fora do ar
        return _erro_api(exc)
    return jsonify(dados)


@intralog_picking_bp.route("/api/intralog/picking/separar", methods=["POST"])
@permission_required(PERMISSION)
def api_confirmar_separacao():
    payload = request.get_json(silent=True) or {}
    try:
        svc.confirmar_separacao(
            payload.get("cod_os_completo"),
            payload.get("cod_interno"),
            session.get("username", "desconhecido"),
            servico_raiz=payload.get("servico_raiz") or "",
            qtde=payload.get("qtde"),
            unidade=payload.get("unidade") or "",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"sucesso": True, "message": "Material separado."})


@intralog_picking_bp.route("/api/intralog/picking/estornar", methods=["POST"])
@permission_required(PERMISSION)
def api_estornar_separacao():
    payload = request.get_json(silent=True) or {}
    try:
        svc.estornar_separacao(payload.get("cod_os_completo"), payload.get("cod_interno"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"sucesso": True, "message": "Separação estornada."})


@intralog_picking_bp.route("/api/intralog/picking/observacao", methods=["POST"])
@permission_required(PERMISSION)
def api_salvar_observacao():
    payload = request.get_json(silent=True) or {}
    try:
        svc.salvar_observacao(
            payload.get("cod_os_completo"), payload.get("cod_interno"), payload.get("observacao")
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"sucesso": True, "message": "Observação salva."})
