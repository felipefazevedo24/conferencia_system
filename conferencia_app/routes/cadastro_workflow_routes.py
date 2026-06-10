from datetime import datetime

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

from ..auth import has_permission, login_required, permission_required
from ..extensions import db
from ..models import CadastroWorkflowSLAConfig, CadastroWorkflowSolicitacao
from ..services.cadastro_workflow_service import (
    STATUS,
    TIPOS_CADASTRO,
    buscar_duplicidades,
    criar_solicitacao,
    executar_acao,
    get_dados,
    get_sla_horas,
    prazo_restante,
    relatorio_indicadores,
    tempo_na_etapa,
)


cadastro_workflow_bp = Blueprint("cadastro_workflow", __name__, url_prefix="/cadastros")


def _can_operar_compras() -> bool:
    return session.get("role") in {"Compras", "Admin"}


def _can_operar_fiscal() -> bool:
    return session.get("role") in {"Fiscal", "Admin"}


def _query_solicitacoes():
    role = session.get("role")
    username = session.get("username")
    query = CadastroWorkflowSolicitacao.query
    if role == "Compras":
        query = query.filter(CadastroWorkflowSolicitacao.departamento_atual.in_(["Compras", "Fiscal", "Solicitante", "Concluido", "Encerrado"]))
    elif role == "Fiscal":
        query = query.filter(CadastroWorkflowSolicitacao.departamento_atual.in_(["Fiscal", "Compras", "Concluido", "Encerrado"]))
    elif role != "Admin":
        query = query.filter(CadastroWorkflowSolicitacao.solicitante == username)

    tipo = request.args.get("tipo", "").strip()
    status = request.args.get("status", "").strip()
    numero = request.args.get("numero", "").strip()
    if tipo:
        query = query.filter(CadastroWorkflowSolicitacao.tipo == tipo)
    if status:
        query = query.filter(CadastroWorkflowSolicitacao.status == status)
    if numero:
        query = query.filter(CadastroWorkflowSolicitacao.numero.contains(numero.zfill(6) if numero.isdigit() else numero))
    return query.order_by(CadastroWorkflowSolicitacao.data_ultima_movimentacao.desc())


@cadastro_workflow_bp.get("/")
@permission_required("PAGE_CADASTRO_WORKFLOW")
def dashboard():
    solicitacoes = _query_solicitacoes().limit(250).all()
    indicadores = relatorio_indicadores() if session.get("role") == "Admin" else None
    return render_template(
        "cadastro_workflow_dashboard.html",
        user=session.get("username"),
        solicitacoes=solicitacoes,
        tipos=TIPOS_CADASTRO,
        statuses=STATUS,
        filtros=request.args,
        tempo_na_etapa=tempo_na_etapa,
        prazo_restante=prazo_restante,
        indicadores=indicadores,
        can_compras=_can_operar_compras(),
        can_fiscal=_can_operar_fiscal(),
    )


@cadastro_workflow_bp.route("/novo", methods=["GET", "POST"])
@permission_required("PAGE_CADASTRO_WORKFLOW")
def novo():
    tipo = request.values.get("tipo", "material")
    if tipo not in TIPOS_CADASTRO:
        tipo = "material"
    erro = None
    duplicidades = []
    dados = {}
    if request.method == "POST":
        tipo = request.form.get("tipo", tipo)
        meta = TIPOS_CADASTRO.get(tipo)
        if not meta:
            erro = "Tipo de cadastro invalido."
        else:
            dados = {campo: request.form.get(campo, "").strip() for campo, _label, _req in meta["fields"]}
            duplicidades = buscar_duplicidades(tipo, dados)
            try:
                sol = criar_solicitacao(
                    tipo=tipo,
                    dados=dados,
                    solicitante=session.get("username", "usuario"),
                    anexos=request.form.get("anexos", "").strip(),
                )
                return redirect(url_for("cadastro_workflow.detalhe", solicitacao_id=sol.id))
            except ValueError as exc:
                erro = str(exc)
    return render_template(
        "cadastro_workflow_form.html",
        user=session.get("username"),
        tipos=TIPOS_CADASTRO,
        tipo=tipo,
        erro=erro,
        dados=dados,
        duplicidades=duplicidades,
    )


