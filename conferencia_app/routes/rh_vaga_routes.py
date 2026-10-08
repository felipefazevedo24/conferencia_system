"""Rotas do RH - Requisicao de Vaga (abertura, aprovacoes, publicacao e
candidaturas pela pagina publica)."""
from __future__ import annotations

import html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

from flask import Blueprint, Response, current_app, jsonify, render_template, request, send_file, session, url_for
from sqlalchemy import func

from ..auth import has_permission, is_admin_role, permission_required, permission_required_any
from ..extensions import db
from ..models import RhCandidato, RhCandidatoAcesso, RhCargoVaga, RhRequisicaoVaga, Usuario
from ..services import rh_vaga_service as svc
from ..services.smtp_service import enviar_mensagem_smtp

rh_vaga_bp = Blueprint("rh_vaga", __name__)

PERMISSOES_TELA = (svc.PERM_REQUISICAO, svc.PERM_DIRETORIA, svc.PERM_FINANCEIRO, svc.PERM_GESTAO)
PERMISSAO_POR_PAPEL = {"diretoria": svc.PERM_DIRETORIA, "financeiro": svc.PERM_FINANCEIRO, "gestao": svc.PERM_GESTAO}


def _dt(valor):
    return valor.strftime("%d/%m/%Y %H:%M") if valor else None


def _num(valor):
    return float(valor) if valor is not None else None


def _ator() -> svc.Ator:
    return svc.Ator(
        usuario=session.get("username", ""),
        gestao=has_permission(svc.PERM_GESTAO),
        diretoria=has_permission(svc.PERM_DIRETORIA),
        financeiro=has_permission(svc.PERM_FINANCEIRO),
    )


def _link_publico(requisicao: RhRequisicaoVaga) -> str | None:
    if requisicao.status != svc.STATUS_PUBLICADA or not requisicao.token_publico:
        return None
    return url_for("rh_vaga.vaga_publica_page", token=requisicao.token_publico, _external=True)


def _fmt(requisicao: RhRequisicaoVaga, ator: svc.Ator, completo: bool = False, candidatos: int | None = None) -> dict:
    etapa = svc.etapa_atual(requisicao)
    dados = {
        "id": requisicao.id,
        "numero": requisicao.numero,
        "tipo": requisicao.tipo,
        "tipo_label": svc.TIPOS.get(requisicao.tipo, requisicao.tipo),
        "cargo_id": requisicao.cargo_id,
        "cargo_nome": requisicao.cargo_nome,
        "departamento": requisicao.departamento,
        "quantidade": requisicao.quantidade,
        "status": requisicao.status,
        "etapa": etapa[2] if etapa else None,
        "ciclo": requisicao.ciclo,
        "solicitante": requisicao.solicitante,
        "criado_em": _dt(requisicao.criado_em),
        "atualizado_em": _dt(requisicao.atualizado_em),
        "pode_aprovar": svc.pode_aprovar(requisicao, ator),
        "pode_editar": svc.pode_editar(requisicao, ator),
        "pode_cancelar": svc.pode_cancelar(requisicao, ator),
        "pode_reabrir": svc.pode_reabrir(requisicao, ator),
        "pode_publicar": ator.gestao and requisicao.status == svc.STATUS_APROVADA,
        "pode_encerrar": ator.gestao and requisicao.status == svc.STATUS_PUBLICADA,
    }
    if ator.gestao:
        dados["candidatos"] = candidatos or 0
    if not completo:
        return dados

    ver_salario = svc.pode_ver_salario(requisicao, ator)
    dados.update({
        "perfil": requisicao.perfil,
        "substituido_nome": requisicao.substituido_nome,
        "justificativa": requisicao.justificativa,
        "cancelamento_motivo": requisicao.cancelamento_motivo,
        "ver_salario": ver_salario,
        "faixa_min": _num(requisicao.faixa_min) if ver_salario else None,
        "faixa_max": _num(requisicao.faixa_max) if ver_salario else None,
        "eventos": [
            {"ciclo": e.ciclo, "acao": e.acao, "comentario": e.comentario, "usuario": e.usuario, "criado_em": _dt(e.criado_em)}
            for e in requisicao.eventos
        ],
    })
    if ator.gestao:
        dados.update({
            "titulo_publico": requisicao.titulo_publico,
            "descricao_publica": requisicao.descricao_publica,
            "link_publico": _link_publico(requisicao),
            "publicada_em": _dt(requisicao.publicada_em),
            "publicada_por": requisicao.publicada_por,
            "encerrada_em": _dt(requisicao.encerrada_em),
        })
    return dados


