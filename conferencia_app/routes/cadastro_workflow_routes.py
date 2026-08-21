import json
import os
from datetime import datetime

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from sqlalchemy import or_
from werkzeug.utils import secure_filename

from ..auth import has_permission, login_required, permission_required
from ..extensions import db
from ..models import CadastroWorkflowSLAConfig, CadastroWorkflowSolicitacao, PlanoContaDominio
from ..services.cadastro_workflow_service import (
    CAMPO_OPCOES,
    CATEGORIAS_VISIVEIS,
    MATERIAL_UTILIZACAO_SUGESTOES,
    MOTIVOS_PADRAO_POR_ACAO,
    STATUS,
    TIPOS_CADASTRO,
    buscar_duplicidades,
    buscar_materiais_grv,
    categoria_visivel,
    consultar_cartao_cnpj,
    criar_solicitacao,
    executar_acao,
    get_dados,
    get_sla_horas,
    montar_comentario_acao,
    motivos_padrao_por_acao,
    prazo_restante,
    role_contabil,
    sugerir_plano_contas_material,
    tempo_na_etapa,
)


cadastro_workflow_bp = Blueprint("cadastro_workflow", __name__, url_prefix="/cadastros")
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_ANEXO_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".jpg", ".jpeg", ".png", ".webp"}


def _can_operar_compras() -> bool:
    return session.get("role") in {"Compras", "Admin"}


def _can_operar_contabil() -> bool:
    return role_contabil(session.get("role")) or session.get("role") == "Admin"


def _can_operar_fiscal() -> bool:
    return session.get("role") in {"Fiscal", "Admin"}


def _parse_anexos(anexos: str | None) -> dict:
    raw = (anexos or "").strip()
    if not raw:
        return {"observacoes": "", "imagem": None, "arquivo": None}
    try:
        dados = json.loads(raw)
        if isinstance(dados, dict):
            dados.setdefault("observacoes", "")
            dados.setdefault("imagem", None)
            dados.setdefault("arquivo", None)
            return dados
    except json.JSONDecodeError:
        pass
    return {"observacoes": raw, "imagem": None, "arquivo": None}


def _salvar_imagem_produto():
    arquivo = request.files.get("imagem_produto")
    if not arquivo or not getattr(arquivo, "filename", ""):
        return None
    nome_original = secure_filename(arquivo.filename or "produto.jpg")
    extensao = os.path.splitext(nome_original)[1].lower()
    if extensao not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Envie a imagem do produto em JPG, PNG ou WEBP.")
    pasta = os.path.join(current_app.instance_path, "cadastro_workflow")
    os.makedirs(pasta, exist_ok=True)
    nome_final = secure_filename(f"material_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{extensao}")
    arquivo.save(os.path.join(pasta, nome_final))
    return {
        "nome": nome_original,
        "arquivo": nome_final,
        "url": url_for("cadastro_workflow.anexo_imagem", filename=nome_final),
    }


def _salvar_arquivo_anexo(campo: str = "arquivo_anexo"):
    """Anexo generico (PDF/DOC/XLS/imagem) - diferente da 'Imagem do
    produto', que e so uma foto de referencia. Mesmo padrao de
    _salvar_imagem_produto, so com tipos de arquivo mais amplos."""
    arquivo = request.files.get(campo)
    if not arquivo or not getattr(arquivo, "filename", ""):
        return None
    nome_original = secure_filename(arquivo.filename or "anexo")
    extensao = os.path.splitext(nome_original)[1].lower()
    if extensao not in ALLOWED_ANEXO_EXTENSIONS:
        raise ValueError("Formato de anexo não suportado. Envie PDF, DOC, DOCX, XLS, XLSX ou imagem.")
    pasta = os.path.join(current_app.instance_path, "cadastro_workflow")
    os.makedirs(pasta, exist_ok=True)
    nome_final = secure_filename(f"anexo_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}{extensao}")
    arquivo.save(os.path.join(pasta, nome_final))
    return {
        "nome": nome_original,
        "arquivo": nome_final,
        "url": url_for("cadastro_workflow.anexo_imagem", filename=nome_final),
    }


def _montar_anexos(imagem: dict | None = None, arquivo: dict | None = None, anexos_atuais: dict | None = None) -> str:
    atuais = anexos_atuais or {}
    anexos = {
        "observacoes": request.form.get("anexos", "").strip(),
        "imagem": imagem if imagem is not None else atuais.get("imagem"),
        "arquivo": arquivo if arquivo is not None else atuais.get("arquivo"),
    }
    return json.dumps(anexos, ensure_ascii=False)


