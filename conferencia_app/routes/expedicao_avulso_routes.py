"""Rotas de dados das Solicitacoes de NF (modulo Assistencia Tecnica).

A solicitacao (garantia/bonificacao/teste/conserto/atendimento tecnico) e'
aberta no formulario publico em /solicitacao-nf e gerida em
/assistencia-tecnica. Separacao: Logistica/Comex/Fiscal/Admin. Faturamento e
registro de retorno: somente Fiscal/Admin — quem emite a nota e' o Fiscal.

As URLs continuam com o prefixo antigo (conf-cega-avulso) porque a tela nasceu
como aba da Conferencia de Expedicao; renomea-las nao muda comportamento e so'
criaria risco de deixar alguma chamada para tras."""

from flask import Blueprint, jsonify, request, session

from ..auth import permission_required_any, roles_required
from ..services import solicitacao_nf_service as svc

expedicao_avulso_bp = Blueprint("expedicao_avulso", __name__)

# A tela vive no modulo Assistencia Tecnica. A permissao antiga continua
# valendo para nao tirar o acesso de quem ja' tinha enquanto o cargo novo nao
# e' revisado: o modulo nao pode depender da permissao de outro modulo.
PERMISSOES = ("PAGE_ASSISTENCIA_TECNICA", "PAGE_EXPEDICAO_CONF_CEGA")


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/ordens")
@permission_required_any(*PERMISSOES)
def listar_ordens_avulso():
    return jsonify({"sucesso": True, **svc.listar_ordens_avulso()})


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/ordens/<int:solicitacao_id>/separar", methods=["POST"])
@roles_required("Logística", "Comex", "Fiscal", "Admin")
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


@expedicao_avulso_bp.route(
    "/api/expedicao/conf-cega-avulso/ordens/<int:solicitacao_id>/itens/<int:item_id>",
    methods=["PATCH"])
@roles_required("Logística", "Comex", "Fiscal", "Admin")
def alterar_item_avulso(solicitacao_id, item_id):
    """Corrige a operação/retorno/prazo de um item que ainda não tem nota."""
    payload = request.get_json(silent=True) or {}
    try:
        solicitacao = svc.alterar_item(
            solicitacao_id, item_id,
            usuario=session.get("username", ""),
            tipo_operacao=payload.get("tipo_operacao"),
            necessita_retorno=payload.get("necessita_retorno"),
            data_prevista_retorno=payload.get("data_prevista_retorno"),
        )
    except svc.SolicitacaoNFError as exc:
        return jsonify({"sucesso": False, "erro": str(exc)}), 400
    return jsonify({"sucesso": True, "ordem": svc._serializar(solicitacao)})


@expedicao_avulso_bp.route("/api/expedicao/conf-cega-avulso/tipos-operacao")
@permission_required_any(*PERMISSOES)
def tipos_operacao_avulso():
    return jsonify({"sucesso": True, "tipos": svc.listar_tipos_operacao()})


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
            # Sem itens, fatura tudo que falta; com itens, só aquele grupo —
            # é assim que uma solicitação vira mais de uma nota.
            item_ids=payload.get("itens"),
            # Prazo de retorno: quem informa é o Fiscal, junto com a nota.
            data_prevista_retorno=payload.get("data_prevista_retorno"),
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
            # {item_id: quantidade} permite devolucao parcial.
            retornos={int(k): v for k, v in (payload.get("retornos") or {}).items()} or None,
            # Itens que fecham mesmo tendo voltado menos do que saiu.
            encerrar=payload.get("encerrar"),
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