@cadastro_workflow_bp.get("/<int:solicitacao_id>")
@permission_required("PAGE_CADASTRO_WORKFLOW")
def detalhe(solicitacao_id):
    sol = CadastroWorkflowSolicitacao.query.get_or_404(solicitacao_id)
    if session.get("role") not in {"Admin", "Compras", "Fiscal"} and sol.solicitante != session.get("username"):
        return render_template("acesso_negado.html", user=session.get("username")), 403
    return render_template(
        "cadastro_workflow_detalhe.html",
        user=session.get("username"),
        solicitacao=sol,
        dados=get_dados(sol),
        tipos=TIPOS_CADASTRO,
        tempo_na_etapa=tempo_na_etapa(sol),
        prazo=prazo_restante(sol),
        can_compras=_can_operar_compras(),
        can_fiscal=_can_operar_fiscal(),
        is_solicitante=sol.solicitante == session.get("username"),
    )


@cadastro_workflow_bp.post("/<int:solicitacao_id>/acao")
@permission_required("PAGE_CADASTRO_WORKFLOW")
def acao(solicitacao_id):
    sol = CadastroWorkflowSolicitacao.query.get_or_404(solicitacao_id)
    if session.get("role") not in {"Admin", "Compras", "Fiscal"} and sol.solicitante != session.get("username"):
        return render_template("acesso_negado.html", user=session.get("username")), 403
    acao_nome = request.form.get("acao", "")
    try:
        executar_acao(
            sol,
            acao=acao_nome,
            usuario=session.get("username", "usuario"),
            role=session.get("role", "Solicitante"),
            comentario=request.form.get("comentario", ""),
            form=request.form,
        )
    except ValueError as exc:
        return redirect(url_for("cadastro_workflow.detalhe", solicitacao_id=sol.id, erro=str(exc)))
    return redirect(url_for("cadastro_workflow.detalhe", solicitacao_id=sol.id))


@cadastro_workflow_bp.route("/admin/sla", methods=["GET", "POST"])
@permission_required("PAGE_CADASTRO_WORKFLOW")
def configurar_sla():
    if session.get("role") != "Admin":
        return render_template("acesso_negado.html", user=session.get("username")), 403
    if request.method == "POST":
        for depto in ("Compras", "Fiscal"):
            horas = max(1, int(request.form.get(f"sla_{depto}", 48) or 48))
            cfg = CadastroWorkflowSLAConfig.query.filter_by(departamento=depto).first()
            if not cfg:
                cfg = CadastroWorkflowSLAConfig(departamento=depto)
                db.session.add(cfg)
            cfg.horas = horas
            cfg.atualizado_por = session.get("username")
            cfg.atualizado_em = datetime.now()
        db.session.commit()
        return redirect(url_for("cadastro_workflow.configurar_sla"))
    return render_template(
        "cadastro_workflow_sla.html",
        user=session.get("username"),
        sla_compras=get_sla_horas("Compras"),
        sla_fiscal=get_sla_horas("Fiscal"),
    )


@cadastro_workflow_bp.get("/api/duplicidade")
@login_required
def api_duplicidade():
    if not has_permission("PAGE_CADASTRO_WORKFLOW"):
        return jsonify({"error": "Acesso negado"}), 403
    tipo = request.args.get("tipo", "")
    dados = {
        "documento": request.args.get("documento", ""),
        "codigo": request.args.get("codigo", ""),
        "descricao": request.args.get("descricao", ""),
    }
    return jsonify({"duplicidades": buscar_duplicidades(tipo, dados)})