def _fmt_cargo(cargo: RhCargoVaga, com_faixa: bool) -> dict:
    dados = {"id": cargo.id, "nome": cargo.nome, "perfil": cargo.perfil, "ativo": bool(cargo.ativo)}
    if com_faixa:
        dados.update({"faixa_min": _num(cargo.faixa_min), "faixa_max": _num(cargo.faixa_max)})
    return dados


def _contagem_candidatos() -> dict:
    return dict(
        db.session.query(RhCandidato.requisicao_id, func.count(RhCandidato.id))
        .group_by(RhCandidato.requisicao_id).all()
    )


def _obter_visivel(requisicao_id: int, ator: svc.Ator) -> RhRequisicaoVaga | None:
    """Requisicao que o usuario nao pode ver responde igual a uma que nao existe."""
    requisicao = db.session.get(RhRequisicaoVaga, requisicao_id)
    if not requisicao or not svc.pode_ver(requisicao, ator):
        return None
    return requisicao


# ── Avisos por e-mail ──────────────────────────────────────────────────
def _emails_com_permissao(permissao: str) -> list[str]:
    """Quem tem a permissao por cargo ou por excecao individual. Admin fica de
    fora: tem todas as permissoes e receberia aviso de toda requisicao."""
    emails = []
    for usuario in Usuario.query.filter_by(ativo=True).all():
        if not usuario.email or is_admin_role(usuario.role):
            continue
        if has_permission(permissao, username=usuario.username, role=usuario.role):
            emails.append(usuario.email.strip())
    return emails


def _avisar(requisicao: RhRequisicaoVaga) -> None:
    """Avisa quem precisa agir agora. Nunca derruba a acao que ja' foi gravada."""
    try:
        etapa = svc.etapa_atual(requisicao)
        if etapa:
            destinatarios = _emails_com_permissao(PERMISSAO_POR_PAPEL[etapa[1]])
            assunto = f"Requisição de vaga {requisicao.numero} aguardando aprovação ({etapa[2]})"
            texto = f"A requisição de vaga <strong>{html.escape(requisicao.numero)}</strong> aguarda a aprovação da etapa <strong>{etapa[2]}</strong>."
        elif requisicao.status == svc.STATUS_APROVADA:
            destinatarios = _emails_com_permissao(svc.PERM_GESTAO)
            assunto = f"Requisição de vaga {requisicao.numero} aprovada - falta publicar"
            texto = f"A requisição de vaga <strong>{html.escape(requisicao.numero)}</strong> passou por todas as aprovações e já pode ser publicada."
        elif requisicao.status == svc.STATUS_CORRECAO:
            solicitante = Usuario.query.filter(func.lower(Usuario.username) == requisicao.solicitante.strip().lower()).first()
            destinatarios = [solicitante.email.strip()] if solicitante and solicitante.email else []
            assunto = f"Requisição de vaga {requisicao.numero} devolvida para correção"
            texto = f"A requisição de vaga <strong>{html.escape(requisicao.numero)}</strong> foi devolvida para correção. O motivo está no histórico da requisição."
        else:
            return
        if not destinatarios or not current_app.config.get("MAIL_SENDER"):
            return

        link = url_for("rh_vaga.vagas_page", _external=True)
        msg = MIMEMultipart("mixed")
        msg["Subject"] = assunto
        msg["From"] = f"{current_app.config.get('MAIL_SENDER_NAME', 'Columbia Sync')} <{current_app.config.get('MAIL_SENDER', '')}>"
        msg["To"] = ", ".join(sorted(set(destinatarios)))
        # Sem cargo, salario ou nomes no e-mail: o detalhe so' abre dentro do Sync.
        corpo = f"<p>Olá,</p><p>{texto}</p><p><a href=\"{link}\">{link}</a></p><p>Columbia Sync</p>"
        msg.attach(MIMEText(corpo, "html", "utf-8"))
        enviar_mensagem_smtp(current_app, msg)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Falha ao avisar sobre a requisicao de vaga %s", requisicao.id)


