"""
Facilities Routes - Gestão de Obras, EPI, Limpeza
- Páginas/APIs internas: exigem login (evitar abuso e dados inconsistentes).
- Admin: gestão completa via PAGE_FACILITIES_ADMIN.
"""

import base64
import os
import time
import uuid
from datetime import datetime, date, timedelta

import requests
from flask import Blueprint, current_app, jsonify, render_template, request, send_file, session, url_for
from werkzeug.utils import secure_filename

from ..auth import login_required, permission_required
from ..extensions import db
from ..models import (
    FacilitiesAuditLog,
    FacilitiesChamado,
    FacilitiesColaborador,
    FacilitiesEpiCicloTroca,
    FacilitiesEpiMaterial,
    FacilitiesEpiSolicitacao,
    FacilitiesEstoqueItem,
    FacilitiesLimpeza,
    FacilitiesLimpezaTemplate,
    FacilitiesProjeto,
    FacilitiesProjetoTarefa,
    FacilitiesTarefa,
)


# Cache simples em memória para o catálogo do ERP (evitar bater na API a cada request)
_ERP_ESTOQUE_CACHE = {"ts": 0.0, "data": None}
_ERP_ESTOQUE_TTL_SECONDS = 300  # 5 minutos


def _classificar_tipo_item(item):
    """Classifica um item do ERP como 'epi', 'uniforme' ou None.
    Baseado em grupo/familia (case-insensitive)."""
    grupo = (item.get("grupo") or "").upper()
    familia = (item.get("familia") or "").upper()
    texto = f"{grupo} {familia}"
    if "UNIFORME" in texto:
        return "uniforme"
    if "EPI" in texto:
        return "epi"
    return None


def _buscar_estoque_erp():
    """Busca o catalogo de estoque no ERP externo com cache de 5 min.
    Retorna lista de dicts, ou None em caso de falha."""
    agora = time.time()
    if _ERP_ESTOQUE_CACHE["data"] is not None and (agora - _ERP_ESTOQUE_CACHE["ts"]) < _ERP_ESTOQUE_TTL_SECONDS:
        return _ERP_ESTOQUE_CACHE["data"]
    url = current_app.config.get("ERP_ESTOQUE_URL")
    timeout = current_app.config.get("ERP_ESTOQUE_TIMEOUT", 30)
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=timeout, headers={"ngrok-skip-browser-warning": "true"})
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            return None
        _ERP_ESTOQUE_CACHE["data"] = data
        _ERP_ESTOQUE_CACHE["ts"] = agora
        return data
    except Exception as exc:
        current_app.logger.warning("Falha ao consultar ERP_ESTOQUE_URL: %s", exc)
        return None

from ..auth import login_required, permission_required
from ..extensions import db
from ..models import (
    FacilitiesColaborador,
    FacilitiesEpiMaterial,
    FacilitiesEpiSolicitacao,
    FacilitiesLimpeza,
    FacilitiesProjeto,
    FacilitiesTarefa,
    FacilitiesEstoqueItem,
    FacilitiesChamado,
)


facilities_bp = Blueprint("facilities", __name__)


def _colaborador_do_usuario_logado():
    """Retorna o FacilitiesColaborador vinculado ao usuário logado (por username == nome).
    Usa matching robusto (sem acentos, case-insensitive) para evitar falhas por variações.
    Retorna None se não houver correspondência."""
    username = (session.get("username") or "").strip()
    if not username:
        return None
    # Tenta match exato case-insensitive
    resultado = (
        FacilitiesColaborador.query
        .filter(db.func.lower(FacilitiesColaborador.nome) == username.lower())
        .first()
    )
    if resultado:
        return resultado
    # Fallback: matching normalizado (remove acentos)
    username_norm = _normalizar_nome(username)
    todos = FacilitiesColaborador.query.all()
    for c in todos:
        if _normalizar_nome(c.nome) == username_norm:
            return c
    return None


def _audit(entidade: str, entidade_id, acao: str, detalhes: str = "") -> None:
    """Registra linha no log de auditoria. Nunca falha a request."""
    try:
        log = FacilitiesAuditLog(
            usuario=(session.get("username") or "")[:100],
            entidade=entidade[:40],
            entidade_id=int(entidade_id) if entidade_id else None,
            acao=acao[:40],
            detalhes=(detalhes or "")[:1500],
            ip=(request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:45],
        )
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _estoque_disponivel_erp(codigo_interno: str) -> int | None:
    """Retorna qtde_disponivel do ERP para um codigo_interno, ou None se indisponivel."""
    if not codigo_interno:
        return None
    estoque = _buscar_estoque_erp()
    if not estoque:
        return None
    for item in estoque:
        if (item.get("codigo_interno") or "") == codigo_interno:
            try:
                return int(item.get("qtde_disponivel") or 0)
            except (TypeError, ValueError):
                return 0
    return None


def _meses_validade_por_item(codigo_interno: str, nome_item: str) -> int:
    """Olha os ciclos cadastrados (codigo exato > palavra-chave no nome). Default = 6 meses."""
    if codigo_interno:
        ciclo = (
            FacilitiesEpiCicloTroca.query
            .filter_by(ativo=True, codigo_interno=codigo_interno)
            .first()
        )
        if ciclo:
            return ciclo.meses_validade
    nome_upper = (nome_item or "").upper()
    ciclos = (
        FacilitiesEpiCicloTroca.query
        .filter_by(ativo=True)
        .filter(FacilitiesEpiCicloTroca.palavra_chave.isnot(None))
        .all()
    )
    for c in ciclos:
        pk = (c.palavra_chave or "").upper()
        if pk and pk in nome_upper:
            return c.meses_validade
    return 6


