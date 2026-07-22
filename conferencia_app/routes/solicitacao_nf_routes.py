"""Solicitacao de NF: formulario publico (sem login) para abrir o pedido de
garantia/bonificacao/teste/atendimento tecnico. A gestao interna
(separacao/faturamento/retorno) vive na aba "Faturamento avulso" dentro de
/expedicao/conferencia-cega (ver routes/expedicao_avulso_routes.py)."""

from flask import Blueprint, jsonify, render_template, request

from ..services import solicitacao_nf_service as svc

solicitacao_nf_bp = Blueprint("solicitacao_nf", __name__)


@solicitacao_nf_bp.route("/solicitacao-nf")
def page_solicitacao_nf_form():
    return render_template("solicitacao_nf_form.html", tipos_operacao=svc.TIPOS_OPERACAO)


@solicitacao_nf_bp.route("/api/solicitacao-nf/funcionarios")
def api_solicitacao_nf_funcionarios():
    return jsonify({"sucesso": True, "funcionarios": svc.listar_funcionarios_para_solicitacao()})


@solicitacao_nf_bp.route("/api/solicitacao-nf/clientes")
def api_solicitacao_nf_clientes():
    termo = request.args.get("q", "")
    return jsonify({"sucesso": True, "clientes": svc.buscar_clientes(termo)})


@solicitacao_nf_bp.route("/api/solicitacao-nf/materiais")
def api_solicitacao_nf_materiais():
    termo = request.args.get("q", "")
    return jsonify({"sucesso": True, "materiais": svc.buscar_materiais(termo)})


@solicitacao_nf_bp.route("/api/solicitacao-nf/minhas")
def api_solicitacao_nf_minhas():
    codigo = request.args.get("codigo", "")
    return jsonify({"sucesso": True, "solicitacoes": svc.listar_minhas_solicitacoes(codigo)})


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
    return jsonify({"sucesso": True, "protocolo": solicitacao.protocolo})