# ── Tela e requisicoes ─────────────────────────────────────────────────
@rh_vaga_bp.route("/rh/vagas")
@permission_required_any(*PERMISSOES_TELA)
def vagas_page():
    ator = _ator()
    return render_template(
        "rh_vagas.html",
        user=session["username"],
        user_role=session.get("role", ""),
        pode_abrir=has_permission(svc.PERM_REQUISICAO) or ator.gestao,
        pode_gestao=ator.gestao,
        ve_todas=ator.aprovador,
    )


@rh_vaga_bp.route("/api/rh/vagas", methods=["GET"])
@permission_required_any(*PERMISSOES_TELA)
def api_listar():
    ator = _ator()
    registros = svc.listar(ator, status=request.args.get("status") or "", busca=request.args.get("busca") or "")
    contagem = _contagem_candidatos() if ator.gestao else {}
    return jsonify({
        "requisicoes": [_fmt(r, ator, candidatos=contagem.get(r.id)) for r in registros],
        "metricas": svc.metricas(svc.listar(ator), ator),
        "status": list(svc.STATUS_TODOS),
        # Faixa do cadastro so' vai pro navegador de quem e' do RH.
        "cargos": [_fmt_cargo(c, com_faixa=ator.gestao) for c in svc.listar_cargos_vaga(apenas_ativos=True)],
    })


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>", methods=["GET"])
@permission_required_any(*PERMISSOES_TELA)
def api_detalhe(requisicao_id):
    ator = _ator()
    requisicao = _obter_visivel(requisicao_id, ator)
    if not requisicao:
        return jsonify({"error": "Requisição não encontrada."}), 404
    candidatos = RhCandidato.query.filter_by(requisicao_id=requisicao.id).count() if ator.gestao else None
    return jsonify({"requisicao": _fmt(requisicao, ator, completo=True, candidatos=candidatos)})


@rh_vaga_bp.route("/api/rh/vagas", methods=["POST"])
@permission_required_any(svc.PERM_REQUISICAO, svc.PERM_GESTAO)
def api_criar():
    ator = _ator()
    try:
        requisicao = svc.criar(request.get_json(silent=True) or {}, ator)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    _avisar(requisicao)
    return jsonify({"message": f"Requisição {requisicao.numero} aberta e enviada para a Diretoria.",
                    "requisicao": _fmt(requisicao, ator, completo=True)})


def _acao(requisicao_id, funcao, mensagem, avisar=False, **kwargs):
    ator = _ator()
    requisicao = _obter_visivel(requisicao_id, ator)
    if not requisicao:
        return jsonify({"error": "Requisição não encontrada."}), 404
    try:
        funcao(requisicao, ator=ator, **kwargs)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if avisar:
        _avisar(requisicao)
    candidatos = RhCandidato.query.filter_by(requisicao_id=requisicao.id).count() if ator.gestao else None
    return jsonify({"message": mensagem, "requisicao": _fmt(requisicao, ator, completo=True, candidatos=candidatos)})