def _add_months(d: date, months: int) -> date:
    """Adiciona meses a uma data (sem usar dateutil)."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


# ============================================================================
# PÁGINAS (REQUEREM LOGIN)
# ============================================================================

@facilities_bp.route("/facilities/solicitar-epi")
@login_required
@permission_required("PAGE_FACILITIES_GESTOR")
def page_solicitar_epi():
    """Página para solicitar EPI/Uniforme — exclusivo para gestores e admins."""
    return render_template("facilities_solicitar_epi.html")


@facilities_bp.route("/facilities/minhas-solicitacoes")
@login_required
@permission_required("PAGE_FACILITIES_GESTOR")
def page_minhas_solicitacoes():
    """Tela do gestor de setor: acompanha o status das solicitacoes que ele abriu."""
    return render_template("facilities_minhas_solicitacoes.html")


@facilities_bp.route("/facilities/almoxarife")
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def page_almoxarife():
    """Tela do almoxarife: apenas itens LIBERADOS aguardando entrega fisica."""
    return render_template("facilities_almoxarife.html")


@facilities_bp.route("/facilities/relatorio-consumo")
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def page_relatorio_consumo():
    """Relatório de consumo de EPI/Uniforme por setor."""
    return render_template("facilities_relatorio_consumo.html")


@facilities_bp.route("/facilities/cronograma-limpeza")
@login_required
def page_cronograma_limpeza():
    """Página para ver cronograma de limpeza (colaborador logado)."""
    return render_template("facilities_cronograma_limpeza.html")


# ============================================================================
# PÁGINAS ADMIN (REQUER LOGIN)
# ============================================================================

@facilities_bp.route("/facilities/admin")
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def page_facilities_admin():
    """Painel de gestão Facilities (Admin)"""
    return render_template("facilities_admin.html")


# ============================================================================
# API - COLABORADORES (requer login)
# ============================================================================

@facilities_bp.route("/api/facilities/me")
@login_required
def api_me():
    """Retorna o colaborador vinculado ao usuario logado + se ele e admin Facilities."""
    from ..auth import has_permission

    colab = _colaborador_do_usuario_logado()
    username = session.get("username") or ""
    is_admin = False
    try:
        is_admin = bool(has_permission("PAGE_FACILITIES_ADMIN"))
    except Exception:
        # fallback: role=Admin
        is_admin = (session.get("role") or "").lower() == "admin"

    return jsonify({
        "username": username,
        "is_admin": is_admin,
        "colaborador": {
            "id": colab.id,
            "nome": colab.nome,
            "cargo": colab.cargo or "",
            "setor": colab.setor or "",
            "email": colab.email or "",
            "nivel_acesso": colab.nivel_acesso,
        } if colab else None,
    })


@facilities_bp.route("/api/facilities/colaboradores")
@login_required
@permission_required("PAGE_FACILITIES_GESTOR")
def api_listar_colaboradores():
    """Lista colaboradores ativos para seleção em formulários"""
    rows = FacilitiesColaborador.query.filter_by(ativo=True).order_by(FacilitiesColaborador.nome).all()
    return jsonify({
        "rows": [
            {
                "id": c.id,
                "nome": c.nome,
                "cargo": c.cargo or "",
                "setor": c.setor or "",
                "email": c.email or "",
                "nivel_acesso": c.nivel_acesso,
            }
            for c in rows
        ]
    })


@facilities_bp.route("/api/facilities/colaboradores", methods=["POST"])
@login_required
def api_criar_colaborador():
    """Cria um novo colaborador (usuário logado)."""
    data = request.get_json() or {}
    nome = (data.get("nome") or "").strip()
    if not nome:
        return jsonify({"error": "Nome é obrigatório."}), 400
    
    colaborador = FacilitiesColaborador(
        nome=nome,
        cargo=data.get("cargo", ""),
        setor=data.get("setor", ""),
        telefone=data.get("telefone", ""),
        email=(data.get("email") or "").strip() or None,
        nivel_acesso=data.get("nivel_acesso", "solicitante"),
    )
    db.session.add(colaborador)
    db.session.commit()
    return jsonify({"id": colaborador.id, "nome": colaborador.nome})


# ============================================================================
# API - MATERIAIS EPI/UNIFORME (requer login)
# ============================================================================

@facilities_bp.route("/api/facilities/epi-materiais")
@login_required
def api_listar_epi_materiais():
    """Lista materiais EPI/Uniforme disponiveis.

    Fonte primaria: ERP externo (ERP_ESTOQUE_URL) filtrado por grupo/familia.
    Fallback: tabela local FacilitiesEpiMaterial (seed/cadastro manual).
    """
    tipo = (request.args.get("tipo") or "").lower()

    estoque = _buscar_estoque_erp()
    if estoque:
        rows = []
        for item in estoque:
            classificacao = _classificar_tipo_item(item)
            if classificacao is None:
                continue
            if tipo in ("epi", "uniforme") and classificacao != tipo:
                continue
            try:
                disponivel = int(item.get("qtde_disponivel") or 0)
            except (TypeError, ValueError):
                disponivel = 0
            if disponivel <= 0:
                continue
            codigo = item.get("codigo_interno") or ""
            nome = item.get("item") or ""
            rows.append({
                "id": codigo,
                "codigo_interno": codigo,
                "nome": nome,
                "tipo": classificacao,
                "numero_ca": "",
                "qtd_estoque": disponivel,
                "qtd_minima": 0,
                "abaixo_minimo": disponivel <= 2,
                "label": f"{codigo} - {nome} (estoque: {disponivel})",
            })
        rows.sort(key=lambda r: r["codigo_interno"])
        return jsonify({"rows": rows, "fonte": "erp"})

    # Fallback: catalogo local
    query = FacilitiesEpiMaterial.query.filter_by(ativo=True)
    if tipo in ("epi", "uniforme"):
        query = query.filter_by(tipo=tipo)
    rows = query.order_by(FacilitiesEpiMaterial.codigo_interno).all()
    return jsonify({
        "rows": [
            {
                "id": m.id,
                "codigo_interno": m.codigo_interno,
                "nome": m.nome,
                "tipo": m.tipo,
                "numero_ca": m.numero_ca or "",
                "qtd_estoque": m.qtd_estoque or 0,
                "qtd_minima": m.qtd_minima or 0,
                "abaixo_minimo": (m.qtd_estoque or 0) <= (m.qtd_minima or 0),
                "label": f"{m.codigo_interno} - {m.nome}",
            }
            for m in rows
        ],
        "fonte": "local",
    })


@facilities_bp.route("/api/facilities/epi-materiais", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_criar_epi_material():
    """Cadastra novo material EPI/Uniforme (Admin)"""
    data = request.get_json() or {}
    codigo = (data.get("codigo_interno") or "").strip()
    nome = (data.get("nome") or "").strip()
    if not codigo or not nome:
        return jsonify({"error": "Código e nome são obrigatórios."}), 400

    material = FacilitiesEpiMaterial(
        codigo_interno=codigo,
        nome=nome,
        tipo=data.get("tipo", "epi"),
        numero_ca=(data.get("numero_ca") or "").strip() or None,
        qtd_estoque=int(data.get("qtd_estoque") or 0),
        qtd_minima=int(data.get("qtd_minima") or 0),
    )
    db.session.add(material)
    db.session.commit()
    return jsonify({"id": material.id, "codigo_interno": material.codigo_interno, "nome": material.nome})


@facilities_bp.route("/api/facilities/epi-materiais/<int:id>", methods=["PUT"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_atualizar_epi_material(id):
    """Atualiza material EPI (Admin) - inclui ajuste de estoque/CA."""
    m = FacilitiesEpiMaterial.query.get_or_404(id)
    data = request.get_json() or {}
    if "nome" in data:
        m.nome = data["nome"]
    if "tipo" in data:
        m.tipo = data["tipo"]
    if "numero_ca" in data:
        m.numero_ca = (data["numero_ca"] or "").strip() or None
    if "qtd_estoque" in data:
        m.qtd_estoque = int(data["qtd_estoque"] or 0)
    if "qtd_minima" in data:
        m.qtd_minima = int(data["qtd_minima"] or 0)
    if "ativo" in data:
        m.ativo = bool(data["ativo"])
    db.session.commit()
    return jsonify({"id": m.id, "qtd_estoque": m.qtd_estoque})


# ============================================================================
# API - SOLICITAÇÕES EPI (requer login)
# ============================================================================

@facilities_bp.route("/api/facilities/epi-solicitacoes")
@login_required
@permission_required("PAGE_FACILITIES_GESTOR")
def api_listar_epi_solicitacoes():
    """Lista solicitações de EPI (filtros: status, colaborador_id, solicitante_id, mine, busca, data_ini/fim, setor, tipo)"""
    status = request.args.get("status", "").lower()
    colaborador_id = request.args.get("colaborador_id", type=int)
    solicitante_id = request.args.get("solicitante_id", type=int)
    apenas_minhas = (request.args.get("mine", "").lower() in ("1", "true", "yes"))
    busca = (request.args.get("busca") or "").strip()
    data_ini = request.args.get("data_ini") or ""
    data_fim = request.args.get("data_fim") or ""
    setor = (request.args.get("setor") or "").strip()
    tipo = (request.args.get("tipo") or "").strip().lower()
    limit = request.args.get("limit", type=int) or 200

    query = FacilitiesEpiSolicitacao.query
    if status:
        query = query.filter_by(status=status)
    if tipo in ("epi", "uniforme"):
        query = query.filter_by(tipo=tipo)
    if colaborador_id:
        query = query.filter_by(colaborador_id=colaborador_id)
    if apenas_minhas:
        logado = _colaborador_do_usuario_logado()
        if logado:
            query = query.filter_by(solicitante_id=logado.id)
        else:
            return jsonify({"rows": []})
    elif solicitante_id:
        query = query.filter_by(solicitante_id=solicitante_id)
    if busca:
        termo = f"%{busca.lower()}%"
        query = query.join(FacilitiesColaborador, FacilitiesEpiSolicitacao.colaborador_id == FacilitiesColaborador.id).filter(
            db.or_(
                db.func.lower(FacilitiesColaborador.nome).like(termo),
                db.func.lower(FacilitiesEpiSolicitacao.nome_item).like(termo),
            )
        )
    if setor:
        query = query.join(FacilitiesColaborador, FacilitiesEpiSolicitacao.colaborador_id == FacilitiesColaborador.id).filter(
            db.func.lower(FacilitiesColaborador.setor) == setor.lower()
        )
    if data_ini:
        try:
            dt = datetime.strptime(data_ini, "%Y-%m-%d")
            query = query.filter(FacilitiesEpiSolicitacao.solicitado_em >= dt)
        except ValueError:
            pass
    if data_fim:
        try:
            dt = datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(FacilitiesEpiSolicitacao.solicitado_em < dt)
        except ValueError:
            pass

    rows = query.order_by(FacilitiesEpiSolicitacao.solicitado_em.desc()).limit(limit).all()
    return jsonify({
        "rows": [
            {
                "id": s.id,
                "colaborador_id": s.colaborador_id,
                "colaborador_nome": s.colaborador.nome if s.colaborador else "",
                "colaborador_setor": (s.colaborador.setor if s.colaborador else "") or "",
                "solicitante_id": s.solicitante_id,
                "solicitante_nome": s.solicitante_nome or (s.solicitante.nome if s.solicitante else "") or "",
                "tipo": s.tipo,
                "codigo_item": s.codigo_item,
                "nome_item": s.nome_item,
                "tamanho": s.tamanho or "",
                "quantidade": s.quantidade,
                "motivo": s.motivo or "",
                "motivo_recusa": s.motivo_recusa or "",
                "motivo_cancelamento": s.motivo_cancelamento or "",
                "status": s.status,
                "solicitado_em": s.solicitado_em.strftime("%d/%m/%Y %H:%M") if s.solicitado_em else "",
                "liberado_em": s.liberado_em.strftime("%d/%m/%Y %H:%M") if s.liberado_em else "",
                "liberador_nome": s.liberador.nome if s.liberador else (s.liberado_por_username or ""),
                "retirado_em": s.retirado_em.strftime("%d/%m/%Y %H:%M") if s.retirado_em else "",
                "retirado_por": s.retirado_por or "",
                "numero_ca_entregue": s.numero_ca_entregue or "",
                "tem_assinatura": bool(s.assinatura_path),
                "proxima_troca_em": s.proxima_troca_em.strftime("%d/%m/%Y") if s.proxima_troca_em else "",
                "cancelado_em": s.cancelado_em.strftime("%d/%m/%Y %H:%M") if s.cancelado_em else "",
            }
            for s in rows
        ]
    })


def _normalizar_nome(s: str) -> str:
    """Remove acentos, converte para minúsculas e colapsa espaços.
    Usado para comparação fuzzy de nomes de colaboradores."""
    import unicodedata
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.lower().split())


def _resolver_beneficiario(data: dict):
    """Resolve ou cria FacilitiesColaborador beneficiario a partir do payload.
    Aceita colaborador_id, beneficiario_id (ambos FK) ou beneficiario_nome (texto livre).
    Matching robusto: ignora acentos, caixa e espaços duplicados para evitar duplicatas.
    Retorna (colaborador, erro_msg). colaborador=None implica erro."""
    colaborador_id = data.get("colaborador_id") or data.get("beneficiario_id")
    if colaborador_id:
        colab = FacilitiesColaborador.query.get(int(colaborador_id))
        if not colab:
            return None, "Colaborador informado nao encontrado."
        return colab, None

    nome = (data.get("beneficiario_nome") or "").strip()
    if not nome:
        return None, "Informe o funcionario beneficiario da solicitacao."

    # Busca exata case-insensitive
    existente = (
        FacilitiesColaborador.query
        .filter(db.func.lower(FacilitiesColaborador.nome) == nome.lower())
        .filter(FacilitiesColaborador.ativo == True)  # noqa: E712
        .first()
    )
    if existente:
        return existente, None

    # Busca fuzzy: normaliza acentos e espaços (evita "JOAO SILVA" vs "João Silva")
    nome_norm = _normalizar_nome(nome)
    todos = FacilitiesColaborador.query.filter_by(ativo=True).all()
    for c in todos:
        if _normalizar_nome(c.nome) == nome_norm:
            return c, None

    # Nenhum match — cria novo colaborador
    novo = FacilitiesColaborador(
        nome=nome,
        setor=(data.get("beneficiario_setor") or "").strip() or None,
        cargo=(data.get("beneficiario_cargo") or "").strip() or None,
        nivel_acesso="solicitante",
    )
    db.session.add(novo)
    db.session.flush()  # pega o id
    return novo, None


@facilities_bp.route("/api/facilities/epi-solicitacoes", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_GESTOR")
def api_criar_epi_solicitacao():
    """Cria nova solicitacao de EPI/Uniforme.
    Apenas gestores (PAGE_FACILITIES_GESTOR) e admins podem solicitar.
    """
    data = request.get_json() or {}
    codigo_item = (data.get("codigo_item") or "").strip()
    nome_item = (data.get("nome_item") or "").strip()
    if not codigo_item or not nome_item:
        return jsonify({"error": "Item eh obrigatorio."}), 400

    try:
        quantidade = max(1, int(data.get("quantidade") or 1))
    except (TypeError, ValueError):
        quantidade = 1

    # Validacao de estoque (ERP) - bloqueia se pedir mais do que tem
    disponivel = _estoque_disponivel_erp(codigo_item)
    if disponivel is not None and quantidade > disponivel:
        return jsonify({
            "error": f"Quantidade solicitada ({quantidade}) maior que o estoque disponivel ({disponivel}).",
            "disponivel": disponivel,
        }), 400

    beneficiario, erro = _resolver_beneficiario(data)
    if erro:
        return jsonify({"error": erro}), 400

    from ..auth import has_permission

    # Solicitante = colaborador do usuario logado (gestor)
    logado = _colaborador_do_usuario_logado()
    eh_admin_facilities = bool(has_permission("PAGE_FACILITIES_ADMIN"))

    if not eh_admin_facilities and not logado:
        return jsonify({"error": "Seu usuário não está vinculado a um líder de departamento no Facilities."}), 403

    # Regra de negócio: líder solicita apenas para subordinados do mesmo setor.
    if not eh_admin_facilities and logado:
        setor_lider = _normalizar_nome(logado.setor or "")
        setor_beneficiario = _normalizar_nome(beneficiario.setor or "")
        if not setor_lider:
            return jsonify({"error": "Líder sem setor definido. Atualize seu cadastro antes de solicitar EPI."}), 400
        if not setor_beneficiario:
            return jsonify({"error": "O colaborador beneficiário não possui setor cadastrado."}), 400
        if setor_lider != setor_beneficiario:
            return jsonify({
                "error": "Você só pode solicitar EPI para colaboradores do seu próprio setor.",
                "setor_lider": logado.setor or "",
                "setor_beneficiario": beneficiario.setor or "",
            }), 403

    solicitante_id = logado.id if logado else None
    # solicitante_nome: garante que o nome sempre fica registrado mesmo sem FacilitiesColaborador
    solicitante_nome = (logado.nome if logado else None) or (session.get("username") or "")[:120]

    solicitacao = FacilitiesEpiSolicitacao(
        colaborador_id=beneficiario.id,
        solicitante_id=solicitante_id,
        solicitante_nome=solicitante_nome,
        tipo=data.get("tipo", "epi"),
        codigo_item=codigo_item,
        nome_item=nome_item,
        tamanho=data.get("tamanho", ""),
        quantidade=quantidade,
        motivo=data.get("motivo", ""),
    )
    db.session.add(solicitacao)
    db.session.commit()

    _audit("epi_solicitacao", solicitacao.id, "criar",
           f"beneficiario={beneficiario.nome} item={nome_item} qtd={quantidade}")

    # Notifica gestores por e-mail (assincrono).
    try:
        _notificar_gestores_nova_solicitacao(solicitacao)
    except Exception as exc:  # nunca quebra a request
        current_app.logger.warning("Falha ao notificar gestores Facilities: %s", exc)

    return jsonify({
        "id": solicitacao.id,
        "status": solicitacao.status,
        "beneficiario": {"id": beneficiario.id, "nome": beneficiario.nome},
    })


@facilities_bp.route("/api/facilities/epi-solicitacoes/<int:id>/cancelar", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_GESTOR")
def api_cancelar_epi_solicitacao(id):
    """Cancela uma solicitacao em status 'solicitado'.
    Somente o solicitante (gestor que abriu) ou admin podem cancelar."""
    from ..auth import has_permission

    solicitacao = FacilitiesEpiSolicitacao.query.get_or_404(id)
    if solicitacao.status != "solicitado":
        return jsonify({"error": "Somente solicitacoes pendentes podem ser canceladas."}), 400

    logado = _colaborador_do_usuario_logado()
    eh_admin = False
    try:
        eh_admin = bool(has_permission("PAGE_FACILITIES_ADMIN"))
    except Exception:
        eh_admin = (session.get("role") or "").lower() == "admin"

    if not eh_admin and (not logado or solicitacao.solicitante_id != logado.id):
        return jsonify({"error": "Voce nao pode cancelar esta solicitacao."}), 403

    data = request.get_json(silent=True) or {}
    solicitacao.status = "cancelado"
    solicitacao.cancelado_em = datetime.now()
    solicitacao.cancelado_por = (session.get("username") or "")[:100]
    solicitacao.motivo_cancelamento = (data.get("motivo") or "").strip() or None
    db.session.commit()

    _audit("epi_solicitacao", solicitacao.id, "cancelar",
           f"motivo={solicitacao.motivo_cancelamento or '-'}")
    return jsonify({"id": solicitacao.id, "status": solicitacao.status})


@facilities_bp.route("/api/facilities/colaboradores/<int:id>/historico-epi")
@login_required
@permission_required("PAGE_FACILITIES_GESTOR")
def api_historico_epi_colaborador(id):
    """Retorna ultimas solicitacoes/retiradas do colaborador + alertas de vencimento."""
    colab = FacilitiesColaborador.query.get_or_404(id)
    rows = (
        FacilitiesEpiSolicitacao.query
        .filter_by(colaborador_id=colab.id)
        .order_by(FacilitiesEpiSolicitacao.solicitado_em.desc())
        .limit(30)
        .all()
    )
    hoje = date.today()
    historico = []
    vencidos = 0
    proximos = 0
    for s in rows:
        venc = s.proxima_troca_em
        status_vencimento = None
        if s.status == "retirado" and venc:
            delta = (venc - hoje).days
            if delta < 0:
                status_vencimento = "vencido"
                vencidos += 1
            elif delta <= 30:
                status_vencimento = "proximo"
                proximos += 1
            else:
                status_vencimento = "ok"
        historico.append({
            "id": s.id,
            "tipo": s.tipo,
            "codigo_item": s.codigo_item,
            "nome_item": s.nome_item,
            "tamanho": s.tamanho or "",
            "quantidade": s.quantidade,
            "status": s.status,
            "solicitado_em": s.solicitado_em.strftime("%d/%m/%Y") if s.solicitado_em else "",
            "retirado_em": s.retirado_em.strftime("%d/%m/%Y") if s.retirado_em else "",
            "proxima_troca_em": venc.strftime("%d/%m/%Y") if venc else "",
            "status_vencimento": status_vencimento,
        })
    return jsonify({
        "colaborador": {"id": colab.id, "nome": colab.nome, "setor": colab.setor or ""},
        "historico": historico,
        "resumo": {"vencidos": vencidos, "proximos_30d": proximos, "total_retiradas": sum(1 for h in historico if h["status"] == "retirado")},
    })


def _notificar_gestores_nova_solicitacao(solicitacao: FacilitiesEpiSolicitacao) -> None:
    """Dispara e-mail aos gestores Facilities cadastrados com email."""
    from ..services.email_service import enviar_email_solicitacao_epi

    gestores = (
        FacilitiesColaborador.query
        .filter(FacilitiesColaborador.nivel_acesso == "gestor")
        .filter(FacilitiesColaborador.ativo == True)  # noqa: E712
        .filter(FacilitiesColaborador.email.isnot(None))
        .filter(FacilitiesColaborador.email != "")
        .all()
    )
    emails = [g.email for g in gestores if g.email]
    if not emails:
        return

    solicitante_nome = solicitacao.colaborador.nome if solicitacao.colaborador else "-"
    try:
        url_admin = url_for("facilities.page_facilities_admin", _external=True)
    except Exception:
        url_admin = "/facilities/admin"

    enviar_email_solicitacao_epi(
        destinatarios_emails=emails,
        solicitante_nome=solicitante_nome,
        item_nome=solicitacao.nome_item,
        quantidade=solicitacao.quantidade,
        tamanho=solicitacao.tamanho or "",
        motivo=solicitacao.motivo or "",
        url_admin=url_admin,
    )


@facilities_bp.route("/api/facilities/epi-solicitacoes/<int:id>/aprovar", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_aprovar_epi_solicitacao(id):
    """Aprova ou nega solicitação de EPI (Admin)"""
    data = request.get_json() or {}
    acao = data.get("acao", "").lower()  # liberar|negar
    
    solicitacao = FacilitiesEpiSolicitacao.query.get_or_404(id)
    if solicitacao.status != "solicitado":
        return jsonify({"error": "Solicitacao ja processada."}), 400

    gestor = _colaborador_do_usuario_logado()
    usuario = (session.get("username") or "")[:100]

    if acao == "liberar":
        solicitacao.status = "liberado"
        solicitacao.liberado_em = datetime.now()
        solicitacao.liberado_por_username = usuario
        if gestor:
            solicitacao.liberador_id = gestor.id
    elif acao == "negar":
        solicitacao.status = "negado"
        solicitacao.liberado_em = datetime.now()
        solicitacao.liberado_por_username = usuario
        solicitacao.motivo_recusa = (data.get("motivo_recusa") or "").strip() or None
        if gestor:
            solicitacao.liberador_id = gestor.id
    else:
        return jsonify({"error": "Acao invalida (liberar ou negar)."}), 400

    db.session.commit()
    _audit("epi_solicitacao", solicitacao.id, acao,
           f"motivo_recusa={solicitacao.motivo_recusa or '-'}")

    # Notifica gestor de setor (quem solicitou) sobre o resultado
    try:
        _notificar_gestor_retorno_epi(solicitacao, acao)
    except Exception as exc:
        current_app.logger.warning("Falha ao notificar gestor retorno EPI: %s", exc)

    return jsonify({"id": solicitacao.id, "status": solicitacao.status})


def _notificar_gestor_retorno_epi(solicitacao: "FacilitiesEpiSolicitacao", acao: str) -> None:
    """Envia e-mail ao gestor de setor que abriu a solicitacao informando aprovacao/negacao."""
    from ..services.email_service import enviar_email_retorno_epi

    # Determina e-mail do solicitante
    email_destino = None
    if solicitacao.solicitante and solicitacao.solicitante.email:
        email_destino = solicitacao.solicitante.email
    if not email_destino:
        return  # sem e-mail cadastrado, não envia

    gestor_nome = (
        solicitacao.solicitante_nome
        or (solicitacao.solicitante.nome if solicitacao.solicitante else None)
        or "Gestor"
    )
    colaborador_nome = solicitacao.colaborador.nome if solicitacao.colaborador else "?"
    try:
        url_ficha = url_for("facilities.pagina_ficha_epi", id=solicitacao.id, _external=True)
    except Exception:
        url_ficha = f"/facilities/ficha-epi/{solicitacao.id}"

    enviar_email_retorno_epi(
        destinatario_email=email_destino,
        gestor_nome=gestor_nome,
        colaborador_nome=colaborador_nome,
        item_nome=solicitacao.nome_item,
        quantidade=solicitacao.quantidade,
        acao=acao,
        motivo_recusa=solicitacao.motivo_recusa or "",
        url_ficha=url_ficha,
    )


@facilities_bp.route("/api/facilities/epi-solicitacoes/acao-lote", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_acao_lote_epi():
    """Executa aprovar/negar/cancelar em varias solicitacoes de uma vez.
    Payload: {ids: [1,2,3], acao: 'liberar'|'negar'|'cancelar', motivo?: '...'}"""
    data = request.get_json() or {}
    ids = data.get("ids") or []
    acao = (data.get("acao") or "").lower()
    motivo = (data.get("motivo") or "").strip()
    if not ids or acao not in ("liberar", "negar", "cancelar"):
        return jsonify({"error": "Parametros invalidos."}), 400

    usuario = (session.get("username") or "")[:100]
    gestor = _colaborador_do_usuario_logado()
    agora = datetime.now()

    sols = FacilitiesEpiSolicitacao.query.filter(FacilitiesEpiSolicitacao.id.in_(ids)).all()
    processadas = 0
    for s in sols:
        if s.status != "solicitado":
            continue
        if acao == "liberar":
            s.status = "liberado"
            s.liberado_em = agora
            s.liberado_por_username = usuario
            if gestor:
                s.liberador_id = gestor.id
        elif acao == "negar":
            s.status = "negado"
            s.liberado_em = agora
            s.liberado_por_username = usuario
            s.motivo_recusa = motivo or None
            if gestor:
                s.liberador_id = gestor.id
        elif acao == "cancelar":
            s.status = "cancelado"
            s.cancelado_em = agora
            s.cancelado_por = usuario
            s.motivo_cancelamento = motivo or None
        processadas += 1
    db.session.commit()
    _audit("epi_solicitacao", None, f"lote_{acao}", f"ids={ids} processadas={processadas}")
    return jsonify({"processadas": processadas, "solicitadas": len(ids)})


@facilities_bp.route("/api/facilities/epi-solicitacoes/<int:id>/retirar", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_retirar_epi_solicitacao(id):
    """Registra a entrega/retirada do EPI: assinatura digital + CA + abate estoque.

    Espera JSON: {
        "assinatura_base64": "data:image/png;base64,...",
        "numero_ca": "12345" (opcional, se em branco usa o CA do material)
    }
    """
    solicitacao = FacilitiesEpiSolicitacao.query.get_or_404(id)
    if solicitacao.status != "liberado":
        return jsonify({"error": "Só é possível registrar retirada de solicitações liberadas."}), 400

    data = request.get_json() or {}
    assinatura_b64 = (data.get("assinatura_base64") or "").strip()
    if not assinatura_b64:
        return jsonify({"error": "Assinatura digital é obrigatória (NR-6)."}), 400

    # Salva PNG da assinatura
    try:
        png_bytes = _decodificar_assinatura_png(assinatura_b64)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    pasta = _pasta_assinaturas()
    os.makedirs(pasta, exist_ok=True)
    nome_arquivo = f"epi_{solicitacao.id}_{uuid.uuid4().hex[:10]}.png"
    caminho = os.path.join(pasta, secure_filename(nome_arquivo))
    with open(caminho, "wb") as fp:
        fp.write(png_bytes)

    # CA: payload sobrescreve, senão pega do material cadastrado
    numero_ca = (data.get("numero_ca") or "").strip()
    if not numero_ca:
        material = FacilitiesEpiMaterial.query.filter_by(codigo_interno=solicitacao.codigo_item).first()
        numero_ca = (material.numero_ca if material and material.numero_ca else "") or ""

    # Abate estoque (se material existe e tem estoque suficiente)
    material = FacilitiesEpiMaterial.query.filter_by(codigo_interno=solicitacao.codigo_item).first()
    if material:
        atual = material.qtd_estoque or 0
        material.qtd_estoque = max(0, atual - (solicitacao.quantidade or 1))

    solicitacao.status = "retirado"
    solicitacao.retirado_em = datetime.now()
    solicitacao.retirado_por = session.get("username") or ""
    solicitacao.numero_ca_entregue = numero_ca or None
    solicitacao.assinatura_path = nome_arquivo

    # Calcula proxima troca baseado em ciclo
    meses = _meses_validade_por_item(solicitacao.codigo_item, solicitacao.nome_item)
    solicitacao.proxima_troca_em = _add_months(date.today(), meses)

    db.session.commit()
    _audit("epi_solicitacao", solicitacao.id, "retirar",
           f"ca={numero_ca} meses_validade={meses} proxima={solicitacao.proxima_troca_em}")
    return jsonify({
        "id": solicitacao.id,
        "status": solicitacao.status,
        "proxima_troca_em": solicitacao.proxima_troca_em.strftime("%d/%m/%Y"),
        "estoque_restante": material.qtd_estoque if material else None,
    })


@facilities_bp.route("/api/facilities/epi-solicitacoes/<int:id>/assinatura")
@login_required
def api_obter_assinatura_epi(id):
    """Serve a imagem PNG da assinatura digital armazenada."""
    solicitacao = FacilitiesEpiSolicitacao.query.get_or_404(id)
    if not solicitacao.assinatura_path:
        return jsonify({"error": "Sem assinatura."}), 404
    caminho = os.path.join(_pasta_assinaturas(), solicitacao.assinatura_path)
    if not os.path.isfile(caminho):
        return jsonify({"error": "Arquivo nao encontrado."}), 404
    return send_file(caminho, mimetype="image/png")


def _pasta_assinaturas() -> str:
    return os.path.join(current_app.instance_path, "facilities", "assinaturas")


def _pasta_evidencias_limpeza() -> str:
    return os.path.join(current_app.instance_path, "facilities", "limpeza_evidencias")


def _decodificar_assinatura_png(b64_data: str) -> bytes:
    """Aceita 'data:image/png;base64,XXXX' ou apenas 'XXXX'. Devolve bytes."""
    payload = b64_data
    if payload.startswith("data:"):
        try:
            payload = payload.split(",", 1)[1]
        except IndexError:
            raise ValueError("Assinatura invalida.")
    try:
        return base64.b64decode(payload, validate=True)
    except Exception:
        raise ValueError("Falha ao decodificar a assinatura.")


# ============================================================================
# API - CRONOGRAMA LIMPEZA (requer login)
# ============================================================================

@facilities_bp.route("/api/facilities/limpezas")
@login_required
def api_listar_limpezas():
    """Lista cronograma de limpeza (filtros: data, colaborador_id)"""
    data_inicio = request.args.get("data_inicio")
    data_fim = request.args.get("data_fim")
    colaborador_id = request.args.get("colaborador_id", type=int)
    
    query = FacilitiesLimpeza.query
    if data_inicio:
        query = query.filter(FacilitiesLimpeza.data_agendada >= data_inicio)
    if data_fim:
        query = query.filter(FacilitiesLimpeza.data_agendada <= data_fim)
    if colaborador_id:
        query = query.filter_by(colaborador_id=colaborador_id)
    
    rows = query.order_by(FacilitiesLimpeza.data_agendada, FacilitiesLimpeza.hora_inicio).all()
    return jsonify({
        "rows": [
            {
                "id": l.id,
                "colaborador_id": l.colaborador_id,
                "colaborador_nome": l.colaborador.nome if l.colaborador else "",
                "titulo": l.titulo,
                "local": l.local or "",
                "data_agendada": l.data_agendada.strftime("%Y-%m-%d") if l.data_agendada else "",
                "data_agendada_label": l.data_agendada.strftime("%d/%m/%Y") if l.data_agendada else "",
                "hora_inicio": l.hora_inicio or "",
                "hora_fim": l.hora_fim or "",
                "observacoes": l.observacoes or "",
                "concluido": l.concluido,
                "concluido_em": l.concluido_em.strftime("%d/%m/%Y %H:%M") if l.concluido_em else "",
                "concluido_por": l.concluido_por or "",
                "tem_evidencia": bool(l.evidencia_foto_path),
            }
            for l in rows
        ]
    })


@facilities_bp.route("/api/facilities/limpezas", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_criar_limpeza():
    """Cria novo agendamento de limpeza (Admin)"""
    data = request.get_json() or {}
    titulo = (data.get("titulo") or "").strip()
    data_agendada = data.get("data_agendada")
    
    if not titulo or not data_agendada:
        return jsonify({"error": "Título e data são obrigatórios."}), 400
    
    limpeza = FacilitiesLimpeza(
        colaborador_id=data.get("colaborador_id"),
        titulo=titulo,
        local=data.get("local", ""),
        data_agendada=datetime.strptime(data_agendada, "%Y-%m-%d").date(),
        hora_inicio=data.get("hora_inicio", ""),
        hora_fim=data.get("hora_fim", ""),
        observacoes=data.get("observacoes", ""),
    )
    db.session.add(limpeza)
    db.session.commit()
    return jsonify({"id": limpeza.id, "titulo": limpeza.titulo})


@facilities_bp.route("/api/facilities/limpezas/<int:id>/concluir", methods=["POST"])
@login_required
def api_concluir_limpeza(id):
    """Marca limpeza como concluida. Aceita upload opcional de foto-evidencia.

    - multipart/form-data com campo 'foto' (File) OU
    - JSON com 'foto_base64' (data:image/...;base64,XXXX)
    """
    limpeza = FacilitiesLimpeza.query.get_or_404(id)
    if limpeza.concluido:
        return jsonify({"error": "Limpeza ja concluida."}), 400

    pasta = _pasta_evidencias_limpeza()
    os.makedirs(pasta, exist_ok=True)
    nome_arquivo = None

    arquivo = request.files.get("foto")
    if arquivo and arquivo.filename:
        ext = (os.path.splitext(arquivo.filename)[1] or ".jpg").lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
            return jsonify({"error": "Formato de foto invalido."}), 400
        nome_arquivo = f"limp_{limpeza.id}_{uuid.uuid4().hex[:10]}{ext}"
        arquivo.save(os.path.join(pasta, secure_filename(nome_arquivo)))
    else:
        payload = request.get_json(silent=True) or {}
        foto_b64 = (payload.get("foto_base64") or "").strip()
        if foto_b64:
            try:
                bin_data = _decodificar_assinatura_png(foto_b64)
            except ValueError as exc:
                return jsonify({"error": str(exc)}), 400
            nome_arquivo = f"limp_{limpeza.id}_{uuid.uuid4().hex[:10]}.png"
            with open(os.path.join(pasta, secure_filename(nome_arquivo)), "wb") as fp:
                fp.write(bin_data)

    limpeza.concluido = True
    limpeza.concluido_em = datetime.now()
    limpeza.concluido_por = session.get("username") or ""
    if nome_arquivo:
        limpeza.evidencia_foto_path = nome_arquivo
    db.session.commit()
    return jsonify({"id": limpeza.id, "concluido": True, "tem_evidencia": bool(nome_arquivo)})


@facilities_bp.route("/api/facilities/limpezas/<int:id>/evidencia")
@login_required
def api_obter_evidencia_limpeza(id):
    """Serve a foto-evidencia da limpeza concluida."""
    limpeza = FacilitiesLimpeza.query.get_or_404(id)
    if not limpeza.evidencia_foto_path:
        return jsonify({"error": "Sem evidencia."}), 404
    caminho = os.path.join(_pasta_evidencias_limpeza(), limpeza.evidencia_foto_path)
    if not os.path.isfile(caminho):
        return jsonify({"error": "Arquivo nao encontrado."}), 404
    return send_file(caminho)


# ============================================================================
# API ADMIN - PROJETOS/OBRAS
# ============================================================================

@facilities_bp.route("/api/facilities/projetos")
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_listar_projetos():
    """Lista projetos/obras (Admin)"""
    status = request.args.get("status", "").lower()
    query = FacilitiesProjeto.query
    if status:
        query = query.filter_by(status=status)
    rows = query.order_by(FacilitiesProjeto.criado_em.desc()).all()
    return jsonify({
        "rows": [
            {
                "id": p.id,
                "codigo": f"{str(p.id).zfill(4)}-{p.criado_em.strftime('%m-%y')}",
                "nome": p.nome,
                "cliente_nome": p.cliente_nome or "",
                "cliente_telefone": p.cliente_telefone or "",
                "status": p.status,
                "criado_em": p.criado_em.strftime("%d/%m/%Y") if p.criado_em else "",
                "tarefas_total": len(p.tarefas),
                "tarefas_concluidas": len([t for t in p.tarefas if t.status == "concluido"]),
            }
            for p in rows
        ]
    })


@facilities_bp.route("/api/facilities/projetos", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_criar_projeto():
    """Cria novo projeto/obra (Admin)"""
    data = request.get_json() or {}
    nome = (data.get("nome") or "").strip()
    if not nome:
        return jsonify({"error": "Nome é obrigatório."}), 400
    
    projeto = FacilitiesProjeto(
        nome=nome,
        cliente_nome=data.get("cliente_nome", ""),
        cliente_telefone=data.get("cliente_telefone", ""),
        cliente_endereco=data.get("cliente_endereco", ""),
        observacoes=data.get("observacoes", ""),
    )
    db.session.add(projeto)
    db.session.commit()
    return jsonify({"id": projeto.id, "nome": projeto.nome})


@facilities_bp.route("/api/facilities/projetos/<int:id>", methods=["PUT"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_atualizar_projeto(id):
    """Atualiza projeto/obra (Admin)"""
    projeto = FacilitiesProjeto.query.get_or_404(id)
    data = request.get_json() or {}
    
    if "nome" in data:
        projeto.nome = data["nome"]
    if "cliente_nome" in data:
        projeto.cliente_nome = data["cliente_nome"]
    if "cliente_telefone" in data:
        projeto.cliente_telefone = data["cliente_telefone"]
    if "cliente_endereco" in data:
        projeto.cliente_endereco = data["cliente_endereco"]
    if "observacoes" in data:
        projeto.observacoes = data["observacoes"]
    if "status" in data:
        projeto.status = data["status"]
    
    db.session.commit()
    return jsonify({"id": projeto.id, "nome": projeto.nome, "status": projeto.status})


# ============================================================================
# API ADMIN - TAREFAS
# ============================================================================

@facilities_bp.route("/api/facilities/projetos/<int:projeto_id>/tarefas")
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_listar_tarefas(projeto_id):
    """Lista tarefas de um projeto (Admin)"""
    tarefas = FacilitiesTarefa.query.filter_by(projeto_id=projeto_id).order_by(FacilitiesTarefa.id).all()
    return jsonify({
        "rows": [
            {
                "id": t.id,
                "titulo": t.titulo,
                "local": t.local or "",
                "descricao": t.descricao or "",
                "status": t.status,
                "status_label": {
                    "nao_planejado": "Não Planejado",
                    "planejado": "Planejado",
                    "em_andamento": "Em Andamento",
                    "pausado": "Pausado",
                    "concluido": "Concluído",
                }.get(t.status, t.status),
                "observacao": t.observacao or "",
                "impedimento": t.impedimento or "",
                "foto_path": t.foto_path or "",
                "data_inicio_prevista": t.data_inicio_prevista.strftime("%Y-%m-%d") if t.data_inicio_prevista else "",
                "data_fim_prevista": t.data_fim_prevista.strftime("%Y-%m-%d") if t.data_fim_prevista else "",
            }
            for t in tarefas
        ]
    })


@facilities_bp.route("/api/facilities/projetos/<int:projeto_id>/tarefas", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_criar_tarefa(projeto_id):
    """Cria nova tarefa em um projeto (Admin)"""
    data = request.get_json() or {}
    titulo = (data.get("titulo") or "").strip()
    if not titulo:
        return jsonify({"error": "Título é obrigatório."}), 400
    
    tarefa = FacilitiesTarefa(
        projeto_id=projeto_id,
        titulo=titulo,
        local=data.get("local", ""),
        descricao=data.get("descricao", ""),
        status=data.get("status", "nao_planejado"),
    )
    if data.get("data_inicio_prevista"):
        tarefa.data_inicio_prevista = datetime.strptime(data["data_inicio_prevista"], "%Y-%m-%d").date()
    if data.get("data_fim_prevista"):
        tarefa.data_fim_prevista = datetime.strptime(data["data_fim_prevista"], "%Y-%m-%d").date()
    
    db.session.add(tarefa)
    db.session.commit()
    return jsonify({"id": tarefa.id, "titulo": tarefa.titulo})


@facilities_bp.route("/api/facilities/tarefas/<int:id>", methods=["PUT"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_atualizar_tarefa(id):
    """Atualiza tarefa (Admin)"""
    tarefa = FacilitiesTarefa.query.get_or_404(id)
    data = request.get_json() or {}
    
    if "titulo" in data:
        tarefa.titulo = data["titulo"]
    if "local" in data:
        tarefa.local = data["local"]
    if "descricao" in data:
        tarefa.descricao = data["descricao"]
    if "status" in data:
        tarefa.status = data["status"]
    if "observacao" in data:
        tarefa.observacao = data["observacao"]
    if "impedimento" in data:
        tarefa.impedimento = data["impedimento"]
    if "data_inicio_prevista" in data:
        tarefa.data_inicio_prevista = datetime.strptime(data["data_inicio_prevista"], "%Y-%m-%d").date() if data["data_inicio_prevista"] else None
    if "data_fim_prevista" in data:
        tarefa.data_fim_prevista = datetime.strptime(data["data_fim_prevista"], "%Y-%m-%d").date() if data["data_fim_prevista"] else None
    
    tarefa.atualizado_em = datetime.now()
    db.session.commit()
    return jsonify({"id": tarefa.id, "status": tarefa.status})


# ============================================================================
# ADMIN - COLABORADORES
# ============================================================================

@facilities_bp.route("/api/facilities/colaboradores/<int:id>", methods=["PUT"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_atualizar_colaborador(id):
    """Atualiza colaborador (Admin)"""
    colaborador = FacilitiesColaborador.query.get_or_404(id)
    data = request.get_json() or {}
    
    if "nome" in data:
        colaborador.nome = data["nome"]
    if "cargo" in data:
        colaborador.cargo = data["cargo"]
    if "setor" in data:
        colaborador.setor = data["setor"]
    if "telefone" in data:
        colaborador.telefone = data["telefone"]
    if "email" in data:
        colaborador.email = (data["email"] or "").strip() or None
    if "nivel_acesso" in data:
        colaborador.nivel_acesso = data["nivel_acesso"]
    if "ativo" in data:
        colaborador.ativo = data["ativo"]
    
    db.session.commit()
    return jsonify({"id": colaborador.id, "nome": colaborador.nome})


@facilities_bp.route("/api/facilities/colaboradores/<int:id>", methods=["DELETE"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_desativar_colaborador(id):
    """Desativa colaborador (Admin)"""
    colaborador = FacilitiesColaborador.query.get_or_404(id)
    colaborador.ativo = False
    db.session.commit()
    return jsonify({"id": colaborador.id, "ativo": False})


# ============================================================================
# ADMIN - DASHBOARD/MÉTRICAS
# ============================================================================

@facilities_bp.route("/api/facilities/dashboard")
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_dashboard():
    """Métricas para o painel admin"""
    from sqlalchemy import func
    
    epi_pendentes = FacilitiesEpiSolicitacao.query.filter_by(status="solicitado").count()
    epi_liberados_hoje = FacilitiesEpiSolicitacao.query.filter(
        FacilitiesEpiSolicitacao.status == "liberado",
        func.date(FacilitiesEpiSolicitacao.liberado_em) == datetime.now().date()
    ).count()
    
    limpezas_hoje = FacilitiesLimpeza.query.filter(
        FacilitiesLimpeza.data_agendada == datetime.now().date()
    ).count()
    limpezas_pendentes = FacilitiesLimpeza.query.filter(
        FacilitiesLimpeza.data_agendada == datetime.now().date(),
        FacilitiesLimpeza.concluido == False
    ).count()
    
    projetos_ativos = FacilitiesProjeto.query.filter_by(status="Em andamento").count()
    
    colaboradores_ativos = FacilitiesColaborador.query.filter_by(ativo=True).count()

    # Estoque baixo: itens cuja quantidade <= minima (e minima > 0).
    materiais_estoque_baixo = (
        FacilitiesEpiMaterial.query
        .filter(FacilitiesEpiMaterial.ativo == True)  # noqa: E712
        .filter(FacilitiesEpiMaterial.qtd_minima > 0)
        .filter(FacilitiesEpiMaterial.qtd_estoque <= FacilitiesEpiMaterial.qtd_minima)
        .count()
    )

    # Vencimentos NR-6
    hoje = date.today()
    em_30_dias = hoje + timedelta(days=30)
    epi_vencido = FacilitiesEpiSolicitacao.query.filter(
        FacilitiesEpiSolicitacao.status == "retirado",
        FacilitiesEpiSolicitacao.proxima_troca_em.isnot(None),
        FacilitiesEpiSolicitacao.proxima_troca_em < hoje,
    ).count()
    epi_vence_30d = FacilitiesEpiSolicitacao.query.filter(
        FacilitiesEpiSolicitacao.status == "retirado",
        FacilitiesEpiSolicitacao.proxima_troca_em.isnot(None),
        FacilitiesEpiSolicitacao.proxima_troca_em >= hoje,
        FacilitiesEpiSolicitacao.proxima_troca_em <= em_30_dias,
    ).count()

    # Retiradas pendentes (aprovadas ha >3 dias sem retirar)
    tres_dias_atras = datetime.now() - timedelta(days=3)
    retiradas_pendentes = FacilitiesEpiSolicitacao.query.filter(
        FacilitiesEpiSolicitacao.status == "liberado",
        FacilitiesEpiSolicitacao.liberado_em < tres_dias_atras,
    ).count()

    # Top 5 itens solicitados ultimos 30 dias
    trinta_dias_atras = datetime.now() - timedelta(days=30)
    top_itens = (
        db.session.query(
            FacilitiesEpiSolicitacao.nome_item,
            func.sum(FacilitiesEpiSolicitacao.quantidade).label("qtd"),
        )
        .filter(FacilitiesEpiSolicitacao.solicitado_em >= trinta_dias_atras)
        .filter(FacilitiesEpiSolicitacao.status != "cancelado")
        .group_by(FacilitiesEpiSolicitacao.nome_item)
        .order_by(func.sum(FacilitiesEpiSolicitacao.quantidade).desc())
        .limit(5)
        .all()
    )

    # Solicitacoes por dia (ultimos 14 dias)
    quatorze_dias = datetime.now() - timedelta(days=14)
    por_dia_raw = (
        db.session.query(
            func.date(FacilitiesEpiSolicitacao.solicitado_em).label("dia"),
            func.count(FacilitiesEpiSolicitacao.id).label("qtd"),
        )
        .filter(FacilitiesEpiSolicitacao.solicitado_em >= quatorze_dias)
        .group_by(func.date(FacilitiesEpiSolicitacao.solicitado_em))
        .all()
    )
    por_dia_map = {str(r.dia): r.qtd for r in por_dia_raw}
    por_dia = []
    for i in range(13, -1, -1):
        d = (datetime.now().date() - timedelta(days=i))
        por_dia.append({"dia": d.strftime("%d/%m"), "qtd": por_dia_map.get(str(d), 0)})

    # Tempo medio aprovacao (horas)
    tempo_medio = (
        db.session.query(
            func.avg(
                (func.julianday(FacilitiesEpiSolicitacao.liberado_em) - func.julianday(FacilitiesEpiSolicitacao.solicitado_em)) * 24
            )
        )
        .filter(FacilitiesEpiSolicitacao.status.in_(["liberado", "retirado", "negado"]))
        .filter(FacilitiesEpiSolicitacao.liberado_em.isnot(None))
        .scalar()
    ) if db.engine.dialect.name == "sqlite" else None
    if tempo_medio is None and db.engine.dialect.name != "sqlite":
        # MySQL
        try:
            tempo_medio = db.session.execute(
                db.text(
                    "SELECT AVG(TIMESTAMPDIFF(MINUTE, solicitado_em, liberado_em))/60 "
                    "FROM facilities_epi_solicitacao WHERE liberado_em IS NOT NULL"
                )
            ).scalar()
        except Exception:
            tempo_medio = None

    return jsonify({
        "epi_pendentes": epi_pendentes,
        "epi_liberados_hoje": epi_liberados_hoje,
        "limpezas_hoje": limpezas_hoje,
        "limpezas_pendentes": limpezas_pendentes,
        "projetos_ativos": projetos_ativos,
        "colaboradores_ativos": colaboradores_ativos,
        "colaboradores": colaboradores_ativos,
        "materiais_estoque_baixo": materiais_estoque_baixo,
        "estoque_critico": FacilitiesEstoqueItem.query.filter(
            FacilitiesEstoqueItem.quantidade <= FacilitiesEstoqueItem.qtd_minima
        ).count(),
        "chamados_abertos": FacilitiesChamado.query.filter(
            FacilitiesChamado.status.in_(["aberto", "em_analise", "aprovado", "em_execucao"])
        ).count(),
        "epi_vencido": epi_vencido,
        "epi_vence_30d": epi_vence_30d,
        "retiradas_pendentes": retiradas_pendentes,
        "ultimas_solicitacoes": [
            {
                "id": s.id,
                "colaborador_nome": s.colaborador.nome if s.colaborador else "-",
                "nome_item": s.nome_item,
                "solicitante_nome": s.solicitante_nome or "-",
                "solicitado_em": s.solicitado_em.strftime("%d/%m/%Y") if s.solicitado_em else "",
                "status": s.status,
            }
            for s in FacilitiesEpiSolicitacao.query
                .order_by(FacilitiesEpiSolicitacao.solicitado_em.desc())
                .limit(10).all()
        ],
        "top_itens": [{"nome": r.nome_item, "qtd": int(r.qtd)} for r in top_itens],
        "por_dia": por_dia,
        "tempo_medio_aprovacao_h": round(float(tempo_medio), 1) if tempo_medio else None,
    })


# ============================================================================
# BADGE (contador leve)
# ============================================================================

@facilities_bp.route("/api/facilities/badge")
@login_required
def api_facilities_badge():
    """Contadores leves para alimentar badge no menu (polling)."""
    pendentes = FacilitiesEpiSolicitacao.query.filter_by(status="solicitado").count()
    retiradas = FacilitiesEpiSolicitacao.query.filter_by(status="liberado").count()
    hoje = date.today()
    em_30 = hoje + timedelta(days=30)
    epi_vencido = FacilitiesEpiSolicitacao.query.filter(
        FacilitiesEpiSolicitacao.status == "retirado",
        FacilitiesEpiSolicitacao.proxima_troca_em.isnot(None),
        FacilitiesEpiSolicitacao.proxima_troca_em < hoje,
    ).count()
    epi_vence_30d = FacilitiesEpiSolicitacao.query.filter(
        FacilitiesEpiSolicitacao.status == "retirado",
        FacilitiesEpiSolicitacao.proxima_troca_em.isnot(None),
        FacilitiesEpiSolicitacao.proxima_troca_em >= hoje,
        FacilitiesEpiSolicitacao.proxima_troca_em <= em_30,
    ).count()
    chamados_abertos = FacilitiesChamado.query.filter(
        FacilitiesChamado.status.in_(["aberto", "em_analise", "aprovado", "em_execucao"])
    ).count()
    estoque_critico = FacilitiesEstoqueItem.query.filter(
        FacilitiesEstoqueItem.quantidade <= FacilitiesEstoqueItem.qtd_minima
    ).count()
    return jsonify({
        "epi_pendentes": pendentes,
        "retiradas_pendentes": retiradas,
        "epi_vencido": epi_vencido,
        "epi_vence_30d": epi_vence_30d,
        "chamados_abertos": chamados_abertos,
        "estoque_critico": estoque_critico,
    })


# ============================================================================
# ALERTAS NR-6 - Vencimentos
# ============================================================================

@facilities_bp.route("/api/facilities/epi/alertas-vencimento")
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_alertas_vencimento_epi():
    """Retorna EPIs vencidos ou proximos do vencimento (30 dias)."""
    hoje = date.today()
    em_30 = hoje + timedelta(days=30)
    rows = (
        FacilitiesEpiSolicitacao.query
        .filter(FacilitiesEpiSolicitacao.status == "retirado")
        .filter(FacilitiesEpiSolicitacao.proxima_troca_em.isnot(None))
        .filter(FacilitiesEpiSolicitacao.proxima_troca_em <= em_30)
        .order_by(FacilitiesEpiSolicitacao.proxima_troca_em.asc())
        .limit(500)
        .all()
    )
    return jsonify({
        "rows": [
            {
                "id": s.id,
                "colaborador_id": s.colaborador_id,
                "colaborador_nome": s.colaborador.nome if s.colaborador else "",
                "setor": (s.colaborador.setor if s.colaborador else "") or "",
                "nome_item": s.nome_item,
                "codigo_item": s.codigo_item,
                "retirado_em": s.retirado_em.strftime("%d/%m/%Y") if s.retirado_em else "",
                "proxima_troca_em": s.proxima_troca_em.strftime("%d/%m/%Y") if s.proxima_troca_em else "",
                "dias_restantes": (s.proxima_troca_em - hoje).days if s.proxima_troca_em else None,
                "situacao": ("vencido" if s.proxima_troca_em and s.proxima_troca_em < hoje else "vence_em_30d"),
            }
            for s in rows
        ]
    })


@facilities_bp.route("/api/facilities/epi/enviar-lembretes", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_enviar_lembretes_epi():
    """Envia email para colaboradores cujas solicitacoes foram liberadas ha >3 dias
    e ainda nao retiradas. Idempotente: marca lembrete_retirada_enviado_em."""
    try:
        from ..services.email_service import enviar_email_async
    except Exception:
        enviar_email_async = None

    tres_dias = datetime.now() - timedelta(days=3)
    sols = (
        FacilitiesEpiSolicitacao.query
        .filter(FacilitiesEpiSolicitacao.status == "liberado")
        .filter(FacilitiesEpiSolicitacao.liberado_em < tres_dias)
        .filter(FacilitiesEpiSolicitacao.lembrete_retirada_enviado_em.is_(None))
        .all()
    )
    enviados = 0
    for s in sols:
        email = (s.colaborador.email if s.colaborador else None)
        if email and enviar_email_async:
            try:
                enviar_email_async(
                    destinatario=email,
                    assunto=f"[Facilities] Retire seu EPI: {s.nome_item}",
                    corpo_html=(
                        f"<p>Ola {s.colaborador.nome},</p>"
                        f"<p>Sua solicitacao do item <b>{s.nome_item}</b> foi liberada em "
                        f"{s.liberado_em.strftime('%d/%m/%Y')} e ainda nao foi retirada.</p>"
                        f"<p>Favor comparecer ao almoxarifado para retirada e assinatura.</p>"
                    ),
                )
            except Exception:
                pass
        s.lembrete_retirada_enviado_em = datetime.now()
        enviados += 1
    db.session.commit()
    _audit("epi_solicitacao", None, "lembretes_enviados", f"qtd={enviados}")
    return jsonify({"enviados": enviados})


# ============================================================================
# FICHA PDF NR-6
# ============================================================================

def _gerar_ficha_pdf_bytes(solicitacoes, colaborador):
    """Gera PDF NR-6 em memoria com ReportLab. Recebe lista de solicitacoes."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    )
    import io

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            rightMargin=1.5 * cm, leftMargin=1.5 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    titulo_style = ParagraphStyle("titulo", parent=styles["Heading1"],
                                   fontSize=16, alignment=1, spaceAfter=12)
    story = []
    story.append(Paragraph("FICHA DE CONTROLE DE EPI - NR-6", titulo_style))
    story.append(Paragraph(
        f"<b>Colaborador:</b> {colaborador.nome}<br/>"
        f"<b>Cargo:</b> {colaborador.cargo or '-'} &nbsp;&nbsp; "
        f"<b>Setor:</b> {colaborador.setor or '-'}<br/>"
        f"<b>Emitido em:</b> {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        styles["Normal"]
    ))
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph(
        "Declaro ter recebido o(s) equipamento(s) abaixo, fui orientado(a) quanto ao uso, "
        "guarda e conservacao, e me comprometo a utiliza-lo(s) apenas para as finalidades a que se destina, "
        "conforme determina a NR-6 e a CLT (art. 158, paragrafo unico).",
        styles["Normal"]
    ))
    story.append(Spacer(1, 0.4 * cm))

    headers = ["Data", "Item", "CA", "Qtd", "Proxima Troca", "Status"]
    data_tbl = [headers]
    for s in solicitacoes:
        data_tbl.append([
            s.retirado_em.strftime("%d/%m/%Y") if s.retirado_em else (s.solicitado_em.strftime("%d/%m/%Y") if s.solicitado_em else ""),
            f"{s.codigo_item} - {s.nome_item}"[:50],
            s.numero_ca_entregue or "-",
            str(s.quantidade),
            s.proxima_troca_em.strftime("%d/%m/%Y") if s.proxima_troca_em else "-",
            s.status.upper(),
        ])
    tbl = Table(data_tbl, colWidths=[2.2 * cm, 6.5 * cm, 2 * cm, 1.3 * cm, 2.8 * cm, 2.5 * cm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("ALIGN", (3, 1), (3, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 1 * cm))

    # Assinatura (se houver 1 solicitacao e ela tiver assinatura)
    if len(solicitacoes) == 1 and solicitacoes[0].assinatura_path:
        caminho = os.path.join(_pasta_assinaturas(), solicitacoes[0].assinatura_path)
        if os.path.exists(caminho):
            try:
                story.append(Paragraph("<b>Assinatura do colaborador:</b>", styles["Normal"]))
                story.append(Image(caminho, width=8 * cm, height=3 * cm))
            except Exception:
                pass

    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(
        "_________________________________________<br/>"
        f"{colaborador.nome}<br/>"
        "Assinatura do Colaborador",
        styles["Normal"]
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer


@facilities_bp.route("/api/facilities/epi-solicitacoes/<int:id>/ficha-pdf")
@login_required
def api_ficha_pdf_solicitacao(id):
    """PDF NR-6 de uma retirada especifica."""
    s = FacilitiesEpiSolicitacao.query.get_or_404(id)
    if not s.colaborador:
        return jsonify({"error": "Sem colaborador."}), 400
    buf = _gerar_ficha_pdf_bytes([s], s.colaborador)
    return send_file(buf, as_attachment=True, download_name=f"ficha_epi_{s.id}.pdf",
                     mimetype="application/pdf")


@facilities_bp.route("/api/facilities/colaboradores/<int:id>/ficha-epi-pdf")
@login_required
def api_ficha_pdf_colaborador(id):
    """PDF NR-6 completo do colaborador (historico de retiradas)."""
    colab = FacilitiesColaborador.query.get_or_404(id)
    sols = (
        FacilitiesEpiSolicitacao.query
        .filter_by(colaborador_id=id)
        .filter(FacilitiesEpiSolicitacao.status.in_(["retirado", "liberado"]))
        .order_by(FacilitiesEpiSolicitacao.solicitado_em.desc())
        .all()
    )
    if not sols:
        return jsonify({"error": "Nenhuma retirada encontrada."}), 404
    buf = _gerar_ficha_pdf_bytes(sols, colab)
    return send_file(buf, as_attachment=True,
                     download_name=f"ficha_epi_{colab.nome.replace(' ', '_')}.pdf",
                     mimetype="application/pdf")


# ============================================================================
# AUDITORIA
# ============================================================================

@facilities_bp.route("/api/facilities/audit-log")
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_listar_audit_log():
    """Lista log de auditoria (filtros: entidade, usuario, data_ini/fim)."""
    entidade = (request.args.get("entidade") or "").strip()
    usuario = (request.args.get("usuario") or "").strip()
    data_ini = request.args.get("data_ini") or ""
    data_fim = request.args.get("data_fim") or ""
    limit = request.args.get("limit", type=int) or 300

    q = FacilitiesAuditLog.query
    if entidade:
        q = q.filter_by(entidade=entidade)
    if usuario:
        q = q.filter(FacilitiesAuditLog.usuario.like(f"%{usuario}%"))
    if data_ini:
        try:
            dt = datetime.strptime(data_ini, "%Y-%m-%d")
            q = q.filter(FacilitiesAuditLog.ts >= dt)
        except ValueError:
            pass
    if data_fim:
        try:
            dt = datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1)
            q = q.filter(FacilitiesAuditLog.ts < dt)
        except ValueError:
            pass
    rows = q.order_by(FacilitiesAuditLog.ts.desc()).limit(limit).all()
    return jsonify({
        "rows": [
            {
                "id": r.id,
                "ts": r.ts.strftime("%d/%m/%Y %H:%M:%S") if r.ts else "",
                "usuario": r.usuario or "",
                "entidade": r.entidade or "",
                "entidade_id": r.entidade_id,
                "acao": r.acao or "",
                "detalhes": r.detalhes or "",
                "ip": r.ip or "",
            }
            for r in rows
        ]
    })


# ============================================================================
# RELATÓRIO DE CONSUMO POR SETOR
# ============================================================================

@facilities_bp.route("/api/facilities/relatorio-consumo")
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_relatorio_consumo():
    """Consumo de EPI/Uniforme agrupado por setor nos últimos N dias.
    Query params: dias (default 90), tipo (epi|uniforme|'')
    """
    dias  = request.args.get("dias", 90, type=int)
    tipo  = (request.args.get("tipo") or "").lower()
    desde = datetime.now() - timedelta(days=max(1, dias))

    q = (
        FacilitiesEpiSolicitacao.query
        .filter(FacilitiesEpiSolicitacao.status == "retirado")
        .filter(FacilitiesEpiSolicitacao.retirado_em >= desde)
    )
    if tipo in ("epi", "uniforme"):
        q = q.filter_by(tipo=tipo)

    sols = q.all()

    # Agrupa por setor
    from collections import defaultdict
    por_setor: dict = defaultdict(lambda: {"quantidade": 0, "itens": defaultdict(int)})
    for s in sols:
        setor = (s.colaborador.setor if s.colaborador and s.colaborador.setor else "Sem setor")
        por_setor[setor]["quantidade"] += s.quantidade or 1
        por_setor[setor]["itens"][s.nome_item] += s.quantidade or 1

    resultado = []
    for setor, dados in sorted(por_setor.items(), key=lambda x: -x[1]["quantidade"]):
        top_itens = sorted(dados["itens"].items(), key=lambda x: -x[1])[:5]
        resultado.append({
            "setor": setor,
            "quantidade_total": dados["quantidade"],
            "top_itens": [{"nome": n, "qtd": q} for n, q in top_itens],
        })

    return jsonify({
        "periodo_dias": dias,
        "desde": desde.strftime("%d/%m/%Y"),
        "total_retiradas": len(sols),
        "por_setor": resultado,
    })


# ============================================================================
# CICLOS DE TROCA EPI (CRUD)
# ============================================================================

@facilities_bp.route("/api/facilities/ciclos-troca", methods=["GET"])
@login_required
def api_listar_ciclos_troca():
    rows = FacilitiesEpiCicloTroca.query.order_by(FacilitiesEpiCicloTroca.palavra_chave.asc()).all()
    return jsonify({
        "rows": [
            {
                "id": c.id,
                "codigo_interno": c.codigo_interno or "",
                "palavra_chave": c.palavra_chave or "",
                "meses_validade": c.meses_validade,
                "descricao": c.descricao or "",
                "ativo": bool(c.ativo),
            }
            for c in rows
        ]
    })


@facilities_bp.route("/api/facilities/ciclos-troca", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_criar_ciclo_troca():
    d = request.get_json() or {}
    pk = (d.get("palavra_chave") or "").strip().upper()
    if not pk:
        return jsonify({"error": "palavra_chave obrigatoria"}), 400
    c = FacilitiesEpiCicloTroca(
        codigo_interno=(d.get("codigo_interno") or "").strip() or None,
        palavra_chave=pk,
        meses_validade=int(d.get("meses_validade") or 6),
        descricao=(d.get("descricao") or "").strip() or None,
        ativo=bool(d.get("ativo", True)),
    )
    db.session.add(c)
    db.session.commit()
    _audit("ciclo_troca", c.id, "criar", f"{pk}/{c.meses_validade}m")
    return jsonify({"id": c.id}), 201


@facilities_bp.route("/api/facilities/ciclos-troca/<int:id>", methods=["PUT"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_atualizar_ciclo_troca(id):
    c = FacilitiesEpiCicloTroca.query.get_or_404(id)
    d = request.get_json() or {}
    if "palavra_chave" in d:
        c.palavra_chave = (d["palavra_chave"] or "").strip().upper()
    if "codigo_interno" in d:
        c.codigo_interno = (d["codigo_interno"] or "").strip() or None
    if "meses_validade" in d:
        c.meses_validade = int(d["meses_validade"] or 6)
    if "descricao" in d:
        c.descricao = (d["descricao"] or "").strip() or None
    if "ativo" in d:
        c.ativo = bool(d["ativo"])
    db.session.commit()
    _audit("ciclo_troca", c.id, "atualizar")
    return jsonify({"id": c.id})


@facilities_bp.route("/api/facilities/ciclos-troca/<int:id>", methods=["DELETE"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_deletar_ciclo_troca(id):
    c = FacilitiesEpiCicloTroca.query.get_or_404(id)
    db.session.delete(c)
    db.session.commit()
    _audit("ciclo_troca", id, "deletar")
    return jsonify({"ok": True})


# ============================================================================
# LIMPEZA - TEMPLATES / CHECKLIST / QR
# ============================================================================

@facilities_bp.route("/api/facilities/limpeza-templates", methods=["GET"])
@login_required
def api_listar_limpeza_templates():
    ativo = request.args.get("ativo")
    q = FacilitiesLimpezaTemplate.query
    if ativo in ("1", "true"):
        q = q.filter_by(ativo=True)
    rows = q.order_by(FacilitiesLimpezaTemplate.nome.asc()).all()
    return jsonify({
        "rows": [
            {
                "id": t.id,
                "nome": t.nome,
                "local": t.local or "",
                "recorrencia": t.recorrencia,
                "dias_semana": t.dias_semana or "",
                "hora_inicio": t.hora_inicio or "",
                "hora_fim": t.hora_fim or "",
                "colaborador_id": t.colaborador_id,
                "colaborador_nome": t.colaborador.nome if t.colaborador else "",
                "checklist_json": t.checklist_json or "[]",
                "qr_code": t.qr_code,
                "ativo": bool(t.ativo),
            }
            for t in rows
        ]
    })


@facilities_bp.route("/api/facilities/limpeza-templates", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_criar_limpeza_template():
    import json as _json
    d = request.get_json() or {}
    nome = (d.get("nome") or "").strip()
    if not nome:
        return jsonify({"error": "nome obrigatorio"}), 400

    hora_inicio = (d.get("hora_inicio") or "").strip()[:5] or None
    hora_fim = (d.get("hora_fim") or "").strip()[:5] or None

    checklist = d.get("checklist") or []
    if isinstance(checklist, list):
        checklist_json = _json.dumps(checklist, ensure_ascii=False)
    else:
        checklist_json = "[]"

    t = FacilitiesLimpezaTemplate(
        nome=nome,
        local=(d.get("local") or "").strip() or None,
        recorrencia=(d.get("recorrencia") or "diaria").strip().lower(),
        dias_semana=(d.get("dias_semana") or "").strip() or None,
        hora_inicio=hora_inicio,
        hora_fim=hora_fim,
        colaborador_id=d.get("colaborador_id") or None,
        checklist_json=checklist_json,
        qr_code=uuid.uuid4().hex[:16],
        ativo=bool(d.get("ativo", True)),
    )
    db.session.add(t)
    db.session.commit()
    _audit("limpeza_template", t.id, "criar", nome)
    return jsonify({"id": t.id, "qr_code": t.qr_code}), 201


@facilities_bp.route("/api/facilities/limpeza-templates/<int:id>", methods=["PUT"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_atualizar_limpeza_template(id):
    import json as _json
    t = FacilitiesLimpezaTemplate.query.get_or_404(id)
    d = request.get_json() or {}
    for f in ("nome", "local", "recorrencia", "dias_semana"):
        if f in d:
            setattr(t, f, (d[f] or "").strip() or None if f != "nome" else (d[f] or t.nome))
    if "hora_inicio" in d:
        t.hora_inicio = (d["hora_inicio"] or "").strip()[:5] or None
    if "hora_fim" in d:
        t.hora_fim = (d["hora_fim"] or "").strip()[:5] or None
    if "colaborador_id" in d:
        t.colaborador_id = d["colaborador_id"] or None
    if "checklist" in d and isinstance(d["checklist"], list):
        t.checklist_json = _json.dumps(d["checklist"], ensure_ascii=False)
    if "ativo" in d:
        t.ativo = bool(d["ativo"])
    db.session.commit()
    _audit("limpeza_template", t.id, "atualizar")
    return jsonify({"id": t.id})


@facilities_bp.route("/api/facilities/limpeza-templates/<int:id>", methods=["DELETE"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_deletar_limpeza_template(id):
    t = FacilitiesLimpezaTemplate.query.get_or_404(id)
    db.session.delete(t)
    db.session.commit()
    _audit("limpeza_template", id, "deletar")
    return jsonify({"ok": True})


@facilities_bp.route("/api/facilities/limpezas/gerar-da-semana", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_gerar_limpezas_da_semana():
    """Cria agendamentos dos templates ativos para os proximos 7 dias (idempotente)."""
    templates = FacilitiesLimpezaTemplate.query.filter_by(ativo=True).all()
    criados = 0
    hoje = date.today()
    for i in range(7):
        dia = hoje + timedelta(days=i)
        # isoweekday: 1=seg..7=dom
        iw = dia.isoweekday()
        for t in templates:
            should = False
            if t.recorrencia == "diaria":
                should = True
            elif t.recorrencia == "semanal":
                dias = [int(x) for x in (t.dias_semana or "").split(",") if x.strip().isdigit()]
                should = iw in dias
            elif t.recorrencia == "quinzenal":
                should = (iw == 1) and (dia.isocalendar()[1] % 2 == 0)
            elif t.recorrencia == "mensal":
                should = (dia.day == 1)
            if not should:
                continue
            # checar se ja existe agendamento daquele template para aquele dia
            ja = (FacilitiesLimpeza.query
                  .filter_by(data_agendada=dia, local=t.local or t.nome,
                             responsavel_id=t.colaborador_id)
                  .first())
            if ja:
                continue
            L = FacilitiesLimpeza(
                local=t.local or t.nome,
                descricao=f"[auto] Template: {t.nome}",
                data_agendada=dia,
                responsavel_id=t.colaborador_id,
                concluido=False,
            )
            db.session.add(L)
            criados += 1
    db.session.commit()
    _audit("limpeza", None, "gerar_semana", f"criados={criados}")
    return jsonify({"criados": criados})


@facilities_bp.route("/facilities/qr/<token>")
def facilities_qr_template(token):
    """Landing para QR Code de template de limpeza - abre pagina de conclusao."""
    t = FacilitiesLimpezaTemplate.query.filter_by(qr_code=token).first_or_404()
    return render_template("facilities_limpeza_qr.html", template=t)


# ============================================================================
# KANBAN - TAREFAS DE PROJETO
# ============================================================================

@facilities_bp.route("/api/facilities/projetos/<int:projeto_id>/tarefas-kanban", methods=["GET"])
@login_required
def api_listar_tarefas_kanban(projeto_id):
    rows = (FacilitiesProjetoTarefa.query
            .filter_by(projeto_id=projeto_id)
            .order_by(FacilitiesProjetoTarefa.status.asc(), FacilitiesProjetoTarefa.ordem.asc())
            .all())
    return jsonify({
        "rows": [
            {
                "id": t.id,
                "projeto_id": t.projeto_id,
                "titulo": t.titulo,
                "descricao": t.descricao or "",
                "status": t.status,
                "ordem": t.ordem,
                "responsavel_id": t.responsavel_id,
                "responsavel_nome": t.responsavel.nome if t.responsavel else "",
                "data_inicio": t.data_inicio.strftime("%d/%m/%Y") if t.data_inicio else "",
                "data_fim": t.data_fim.strftime("%d/%m/%Y") if t.data_fim else "",
                "impedimento": t.impedimento or "",
                "impedimento_em": t.impedimento_em.strftime("%d/%m/%Y %H:%M") if t.impedimento_em else "",
                "foto_path": t.foto_path or "",
                "concluido_em": t.concluido_em.strftime("%d/%m/%Y %H:%M") if t.concluido_em else "",
            }
            for t in rows
        ]
    })


@facilities_bp.route("/api/facilities/projetos/<int:projeto_id>/tarefas-kanban", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_criar_tarefa_kanban(projeto_id):
    FacilitiesProjeto.query.get_or_404(projeto_id)
    d = request.get_json() or {}
    titulo = (d.get("titulo") or "").strip()
    if not titulo:
        return jsonify({"error": "titulo obrigatorio"}), 400
    def _dt(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date() if s else None
        except ValueError:
            return None
    t = FacilitiesProjetoTarefa(
        projeto_id=projeto_id,
        titulo=titulo,
        descricao=(d.get("descricao") or "").strip() or None,
        status=(d.get("status") or "nao_planejado").strip().lower(),
        ordem=int(d.get("ordem") or 0),
        responsavel_id=d.get("responsavel_id") or None,
        data_inicio=_dt(d.get("data_inicio")),
        data_fim=_dt(d.get("data_fim")),
    )
    db.session.add(t)
    db.session.commit()
    _audit("projeto_tarefa", t.id, "criar", f"projeto={projeto_id}")
    return jsonify({"id": t.id}), 201


@facilities_bp.route("/api/facilities/tarefas-kanban/<int:id>", methods=["PUT"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_atualizar_tarefa_kanban(id):
    t = FacilitiesProjetoTarefa.query.get_or_404(id)
    d = request.get_json() or {}
    def _dt(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date() if s else None
        except ValueError:
            return None
    if "titulo" in d:
        t.titulo = (d["titulo"] or t.titulo).strip()
    if "descricao" in d:
        t.descricao = (d["descricao"] or "").strip() or None
    if "status" in d:
        novo = (d["status"] or "").strip().lower()
        if novo and novo != t.status:
            t.status = novo
            if novo == "concluido":
                t.concluido_em = datetime.now()
    if "ordem" in d:
        t.ordem = int(d["ordem"] or 0)
    if "responsavel_id" in d:
        t.responsavel_id = d["responsavel_id"] or None
    if "data_inicio" in d:
        t.data_inicio = _dt(d["data_inicio"])
    if "data_fim" in d:
        t.data_fim = _dt(d["data_fim"])
    t.atualizado_em = datetime.now()
    db.session.commit()
    _audit("projeto_tarefa", t.id, "atualizar", f"status={t.status}")
    return jsonify({"id": t.id, "status": t.status})


@facilities_bp.route("/api/facilities/tarefas-kanban/<int:id>", methods=["DELETE"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_deletar_tarefa_kanban(id):
    t = FacilitiesProjetoTarefa.query.get_or_404(id)
    db.session.delete(t)
    db.session.commit()
    _audit("projeto_tarefa", id, "deletar")
    return jsonify({"ok": True})


@facilities_bp.route("/api/facilities/tarefas-kanban/<int:id>/impedimento", methods=["POST"])
@login_required
def api_registrar_impedimento(id):
    """Registra impedimento numa tarefa: descricao + foto opcional (base64)."""
    t = FacilitiesProjetoTarefa.query.get_or_404(id)
    d = request.get_json() or {}
    descricao = (d.get("descricao") or "").strip()
    if not descricao:
        return jsonify({"error": "descricao obrigatoria"}), 400
    foto_b64 = d.get("foto_base64") or ""
    nome_foto = None
    if foto_b64:
        try:
            raw = _decodificar_assinatura_png(foto_b64)
            nome_foto = f"tarefa_{id}_{uuid.uuid4().hex[:8]}.png"
            pasta = os.path.join(current_app.instance_path, "facilities", "impedimentos")
            os.makedirs(pasta, exist_ok=True)
            with open(os.path.join(pasta, nome_foto), "wb") as fh:
                fh.write(raw)
        except Exception:
            nome_foto = None
    t.impedimento = descricao
    t.impedimento_em = datetime.now()
    if nome_foto:
        t.foto_path = nome_foto
    if t.status != "concluido":
        t.status = "impedimento"
    db.session.commit()
    _audit("projeto_tarefa", t.id, "impedimento", descricao[:200])
    return jsonify({"id": t.id, "status": t.status, "foto": nome_foto})


# ============================================================================
# TELA DE RETIRADA (colaborador/QR)
# ============================================================================

@facilities_bp.route("/facilities/retirar/<int:id>")
@login_required
def pagina_retirar_epi(id):
    """Pagina dedicada para retirada com assinatura (signature_pad)."""
    s = FacilitiesEpiSolicitacao.query.get_or_404(id)
    return render_template("facilities_retirar_epi.html", solicitacao=s)


# ============================================================================
# FICHA IMPRIMÍVEL (HTML — abre nova aba, Ctrl+P para imprimir)
# ============================================================================

@facilities_bp.route("/facilities/ficha-epi/<int:id>")
@login_required
def pagina_ficha_epi(id):
    """Ficha NR-6 imprimível em HTML. Disponível para qualquer status."""
    from datetime import datetime as _dt
    from flask import current_app
    s = FacilitiesEpiSolicitacao.query.get_or_404(id)
    assinatura_url = None
    if s.assinatura_path:
        assinatura_url = f"/api/facilities/epi-solicitacoes/{s.id}/assinatura"
    agora = _dt.now().strftime("%d/%m/%Y %H:%M")
    empresa_nome = current_app.config.get("EMPRESA_NOME", "Columbia")
    empresa_cnpj = current_app.config.get("EMPRESA_CNPJ", "")
    logo_url = current_app.config.get("EMPRESA_LOGO_URL", "")
    # Se for um caminho relativo (sem http), prefixamos /static/
    if logo_url and not logo_url.startswith("http"):
        logo_url = f"/static/{logo_url.lstrip('/')}"
    return render_template(
        "facilities_ficha_epi.html",
        sol=s,
        assinatura_url=assinatura_url,
        agora=agora,
        empresa_nome=empresa_nome,
        empresa_cnpj=empresa_cnpj,
        logo_url=logo_url,
    )


# ============================================================================
# ESTOQUE EPI / UNIFORME  (novo módulo)
# ============================================================================

@facilities_bp.route("/api/facilities/estoque", methods=["GET"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_listar_estoque():
    busca = (request.args.get("busca") or "").strip()
    status_filter = (request.args.get("status") or "").lower()

    q = (
        FacilitiesEstoqueItem.query
        .join(FacilitiesEstoqueItem.material, isouter=True)
        .order_by(FacilitiesEpiMaterial.nome.asc())
    )
    if busca:
        q = q.filter(FacilitiesEpiMaterial.nome.ilike(f"%{busca}%"))
    if status_filter == "baixo":
        q = q.filter(
            FacilitiesEstoqueItem.quantidade > 0,
            FacilitiesEstoqueItem.quantidade < FacilitiesEstoqueItem.qtd_minima,
        )
    elif status_filter == "zerado":
        q = q.filter(FacilitiesEstoqueItem.quantidade <= 0)

    rows = q.all()
    return jsonify({
        "rows": [
            {
                "id": r.id,
                "material_id": r.material_id,
                "material_nome": r.material.nome if r.material else "",
                "tipo": r.material.tipo if r.material else "",
                "numero_ca": r.numero_ca or "",
                "lote": r.lote or "",
                "data_validade": r.data_validade.strftime("%Y-%m-%d") if r.data_validade else "",
                "localizacao": r.localizacao or "",
                "quantidade": r.quantidade,
                "qtd_minima": r.qtd_minima,
            }
            for r in rows
        ]
    })


@facilities_bp.route("/api/facilities/estoque/<int:id>", methods=["GET"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_obter_estoque_item(id):
    r = FacilitiesEstoqueItem.query.get_or_404(id)
    return jsonify({
        "id": r.id,
        "material_id": r.material_id,
        "numero_ca": r.numero_ca or "",
        "lote": r.lote or "",
        "data_validade": r.data_validade.strftime("%Y-%m-%d") if r.data_validade else "",
        "localizacao": r.localizacao or "",
        "quantidade": r.quantidade,
        "qtd_minima": r.qtd_minima,
    })


@facilities_bp.route("/api/facilities/estoque", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_criar_estoque_item():
    d = request.get_json() or {}
    mat_id = d.get("material_id")
    if not mat_id:
        return jsonify({"error": "material_id obrigatório"}), 400
    validade = None
    if d.get("data_validade"):
        try:
            from datetime import datetime as _dt2
            validade = _dt2.strptime(d["data_validade"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            pass
    item = FacilitiesEstoqueItem(
        material_id=int(mat_id),
        numero_ca=(d.get("numero_ca") or "").strip() or None,
        lote=(d.get("lote") or "").strip() or None,
        data_validade=validade,
        localizacao=(d.get("localizacao") or "").strip() or None,
        quantidade=max(0, int(d.get("quantidade") or 0)),
        qtd_minima=max(0, int(d.get("qtd_minima") or 5)),
    )
    db.session.add(item)
    db.session.commit()
    _audit("estoque_item", item.id, "criar", f"mat={mat_id}, qtd={item.quantidade}")
    return jsonify({"id": item.id}), 201


@facilities_bp.route("/api/facilities/estoque/<int:id>", methods=["PUT"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_atualizar_estoque_item(id):
    item = FacilitiesEstoqueItem.query.get_or_404(id)
    d = request.get_json() or {}
    if "material_id" in d and d["material_id"]:
        item.material_id = int(d["material_id"])
    if "numero_ca" in d:
        item.numero_ca = (d["numero_ca"] or "").strip() or None
    if "lote" in d:
        item.lote = (d["lote"] or "").strip() or None
    if "data_validade" in d:
        if d["data_validade"]:
            try:
                from datetime import datetime as _dt2
                item.data_validade = _dt2.strptime(d["data_validade"], "%Y-%m-%d").date()
            except (ValueError, TypeError):
                item.data_validade = None
        else:
            item.data_validade = None
    if "localizacao" in d:
        item.localizacao = (d["localizacao"] or "").strip() or None
    if "quantidade" in d:
        item.quantidade = max(0, int(d["quantidade"] or 0))
    if "qtd_minima" in d:
        item.qtd_minima = max(0, int(d["qtd_minima"] or 5))
    from datetime import datetime as _dt3
    item.atualizado_em = _dt3.now()
    db.session.commit()
    _audit("estoque_item", item.id, "atualizar")
    return jsonify({"id": item.id})


@facilities_bp.route("/api/facilities/estoque/<int:id>", methods=["DELETE"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_deletar_estoque_item(id):
    item = FacilitiesEstoqueItem.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    _audit("estoque_item", id, "deletar")
    return jsonify({"ok": True})


@facilities_bp.route("/api/facilities/estoque/<int:id>/baixa", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_baixa_estoque(id):
    item = FacilitiesEstoqueItem.query.get_or_404(id)
    d = request.get_json() or {}
    qtd = max(1, int(d.get("quantidade") or 1))
    if item.quantidade < qtd:
        return jsonify({"error": f"Estoque insuficiente (disponível: {item.quantidade})"}), 400
    item.quantidade -= qtd
    from datetime import datetime as _dt3
    item.atualizado_em = _dt3.now()
    db.session.commit()
    obs = (d.get("observacao") or "").strip() or None
    _audit("estoque_item", item.id, "baixa", f"qtd={qtd}" + (f", obs={obs}" if obs else ""))
    return jsonify({"ok": True, "quantidade_restante": item.quantidade})


# ============================================================================
# CHAMADOS DE FACILITIES  (novo módulo)
# ============================================================================

@facilities_bp.route("/api/facilities/chamados", methods=["GET"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_listar_chamados():
    q = FacilitiesChamado.query
    status = (request.args.get("status") or "").lower()
    categoria = (request.args.get("categoria") or "").lower()
    prioridade = (request.args.get("prioridade") or "").lower()
    busca = (request.args.get("busca") or "").strip()

    if status:
        q = q.filter_by(status=status)
    if categoria:
        q = q.filter_by(categoria=categoria)
    if prioridade:
        q = q.filter_by(prioridade=prioridade)
    if busca:
        q = q.filter(FacilitiesChamado.titulo.ilike(f"%{busca}%"))

    rows = q.order_by(FacilitiesChamado.aberto_em.desc()).limit(200).all()
    return jsonify({
        "rows": [
            {
                "id": r.id,
                "titulo": r.titulo,
                "descricao": r.descricao or "",
                "categoria": r.categoria,
                "prioridade": r.prioridade,
                "status": r.status,
                "local": r.local or "",
                "aberto_por": r.aberto_por or "",
                "responsavel": r.responsavel or "",
                "observacao": r.observacao or "",
                "aberto_em": r.aberto_em.strftime("%d/%m/%Y %H:%M") if r.aberto_em else "",
                "atualizado_em": r.atualizado_em.strftime("%d/%m/%Y %H:%M") if r.atualizado_em else "",
                "concluido_em": r.concluido_em.strftime("%d/%m/%Y") if r.concluido_em else "",
            }
            for r in rows
        ]
    })


@facilities_bp.route("/api/facilities/chamados", methods=["POST"])
@login_required
def api_criar_chamado():
    d = request.get_json() or {}
    titulo = (d.get("titulo") or "").strip()
    if not titulo:
        return jsonify({"error": "titulo obrigatório"}), 400
    chamado = FacilitiesChamado(
        titulo=titulo,
        descricao=(d.get("descricao") or "").strip() or None,
        categoria=(d.get("categoria") or "outros").lower(),
        prioridade=(d.get("prioridade") or "media").lower(),
        status="aberto",
        local=(d.get("local") or "").strip() or None,
        aberto_por=session.get("username") or "anon",
        responsavel=(d.get("responsavel") or "").strip() or None,
    )
    db.session.add(chamado)
    db.session.commit()
    _audit("chamado", chamado.id, "criar", f"{titulo[:60]}")
    return jsonify({"id": chamado.id}), 201


@facilities_bp.route("/api/facilities/chamados/<int:id>/status", methods=["POST"])
@login_required
@permission_required("PAGE_FACILITIES_ADMIN")
def api_atualizar_status_chamado(id):
    from datetime import datetime as _dt3
    chamado = FacilitiesChamado.query.get_or_404(id)
    d = request.get_json() or {}
    novo_status = (d.get("status") or "").lower()
    valid = {"aberto", "em_analise", "aprovado", "em_execucao", "concluido", "cancelado"}
    if novo_status not in valid:
        return jsonify({"error": f"status inválido: {novo_status}"}), 400
    chamado.status = novo_status
    chamado.observacao = (d.get("observacao") or "").strip() or chamado.observacao
    chamado.atualizado_em = _dt3.now()
    if novo_status in ("concluido", "cancelado") and not chamado.concluido_em:
        chamado.concluido_em = _dt3.now()
    db.session.commit()
    _audit("chamado", chamado.id, f"status→{novo_status}")
    return jsonify({"id": chamado.id, "status": chamado.status})

