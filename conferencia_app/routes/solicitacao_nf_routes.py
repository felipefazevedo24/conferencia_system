"""Solicitacao de NF: formulario publico (sem login) para abrir o pedido de
garantia/bonificacao/teste/atendimento tecnico. A gestao interna
(separacao/faturamento/retorno) vive na aba "Faturamento avulso" dentro de
/expedicao/conferencia-cega (ver routes/expedicao_avulso_routes.py)."""

import hashlib
import hmac

from flask import Blueprint, current_app, jsonify, render_template, request, session

from ..auth import login_required
from ..models import ExpedicaoRomaneio
from ..services import solicitacao_nf_service as svc

solicitacao_nf_bp = Blueprint("solicitacao_nf", __name__)


def _token_romaneio_at(romaneio_id: int) -> str:
    """Token HMAC curto do link público (sem login) do romaneio gerado fora
    do horário comercial — mesmo padrão do link do motorista em viagem_routes.
    Sem coluna nova no banco: o token é derivado do id + SECRET_KEY."""
    secret = (current_app.config.get("SECRET_KEY") or "dev").encode("utf-8")
    msg = f"romaneio_at:{romaneio_id}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:16]


@solicitacao_nf_bp.route("/solicitacao-nf/romaneio/<int:romaneio_id>/<token>")
def page_romaneio_assistencia_tecnica(romaneio_id, token):
    """Link público (sem login) para o solicitante baixar/imprimir o romaneio
    de segurança gerado quando a solicitação caiu fora do horário comercial.
    Só funciona para romaneios de origem Assistência Técnica — o token não dá
    acesso a nenhum outro romaneio."""
    if not token or not hmac.compare_digest(token, _token_romaneio_at(romaneio_id)):
        return "Link inválido.", 404
    romaneio = ExpedicaoRomaneio.query.get(romaneio_id)
    if not romaneio or not romaneio.origem_assistencia_tecnica:
        return "Romaneio não encontrado.", 404
    from .expedicao_romaneio_routes import _render_romaneio_visualizar
    return _render_romaneio_visualizar(romaneio)


@solicitacao_nf_bp.route("/solicitacao-nf")
def page_solicitacao_nf_form():
    return render_template("solicitacao_nf_form.html",
                           tipos_operacao=svc.listar_tipos_operacao())


@solicitacao_nf_bp.route("/api/solicitacao-nf/funcionarios")
def api_solicitacao_nf_funcionarios():
    return jsonify({"sucesso": True, "funcionarios": svc.listar_funcionarios_para_solicitacao()})


@solicitacao_nf_bp.route("/api/solicitacao-nf/clientes")
def api_solicitacao_nf_clientes():
    termo = request.args.get("q", "")
    try:
        return jsonify({"sucesso": True, "clientes": svc.buscar_clientes(termo)})
    except Exception:
        current_app.logger.exception("Falha ao buscar clientes para solicitação de NF")
        return jsonify({"sucesso": False, "erro":
                        "Não foi possível consultar os clientes no ERP. Tente novamente."}), 503


@solicitacao_nf_bp.route("/api/solicitacao-nf/materiais")
def api_solicitacao_nf_materiais():
    termo = request.args.get("q", "")
    try:
        return jsonify({"sucesso": True, "materiais": svc.buscar_materiais(termo)})
    except Exception:
        current_app.logger.exception("Falha ao buscar materiais para solicitação de NF")
        return jsonify({"sucesso": False, "erro":
                        "Não foi possível consultar os materiais no ERP. Tente novamente."}), 503


@solicitacao_nf_bp.route("/api/solicitacao-nf/minhas")
@login_required
def api_solicitacao_nf_minhas():
    """Consulta das próprias solicitações: exige login.

    O código do funcionário vem da conta logada, nunca da URL — antes bastava
    escolher o código de outra pessoa para ver as solicitações dela."""
    funcionario = svc.resolver_funcionario_do_usuario(session.get("username"))
    if not funcionario:
        return jsonify({"sucesso": False, "precisa_vincular": True,
                        "erro": "Não identificamos qual funcionário é você. Escolha seu nome uma vez para vincular."}), 409
    solicitacoes = svc.listar_minhas_solicitacoes(str(funcionario.get("codigo") or ""))
    for s in solicitacoes:
        # Mesmo link público usado logo após criar a solicitação: funciona
        # aqui também, sem depender de o funcionário ter permissão de romaneio.
        if s.get("romaneio_id"):
            s["romaneio_url"] = (
                f"/solicitacao-nf/romaneio/{s['romaneio_id']}/"
                f"{_token_romaneio_at(s['romaneio_id'])}"
            )
    return jsonify({"sucesso": True, "funcionario": funcionario.get("nome"),
                    "solicitacoes": solicitacoes})


@solicitacao_nf_bp.route("/api/solicitacao-nf/vincular", methods=["POST"])
@login_required
def api_solicitacao_nf_vincular():
    dados = request.get_json(silent=True) or {}
    try:
        funcionario = svc.vincular_funcionario(session.get("username"), dados.get("codigo"))
    except svc.SolicitacaoNFError as exc:
        return jsonify({"sucesso": False, "erro": str(exc)}), 400
    return jsonify({"sucesso": True, "funcionario": funcionario.get("nome")})


@solicitacao_nf_bp.route("/api/solicitacao-nf", methods=["POST"])
def api_solicitacao_nf_criar():
    payload = request.get_json(silent=True) or {}
    # Honeypot: campo oculto que só um bot preencheria. Resposta 200 "de
    # sucesso" falsa, sem gravar nada, para não revelar a defesa.
    if str(payload.get("website") or "").strip():
        return jsonify({"sucesso": True, "protocolo": "SNF-000000"})
    try:
        solicitacao = svc.criar_solicitacao(payload, ip=request.remote_addr)
    except svc.SolicitacaoNFError as exc:
        return jsonify({"sucesso": False, "erro": str(exc)}), 400
    except Exception:
        from flask import current_app
        current_app.logger.exception("Falha ao criar solicitacao de NF")
        return jsonify({"sucesso": False, "erro": "Falha ao registrar solicitação. Tente novamente."}), 500

    resposta = {"sucesso": True, "protocolo": solicitacao.protocolo}
    # Presença de romaneio_id = solicitação caiu fora do horário comercial com
    # necessidade no mesmo dia: o material sai hoje sem NF, e o romaneio de
    # segurança já está pronto para download.
    if solicitacao.romaneio_id:
        resposta["romaneio_id"] = solicitacao.romaneio_id
        resposta["romaneio_url"] = (
            f"/solicitacao-nf/romaneio/{solicitacao.romaneio_id}/"
            f"{_token_romaneio_at(solicitacao.romaneio_id)}"
        )
        resposta["aviso"] = (
            "Fora do horário comercial: a nota fiscal só será emitida no "
            "próximo dia útil. A separação já foi registrada para hoje, e um "
            "romaneio de expedição foi gerado para acompanhar o material."
        )
    return jsonify(resposta)