def _payload() -> dict:
    return request.get_json(silent=True) or {}


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>", methods=["PUT"])
@permission_required_any(svc.PERM_REQUISICAO, svc.PERM_GESTAO)
def api_corrigir(requisicao_id):
    return _acao(requisicao_id, svc.atualizar, "Requisição corrigida e reenviada para a Diretoria.",
                 avisar=True, dados=_payload())


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>/aprovar", methods=["POST"])
@permission_required_any(svc.PERM_DIRETORIA, svc.PERM_FINANCEIRO, svc.PERM_GESTAO)
def api_aprovar(requisicao_id):
    return _acao(requisicao_id, svc.aprovar, "Etapa aprovada.", avisar=True, comentario=_payload().get("comentario") or "")


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>/reprovar", methods=["POST"])
@permission_required_any(svc.PERM_DIRETORIA, svc.PERM_FINANCEIRO, svc.PERM_GESTAO)
def api_reprovar(requisicao_id):
    return _acao(requisicao_id, svc.reprovar, "Requisição devolvida para correção.", avisar=True,
                 motivo=_payload().get("motivo") or "")


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>/cancelar", methods=["POST"])
@permission_required_any(svc.PERM_REQUISICAO, svc.PERM_GESTAO)
def api_cancelar(requisicao_id):
    return _acao(requisicao_id, svc.cancelar, "Requisição cancelada.", motivo=_payload().get("motivo") or "")


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>/reabrir", methods=["POST"])
@permission_required_any(svc.PERM_REQUISICAO, svc.PERM_GESTAO)
def api_reabrir(requisicao_id):
    return _acao(requisicao_id, svc.reabrir, "Requisição reaberta: o processo recomeça na Diretoria.", avisar=True)


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>/publicar", methods=["POST"])
@permission_required(svc.PERM_GESTAO)
def api_publicar(requisicao_id):
    dados = _payload()
    return _acao(requisicao_id, svc.publicar, "Vaga publicada. O link e o QR code já estão valendo.",
                 titulo=dados.get("titulo") or "", descricao=dados.get("descricao") or "")


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>/encerrar", methods=["POST"])
@permission_required(svc.PERM_GESTAO)
def api_encerrar(requisicao_id):
    return _acao(requisicao_id, svc.encerrar, "Vaga encerrada. O link de candidatura foi desativado.",
                 comentario=_payload().get("comentario") or "")


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>/qrcode.svg", methods=["GET"])
@permission_required(svc.PERM_GESTAO)
def api_qrcode(requisicao_id):
    """QR code do link de candidatura. Gerado com o reportlab, que o projeto
    ja' usa nos PDFs - sem biblioteca nova pra instalar no servidor."""
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode.qr import QrCodeWidget
    from reportlab.graphics.shapes import Drawing

    requisicao = db.session.get(RhRequisicaoVaga, requisicao_id)
    link = _link_publico(requisicao) if requisicao else None
    if not link:
        return jsonify({"error": "A vaga não está publicada."}), 404
    widget = QrCodeWidget(link, barLevel="M")
    x0, y0, x1, y1 = widget.getBounds()
    lado, tamanho = x1 - x0, 480
    desenho = Drawing(tamanho, tamanho, transform=[tamanho / lado, 0, 0, tamanho / lado, 0, 0])
    desenho.add(widget)
    resposta = Response(renderSVG.drawToString(desenho), mimetype="image/svg+xml")
    if request.args.get("download"):
        resposta.headers["Content-Disposition"] = f'attachment; filename="qrcode_{requisicao.numero}.svg"'
    return resposta


# ── Candidatos (so' Gestao do RH) ──────────────────────────────────────
@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>/candidatos", methods=["GET"])
@permission_required(svc.PERM_GESTAO)
def api_candidatos(requisicao_id):
    if not db.session.get(RhRequisicaoVaga, requisicao_id):
        return jsonify({"error": "Requisição não encontrada."}), 404
    candidatos = RhCandidato.query.filter_by(requisicao_id=requisicao_id).order_by(RhCandidato.id.desc()).all()
    return jsonify({"candidatos": [
        {"id": c.id, "nome": c.nome, "email": c.email, "telefone": c.telefone, "mensagem": c.mensagem,
         "curriculo_nome": c.curriculo_nome, "curriculo_kb": round((c.curriculo_tamanho or 0) / 1024),
         "criado_em": _dt(c.criado_em)}
        for c in candidatos
    ]})


@rh_vaga_bp.route("/api/rh/candidatos/<int:candidato_id>/curriculo", methods=["GET"])
@permission_required(svc.PERM_GESTAO)
def api_curriculo(candidato_id):
    candidato = db.session.get(RhCandidato, candidato_id)
    if not candidato or not candidato.curriculo_dados:
        return jsonify({"error": "Currículo não encontrado."}), 404
    dados = candidato.curriculo_dados
    svc.registrar_acesso_candidato(candidato, session.get("username", "desconhecido"), "baixou")
    db.session.commit()
    # Sempre como anexo: o PDF veio de fora e nao deve abrir na origem do Sync.
    resposta = send_file(BytesIO(dados), mimetype="application/pdf", as_attachment=True,
                         download_name=candidato.curriculo_nome or f"curriculo_{candidato.id}.pdf")
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["Cache-Control"] = "no-store"
    return resposta