def _query_visibilidade():
    """Quem atende os chamados (Compras/Fiscal/Admin) ve tudo; quem so
    solicita cadastro ve apenas as proprias solicitacoes."""
    role = session.get("role")
    username = session.get("username")
    query = CadastroWorkflowSolicitacao.query
    if role in {"Compras", "Fiscal", "Admin"} or role_contabil(role):
        return query
    return query.filter(CadastroWorkflowSolicitacao.solicitante == username)


def _filtro_categoria(query, categoria: str):
    """Traduz uma das 4 categorias visiveis (ver categoria_visivel no
    service) em filtro SQL, pra poder paginar/limitar com seguranca."""
    Sol = CadastroWorkflowSolicitacao
    if categoria == "finalizado":
        return query.filter(Sol.status == "Cadastrado")
    if categoria == "em_analise":
        return query.filter(
            Sol.status.in_(["Em Validacao Contabil", "Em Validacao Compras", "Em Validacao Fiscal"]),
            Sol.responsavel_atual.is_(None),
        )
    if categoria == "atendimento_contabil":
        return query.filter(Sol.status == "Em Validacao Contabil", Sol.responsavel_atual.isnot(None))
    if categoria == "atendimento_compras":
        return query.filter(Sol.status == "Em Validacao Compras", Sol.responsavel_atual.isnot(None))
    if categoria == "atendimento_fiscal":
        return query.filter(Sol.status == "Em Validacao Fiscal", Sol.responsavel_atual.isnot(None))
    return query


def _contagens_categoria(base_query):
    return {chave: _filtro_categoria(base_query, chave).count() for chave, _label in CATEGORIAS_VISIVEIS}


def _query_solicitacoes():
    query = _query_visibilidade()

    tipo = request.args.get("tipo", "").strip()
    status = request.args.get("status", "").strip()
    numero = request.args.get("numero", "").strip()
    categoria = request.args.get("categoria", "").strip()
    if tipo:
        query = query.filter(CadastroWorkflowSolicitacao.tipo == tipo)
    if status:
        query = query.filter(CadastroWorkflowSolicitacao.status == status)
    if numero:
        query = query.filter(CadastroWorkflowSolicitacao.numero.contains(numero.zfill(6) if numero.isdigit() else numero))
    if categoria:
        query = _filtro_categoria(query, categoria)
    return query.order_by(CadastroWorkflowSolicitacao.data_ultima_movimentacao.desc())


