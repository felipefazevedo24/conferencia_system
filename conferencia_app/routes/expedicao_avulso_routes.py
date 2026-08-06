"""Rotas da Conferencia de Expedicao - aba Faturamento avulso.

Espelha a mesma pagina (/expedicao/conferencia-cega, mesma permissao
PAGE_EXPEDICAO_CONF_CEGA) das abas fat/st, mas usando o modelo de
Solicitacao de NF (garantia/bonificacao/teste/atendimento tecnico) aberto
pelo formulario publico em /solicitacao-nf. Separacao: Logistica/Fiscal/
Admin. Faturamento e registro de retorno: somente Fiscal/Admin."""

from flask import Blueprint, jsonify, request, session

from ..auth import permission_required, roles_required
from ..services import solicitacao_nf_service as svc

expedicao_avulso_bp = Blueprint("expedicao_avulso", __name__)

PERMISSION = "PAGE_EXPEDICAO_CONF_CEGA"


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/ordens")
@permission_required(PERMISSION)
def listar_ordens_avulso():
    return jsonify({"sucesso": True, **svc.listar_ordens_avulso()})


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/ordens/<int:solicitacao_id>/separar", methods=["POST"])
@roles_required("Logística", "Fiscal", "Admin")
def separar_ordem_avulso(solicitacao_id):
    payload = request.get_json(silent=True) or {}
    try:
        solicitacao = svc.marcar_separada(
            solicitacao_id,
            usuario=session.get("username", ""),
            itens_separados=payload.get("itens_separados") or [],
            observacao=payload.get("observacao"),
        )
    except svc.SolicitacaoNFError as exc:
        return jsonify({"sucesso": False, "erro": str(exc)}), 400
    return jsonify({"sucesso": True, "ordem": svc._serializar(solicitacao)})


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/ordens/<int:solicitacao_id>/faturar", methods=["POST"])
@roles_required("Fiscal", "Admin")
def faturar_ordem_avulso(solicitacao_id):
    payload = request.get_json(silent=True) or {}
    try:
        solicitacao = svc.marcar_faturada(
            solicitacao_id,
            usuario=session.get("username", ""),
            numero_nf=payload.get("numero_nf"),
            observacao=payload.get("observacao"),
        )
    except svc.SolicitacaoNFError as exc:
        return jsonify({"sucesso": False, "erro": str(exc)}), 400
    return jsonify({"sucesso": True, "ordem": svc._serializar(solicitacao)})


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/ordens/<int:solicitacao_id>/vincular-of", methods=["POST"])
@roles_required("Fiscal", "Admin")
def vincular_of_avulso(solicitacao_id):
    payload = request.get_json(silent=True) or {}
    try:
        solicitacao = svc.vincular_ordem_faturamento(
            solicitacao_id,
            usuario=session.get("username", ""),
            cod_ordem_fat=payload.get("cod_ordem_fat"),
        )
    except svc.SolicitacaoNFError as exc:
        return jsonify({"sucesso": False, "erro": str(exc)}), 400
    return jsonify({"sucesso": True, "ordem": svc._serializar(solicitacao)})


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/ordens/<int:solicitacao_id>/retorno", methods=["POST"])
@roles_required("Fiscal", "Admin")
def registrar_retorno_avulso(solicitacao_id):
    payload = request.get_json(silent=True) or {}
    try:
        solicitacao = svc.registrar_retorno(
            solicitacao_id,
            usuario=session.get("username", ""),
            numero_nf_retorno=payload.get("numero_nf_retorno"),
            observacao=payload.get("observacao"),
        )
    except svc.SolicitacaoNFError as exc:
        return jsonify({"sucesso": False, "erro": str(exc)}), 400
    return jsonify({"sucesso": True, "ordem": svc._serializar(solicitacao)})


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/ordens/<int:solicitacao_id>/estornar", methods=["POST"])
@roles_required("Admin")
def estornar_ordem_avulso(solicitacao_id):
    payload = request.get_json(silent=True) or {}
    try:
        solicitacao = svc.estornar_solicitacao(
            solicitacao_id,
            usuario=session.get("username", ""),
            motivo=payload.get("motivo"),
        )
    except svc.SolicitacaoNFError as exc:
        return jsonify({"sucesso": False, "erro": str(exc)}), 400
    return jsonify({"sucesso": True, "ordem": svc._serializar(solicitacao)})


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/ordens/<int:solicitacao_id>", methods=["DELETE"])
@roles_required("Admin")
def excluir_ordem_avulso(solicitacao_id):
    payload = request.get_json(silent=True) or {}
    try:
        svc.excluir_solicitacao(
            solicitacao_id,
            usuario=session.get("username", ""),
            motivo=payload.get("motivo"),
        )
    except svc.SolicitacaoNFError as exc:
        return jsonify({"sucesso": False, "erro": str(exc)}), 400
    return jsonify({"sucesso": True})