@rh_vaga_bp.route("/api/rh/candidatos/<int:candidato_id>", methods=["DELETE"])
@permission_required(svc.PERM_GESTAO)
def api_excluir_candidato(candidato_id):
    candidato = db.session.get(RhCandidato, candidato_id)
    if not candidato:
        return jsonify({"error": "Candidato não encontrado."}), 404
    svc.excluir_candidato(candidato, session.get("username", "desconhecido"))
    return jsonify({"message": "Candidato e currículo excluídos."})


@rh_vaga_bp.route("/api/rh/vagas/<int:requisicao_id>/acessos", methods=["GET"])
@permission_required(svc.PERM_GESTAO)
def api_acessos(requisicao_id):
    acessos = (RhCandidatoAcesso.query.filter_by(requisicao_id=requisicao_id)
               .order_by(RhCandidatoAcesso.id.desc()).limit(200).all())
    return jsonify({"acessos": [
        {"candidato": a.candidato_nome, "acao": a.acao, "usuario": a.usuario, "criado_em": _dt(a.criado_em)}
        for a in acessos
    ]})


# ── Cadastro de cargos e faixas (so' Gestao do RH) ─────────────────────
@rh_vaga_bp.route("/api/rh/cargos", methods=["GET"])
@permission_required(svc.PERM_GESTAO)
def api_cargos():
    return jsonify({"cargos": [_fmt_cargo(c, com_faixa=True) for c in svc.listar_cargos_vaga()]})


@rh_vaga_bp.route("/api/rh/cargos", methods=["POST"])
@permission_required(svc.PERM_GESTAO)
def api_cargo_criar():
    try:
        cargo = svc.salvar_cargo_vaga(_payload(), session.get("username", "desconhecido"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Cargo cadastrado.", "cargo": _fmt_cargo(cargo, com_faixa=True)})


@rh_vaga_bp.route("/api/rh/cargos/<int:cargo_id>", methods=["PUT"])
@permission_required(svc.PERM_GESTAO)
def api_cargo_atualizar(cargo_id):
    cargo = db.session.get(RhCargoVaga, cargo_id)
    if not cargo:
        return jsonify({"error": "Cargo não encontrado."}), 404
    try:
        svc.salvar_cargo_vaga(_payload(), session.get("username", "desconhecido"), cargo=cargo)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Cargo atualizado.", "cargo": _fmt_cargo(cargo, com_faixa=True)})


# ── Pagina publica da vaga (sem login) ─────────────────────────────────
def _ip_cliente() -> str:
    return (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()[:64]


@rh_vaga_bp.route("/vagas/<token>")
def vaga_publica_page(token):
    requisicao = svc.obter_vaga_publica(token)
    resposta = current_app.make_response(render_template(
        "rh_vaga_publica.html", token=token, vaga=requisicao,
        max_mb=svc.MAX_CURRICULO_BYTES // (1024 * 1024),
    ))
    if not requisicao:
        resposta.status_code = 404
    resposta.headers["X-Frame-Options"] = "DENY"
    return resposta


@rh_vaga_bp.route("/api/vagas/<token>/candidatura", methods=["POST"])
def api_candidatura(token):
    requisicao = svc.obter_vaga_publica(token)
    if not requisicao:
        return jsonify({"error": "Esta vaga não está mais recebendo candidaturas."}), 404
    # Rota publica e o app nao tem MAX_CONTENT_LENGTH: recusa upload gigante
    # antes de o Werkzeug ler o corpo inteiro.
    if (request.content_length or 0) > svc.MAX_CURRICULO_BYTES + 64 * 1024:
        return jsonify({"error": "Currículo muito grande (máximo 5 MB)."}), 400
    sucesso = jsonify({"message": "Candidatura recebida. Obrigado pelo interesse! "
                                  "Cada e-mail participa uma vez por vaga: se você já tinha se candidatado, vale a primeira candidatura."})
    # Campo isca, invisivel na pagina: so' robo preenche. Responde como
    # sucesso pra ele nao aprender a pular o campo.
    if (request.form.get("site") or "").strip():
        return sucesso
    try:
        svc.registrar_candidatura(requisicao, request.form, request.files.get("curriculo"), _ip_cliente())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return sucesso
