"""Painel de Compras para as divergências da conferência física
(Compras > Divergências de recebimento). Regra de negócio em
services/ocorrencia_recebimento_service.py."""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import has_permission, is_admin_session, permission_required, permission_required_any
from ..services import ocorrencia_recebimento_service as svc

compras_divergencia_recebimento_bp = Blueprint("compras_divergencia_recebimento", __name__)

PERM_VER = "PAGE_COMPRAS_DIVERGENCIA_RECEBIMENTO"
PERM_TRATAR = "MANAGE_COMPRAS_DIVERGENCIA_RECEBIMENTO"
# A etapa do Fiscal (recusa / NF de devolução) é de quem lança a NF.
PERM_FISCAL = "PAGE_LANCAMENTO"
PERMS_ACESSO = (PERM_VER, PERM_TRATAR, PERM_FISCAL)


def _pode(chave: str) -> bool:
    return is_admin_session() or has_permission(chave)


@compras_divergencia_recebimento_bp.route("/compras/divergencias-recebimento")
@permission_required_any(*PERMS_ACESSO)
def painel_divergencias_recebimento():
    return render_template(
        "compras_divergencias_recebimento.html",
        user=session["username"],
        pode_tratar=_pode(PERM_TRATAR),
        pode_fiscal=_pode(PERM_FISCAL),
        status=svc.STATUS,
        status_escolha=svc.STATUS_ESCOLHA_COMPRAS,
        acoes=svc.ACOES,
        tipos_fiscal=svc.TIPOS_FISCAL,
    )


@compras_divergencia_recebimento_bp.route("/api/compras/divergencias-recebimento", methods=["GET"])
@permission_required_any(*PERMS_ACESSO)
def api_listar():
    return jsonify({
        "ocorrencias": svc.listar_ocorrencias(
            status=request.args.get("status") or "",
            busca=request.args.get("busca") or "",
            somente_abertas=request.args.get("abertas") == "1",
        ),
        "contagem": svc.contagem_por_status(),
    })


@compras_divergencia_recebimento_bp.route("/api/compras/divergencias-recebimento/<int:ocorrencia_id>", methods=["GET"])
@permission_required_any(*PERMS_ACESSO)
def api_obter(ocorrencia_id: int):
    dados = svc.obter_ocorrencia(ocorrencia_id)
    if dados is None:
        return jsonify({"error": "Ocorrência não encontrada."}), 404
    return jsonify(dados)


@compras_divergencia_recebimento_bp.route("/api/compras/divergencias-recebimento/<int:ocorrencia_id>/atualizar", methods=["POST"])
@permission_required(PERM_TRATAR)
def api_atualizar(ocorrencia_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        dados = svc.atualizar_ocorrencia(
            ocorrencia_id, session["username"],
            status=payload.get("status"), acao=payload.get("acao"),
            responsavel=payload.get("responsavel"), previsao=payload.get("previsao"),
            comentario=payload.get("comentario") or "",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if dados is None:
        return jsonify({"error": "Ocorrência não encontrada."}), 404
    return jsonify(dados)


@compras_divergencia_recebimento_bp.route("/api/compras/divergencias-recebimento/<int:ocorrencia_id>/estornar", methods=["POST"])
@permission_required_any(PERM_TRATAR, PERM_FISCAL)
def api_estornar(ocorrencia_id: int):
    atual = svc.obter_ocorrencia(ocorrencia_id)
    if atual is None:
        return jsonify({"error": "Ocorrência não encontrada."}), 404
    # Desfaz quem fechou: a etapa do Fiscal é do Fiscal; o resto, de Compras.
    if not _pode(PERM_FISCAL if atual["fiscal"] else PERM_TRATAR):
        return jsonify({"error": "Sem permissão para estornar esta resolução."}), 403
    payload = request.get_json(silent=True) or {}
    try:
        dados = svc.estornar_resolucao(ocorrencia_id, session["username"], payload.get("motivo") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(dados)


@compras_divergencia_recebimento_bp.route("/api/compras/divergencias-recebimento/<int:ocorrencia_id>/fiscal", methods=["POST"])
@permission_required(PERM_FISCAL)
def api_fiscal(ocorrencia_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        dados = svc.registrar_etapa_fiscal(
            ocorrencia_id, session["username"],
            tipo=payload.get("tipo") or "", numero_nf=payload.get("numero_nf") or "",
            data=payload.get("data") or "", observacao=payload.get("observacao") or "",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if dados is None:
        return jsonify({"error": "Ocorrência não encontrada."}), 404
    return jsonify(dados)