@cadastro_workflow_bp.get("/")
@permission_required("PAGE_CADASTRO_WORKFLOW")
def dashboard():
    solicitacoes = _query_solicitacoes().limit(250).all()
    contagens = _contagens_categoria(_query_visibilidade())
    return render_template(
        "cadastro_workflow_dashboard.html",
        user=session.get("username"),
        solicitacoes=solicitacoes,
        tipos=TIPOS_CADASTRO,
        statuses=STATUS,
        filtros=request.args,
        tempo_na_etapa=tempo_na_etapa,
        prazo_restante=prazo_restante,
        get_dados=get_dados,
        categorias=CATEGORIAS_VISIVEIS,
        contagens=contagens,
        categoria_visivel=categoria_visivel,
        can_contabil=_can_operar_contabil(),
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
            erro = "Tipo de cadastro inválido."
        else:
            campos_abertura = meta.get("solicitante_fields") or meta["fields"]
            dados = {campo: request.form.get(campo, "").strip() for campo, _label, _req in campos_abertura}
            duplicidades = buscar_duplicidades(tipo, dados)
            try:
                imagem = _salvar_imagem_produto() if tipo == "material" else None
                arquivo_anexo = _salvar_arquivo_anexo() if tipo == "material" else None
                sol = criar_solicitacao(
                    tipo=tipo,
                    dados=dados,
                    solicitante=session.get("username", "usuario"),
                    anexos=_montar_anexos(imagem, arquivo_anexo),
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
        campo_opcoes=CAMPO_OPCOES,
        utilizacao_sugestoes=MATERIAL_UTILIZACAO_SUGESTOES,
    )


@cadastro_workflow_bp.get("/<int:solicitacao_id>")
@permission_required("PAGE_CADASTRO_WORKFLOW")
def detalhe(solicitacao_id):
    sol = CadastroWorkflowSolicitacao.query.get_or_404(solicitacao_id)
    if session.get("role") not in {"Admin", "Compras", "Fiscal"} and not role_contabil(session.get("role")) and sol.solicitante != session.get("username"):
        return render_template("acesso_negado.html", user=session.get("username")), 403
    return render_template(
        "cadastro_workflow_detalhe.html",
        user=session.get("username"),
        solicitacao=sol,
        dados=get_dados(sol),
        tipos=TIPOS_CADASTRO,
        campo_opcoes=CAMPO_OPCOES,
        anexos_info=_parse_anexos(sol.anexos),
        tempo_na_etapa=tempo_na_etapa(sol),
        prazo=prazo_restante(sol),
        can_contabil=_can_operar_contabil(),
        can_compras=_can_operar_compras(),
        can_fiscal=_can_operar_fiscal(),
        is_solicitante=sol.solicitante == session.get("username"),
        motivos_padrao=motivos_padrao_por_acao(),
    )


@cadastro_workflow_bp.get("/anexos/<path:filename>")
@login_required
def anexo_imagem(filename):
    if not has_permission("PAGE_CADASTRO_WORKFLOW"):
        return jsonify({"error": "Acesso negado"}), 403
    pasta = os.path.join(current_app.instance_path, "cadastro_workflow")
    return send_from_directory(pasta, secure_filename(filename))


@cadastro_workflow_bp.post("/<int:solicitacao_id>/acao")
@permission_required("PAGE_CADASTRO_WORKFLOW")
def acao(solicitacao_id):
    sol = CadastroWorkflowSolicitacao.query.get_or_404(solicitacao_id)
    if session.get("role") not in {"Admin", "Compras", "Fiscal"} and not role_contabil(session.get("role")) and sol.solicitante != session.get("username"):
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
        if acao_nome == "atualizar_anexos":
            atuais = _parse_anexos(sol.anexos)
            nova_imagem = _salvar_imagem_produto()
            novo_arquivo = _salvar_arquivo_anexo()
            sol.anexos = _montar_anexos(nova_imagem, novo_arquivo, anexos_atuais=atuais)
            db.session.commit()
    except ValueError as exc:
        return redirect(url_for("cadastro_workflow.detalhe", solicitacao_id=sol.id, erro=str(exc)))
    return redirect(url_for("cadastro_workflow.detalhe", solicitacao_id=sol.id))


@cadastro_workflow_bp.route("/admin/sla", methods=["GET", "POST"])
@permission_required("PAGE_CADASTRO_WORKFLOW")
def configurar_sla():
    if session.get("role") != "Admin":
        return render_template("acesso_negado.html", user=session.get("username")), 403
    if request.method == "POST":
        for depto in ("Contabil", "Compras", "Fiscal"):
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
        sla_contabil=get_sla_horas("Contabil"),
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


@cadastro_workflow_bp.get("/api/materiais-grv")
@login_required
def api_materiais_grv():
    if not has_permission("PAGE_CADASTRO_WORKFLOW"):
        return jsonify({"error": "Acesso negado"}), 403
    codigo = request.args.get("codigo", "")
    descricao = request.args.get("descricao", "")
    return jsonify({"materiais": buscar_materiais_grv(codigo=codigo, descricao=descricao, limite=12)})


@cadastro_workflow_bp.get("/api/plano-contas")
@login_required
def api_plano_contas():
    if not has_permission("PAGE_CADASTRO_WORKFLOW"):
        return jsonify({"error": "Acesso negado"}), 403
    termo = (request.args.get("q", "") or "").strip()
    if not termo:
        return jsonify({"planos": []})
    rows = (
        PlanoContaDominio.query
        .filter(
            or_(
                PlanoContaDominio.codigo_conta.ilike(f"%{termo}%"),
                PlanoContaDominio.nome_conta.ilike(f"%{termo}%"),
            )
        )
        .order_by(PlanoContaDominio.codigo_conta.asc())
        .limit(20)
        .all()
    )
    return jsonify(
        {
            "planos": [
                {
                    "codigo": row.codigo_conta,
                    "nome": row.nome_conta,
                    "classificacao": row.classificacao_conta or "",
                    "tipo": row.tipo_conta or "",
                }
                for row in rows
            ]
        }
    )


@cadastro_workflow_bp.get("/api/plano-contas/sugerir")
@login_required
def api_sugerir_plano_contas():
    if not has_permission("PAGE_CADASTRO_WORKFLOW"):
        return jsonify({"error": "Acesso negado"}), 403
    dados = {
        "descricao": request.args.get("descricao", ""),
        "utilizacao": request.args.get("utilizacao", ""),
        "fornecedor_sugerido": request.args.get("fornecedor_sugerido", ""),
        "detalhe_utilizacao": request.args.get("detalhe_utilizacao", ""),
    }
    return jsonify({"planos": sugerir_plano_contas_material(dados, limite=6)})


@cadastro_workflow_bp.get("/api/cnpj")
@login_required
def api_cnpj():
    if not has_permission("PAGE_CADASTRO_WORKFLOW"):
        return jsonify({"error": "Acesso negado"}), 403
    cnpj = request.args.get("cnpj", "")
    try:
        return jsonify({"ok": True, "dados": consultar_cartao_cnpj(cnpj)})
    except ValueError as exc:
        return jsonify({"ok": False, "erro": str(exc)}), 400
