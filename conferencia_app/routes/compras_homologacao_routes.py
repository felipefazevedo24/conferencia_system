"""Rotas da Homologacao de Fornecedores (Compras) - formulario F-COM-001-01."""
from __future__ import annotations

import html
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, session, url_for

from ..auth import is_admin_session, permission_required
from ..extensions import db
from ..models import (
    ComprasHomologacaoEvidencia,
    ComprasHomologacaoFornecedor as Homologacao,
    ComprasHomologacaoFoto,
)
from ..services import compras_homologacao_form as form
from ..services import compras_homologacao_form_rev04 as form_r04
from ..services import compras_homologacao_pdf as pdf_svc
from ..services import compras_homologacao_plano_service as plano_svc
from ..services import compras_homologacao_service as svc
from ..services.smtp_service import enviar_mensagem_smtp

compras_homologacao_bp = Blueprint("compras_homologacao", __name__)

PERMISSION = "PAGE_COMPRAS_HOMOLOGACAO"
CLASSIFICACAO_EM_ANALISE = "Em análise"


def _dt(valor):
    return valor.strftime("%d/%m/%Y %H:%M") if valor else None


def _fmt(homologacao: Homologacao, completo: bool = False) -> dict:
    validade = svc.situacao_validade(homologacao)
    dados = {
        "id": homologacao.id,
        "razao_social": homologacao.razao_social,
        "nome_fantasia": homologacao.nome_fantasia,
        "cnpj": homologacao.cnpj,
        "categoria_compra": homologacao.categoria_compra,
        "status": homologacao.status,
        "nota": homologacao.nota,
        # Com o fornecedor as respostas ainda estao sendo preenchidas por ele -
        # a nota parcial nao classifica nada. So' na exibicao: o valor gravado
        # continua sendo o calculado e volta a aparecer quando retorna pro Rascunho.
        "classificacao": (
            CLASSIFICACAO_EM_ANALISE
            if homologacao.status == Homologacao.STATUS_COM_FORNECEDOR
            else homologacao.classificacao
        ),
        "validade_meses": homologacao.validade_meses,
        "valido_ate": homologacao.valido_ate.isoformat() if homologacao.valido_ate else None,
        "validade_situacao": validade["situacao"],
        "validade_dias_restantes": validade["dias_restantes"],
        "criado_em": _dt(homologacao.criado_em),
        "criado_por": homologacao.criado_por,
        "enviado_em": _dt(homologacao.enviado_em),
        "enviado_por": homologacao.enviado_por,
        "decidido_em": _dt(homologacao.decidido_em),
        "decidido_por": homologacao.decidido_por,
        "justificativa_decisao": homologacao.justificativa_decisao,
        "qtd_fotos": len(homologacao.fotos),
        "formulario_versao": homologacao.formulario_versao or "F-COM-001-01",
        "plano_acao": plano_svc.resumo(plano_svc.plano_ativo(homologacao)),
    }
    if not completo:
        return dados

    dados.update({
        "inscricao_estadual": homologacao.inscricao_estadual,
        "endereco": homologacao.endereco,
        "cidade_estado": homologacao.cidade_estado,
        "contato_principal": homologacao.contato_principal,
        "telefone": homologacao.telefone,
        "email": homologacao.email,
        "website": homologacao.website,
        "descricao_produto_servico": homologacao.descricao_produto_servico,
        "resultado_auditoria": homologacao.resultado_auditoria,
        "obs_conformidade_legal": homologacao.obs_conformidade_legal,
        "comentario": homologacao.comentario,
        "amostra_necessaria": homologacao.amostra_necessaria,
        "amostra_obs": homologacao.amostra_obs,
        "visita_necessaria": homologacao.visita_necessaria,
        "visita_obs": homologacao.visita_obs,
    })

    _, _, detalhe = svc.calcular_nota_da(homologacao)
    dados["detalhe_secoes"] = detalhe
    dados["formulario"] = _formulario_payload(svc.formulario_da(homologacao))
    dados.update(_dados_rev04(homologacao, "/api/compras/homologacao/evidencias/{id}"))
    dados["secoes"] = _secoes_payload(homologacao, "/api/compras/homologacao/evidencias/{id}")
    dados["fotos"] = [
        {
            "id": f.id,
            "nome_arquivo": f.nome_arquivo,
            "legenda": f.legenda,
            "tamanho_bytes": f.tamanho_bytes,
            "enviado_em": _dt(f.enviado_em),
            "enviado_por": f.enviado_por,
            "url": f"/api/compras/homologacao/fotos/{f.id}",
        }
        for f in homologacao.fotos
    ]
    dados["itens_faltando"] = len(svc.itens_faltando(homologacao))
    dados["itens_sem_evidencia"] = len(svc.pendencias_evidencia(homologacao))
    dados["itens_sem_justificativa"] = len(svc.pendencias_justificativa(homologacao))
    dados["plano_acao"] = _fmt_plano(plano_svc.plano_ativo(homologacao), "/api/compras/homologacao/planos/evidencias/{id}")
    dados["plano_pode_criar"] = (
        not dados["plano_acao"]
        and homologacao.status in (Homologacao.STATUS_HOMOLOGADO, Homologacao.STATUS_REPROVADO)
        and bool(plano_svc.pendencias(homologacao))
    )
    convite = svc.ultimo_convite(homologacao)
    dados["convite"] = {
        "email": convite.email,
        "situacao": svc.situacao_convite(convite),
        "enviado_em": _dt(convite.enviado_em),
        "enviado_por": convite.enviado_por,
        "expira_em": _dt(convite.expira_em),
        "respondido_em": _dt(convite.respondido_em),
    } if convite else None
    return dados


def _fmt_plano(plano, url_evidencia: str) -> dict | None:
    """Plano de acao com os itens e evidencias. `url_evidencia` muda entre a
    tela interna e o link do fornecedor."""
    if not plano:
        return None
    return {
        **plano_svc.resumo(plano),
        "relatorio": plano.relatorio,
        "relatorio_origem": plano.relatorio_origem,
        "criado_em": _dt(plano.criado_em),
        "email": plano.email,
        "enviado_em": _dt(plano.enviado_em),
        "enviado_por": plano.enviado_por,
        "concluido_em": _dt(plano.concluido_em),
        "itens": [
            {
                "id": it.id,
                "secao": it.secao,
                "item": it.item,
                "texto": it.texto,
                "resposta": it.resposta,
                "motivo": it.motivo,
                "acao": it.acao,
                "prazo": it.prazo.isoformat() if it.prazo else None,
                "status": it.status,
                "comentario_fornecedor": it.comentario_fornecedor,
                "enviado_em": _dt(it.enviado_em),
                "motivo_recusa": it.motivo_recusa,
                "decidido_em": _dt(it.decidido_em),
                "decidido_por": it.decidido_por,
                "evidencias": [
                    {"id": ev.id, "nome_arquivo": ev.nome_arquivo, "enviado_em": _dt(ev.enviado_em),
                     "url": url_evidencia.format(id=ev.id)}
                    for ev in it.evidencias
                ],
            }
            for it in plano.itens
        ],
    }


def _formulario_payload(formulario) -> dict:
    """Regras do formulario que as telas precisam pra pre-visualizar a nota
    e os avisos - a nota que vale e' a calculada no servidor."""
    if formulario is form_r04:
        return {
            "versao": form_r04.VERSAO,
            "codigo": form_r04.CODIGO_FORMULARIO,
            "revisao": form_r04.REVISAO,
            "titulo": form_r04.TITULO_FORMULARIO,
            "regra": "pontos",
            "faixas": [
                {"minimo": form_r04.NOTA_MINIMA_QUALIFICADO, "estrito": True,
                 "classificacao": form_r04.CLASSIFICACAO_QUALIFICADO, "slug": "aprovado"},
                {"minimo": form_r04.NOTA_MINIMA_PLANO_ACAO, "estrito": True,
                 "classificacao": form_r04.CLASSIFICACAO_PLANO_ACAO, "slug": "ressalvas"},
                {"minimo": None, "estrito": False,
                 "classificacao": form_r04.CLASSIFICACAO_DESQUALIFICADO, "slug": "reprovado"},
            ],
            "texto_faixas": form_r04.TEXTO_FAIXAS,
            "setores_responsaveis": list(form_r04.SETORES_RESPONSAVEIS),
            "secao_iso": form_r04.SECAO_ISO,
        }
    return {
        "versao": "F-COM-001-01",
        "codigo": form.CODIGO_FORMULARIO,
        "revisao": "01",
        "titulo": form.TITULO_FORMULARIO,
        "regra": "pesos",
        "faixas": [
            {"minimo": form.NOTA_MINIMA_APROVADO, "estrito": False,
             "classificacao": form.CLASSIFICACAO_APROVADO, "slug": "aprovado"},
            {"minimo": form.NOTA_MINIMA_RESSALVAS, "estrito": False,
             "classificacao": form.CLASSIFICACAO_RESSALVAS, "slug": "ressalvas"},
            {"minimo": None, "estrito": False,
             "classificacao": form.CLASSIFICACAO_REPROVADO, "slug": "reprovado"},
        ],
        "texto_faixas": "≥ 75% Aprovado · ≥ 55% Com ressalvas · abaixo disso Reprovado",
        "setores_responsaveis": [],
        "secao_iso": None,
        "obs_conformidade_legal": form.OBS_CONFORMIDADE_LEGAL,
    }


def _evidencias_por_item(homologacao: Homologacao | None, url_evidencia: str) -> dict:
    evidencias: dict[tuple[str, int], list] = {}
    for e in (homologacao.evidencias if homologacao else []):
        evidencias.setdefault((e.secao, e.item), []).append({
            "id": e.id,
            "nome_arquivo": e.nome_arquivo,
            "tamanho_bytes": e.tamanho_bytes,
            "enviado_em": _dt(e.enviado_em),
            "enviado_por": e.enviado_por,
            "url": url_evidencia.format(id=e.id),
        })
    return evidencias


def _dados_rev04(homologacao: Homologacao, url_evidencia: str) -> dict:
    """ISO 9001 e responsaveis (so' existem no F 066 rev. 04)."""
    if not svc.eh_rev04(homologacao):
        return {}
    por_setor = {r.setor: r for r in homologacao.responsaveis}
    return {
        "iso9001_certificado": homologacao.iso9001_certificado,
        "iso9001_validade": homologacao.iso9001_validade.isoformat() if homologacao.iso9001_validade else None,
        "iso9001_evidencias": _evidencias_por_item(homologacao, url_evidencia).get((form_r04.SECAO_ISO, 1), []),
        "iso9001_valido": svc.iso_valido(homologacao),
        "responsaveis": [
            {
                "setor": setor,
                "nome": getattr(por_setor.get(setor), "nome", None),
                "cargo": getattr(por_setor.get(setor), "cargo", None),
                "telefone": getattr(por_setor.get(setor), "telefone", None),
                "email": getattr(por_setor.get(setor), "email", None),
            }
            for setor in form_r04.SETORES_RESPONSAVEIS
        ],
    }


def _regras_secao(formulario, secao: dict) -> dict:
    if formulario is form_r04:
        return {
            "evidencia_exigida": [form_r04.RESPOSTA_ATENDE] if secao["evidencia"] == "obrigatoria" else [],
            "evidencia_limita": [form_r04.RESPOSTA_ATENDE] if secao["evidencia"] == "limita_parcial" else [],
            "justificar_na": secao["justificar_na"],
            "pula_com_iso": secao["pula_com_iso"],
        }
    return {
        "evidencia_exigida": [r for r in form.RESPOSTAS_EXIGEM_EVIDENCIA if r in secao["escala"]],
        "evidencia_limita": [],
        "justificar_na": False,
        "pula_com_iso": False,
    }


def _secoes_payload(homologacao: Homologacao | None, url_evidencia: str, formulario=None) -> list[dict]:
    """Secoes do formulario com resposta, comentario e evidencias de cada
    item. `url_evidencia` muda entre a tela interna e o link do fornecedor.
    Sem homologacao (formulario em branco), usa `formulario`."""
    formulario = formulario or svc.formulario_da(homologacao)
    respostas = {(r.secao, r.item): r for r in (homologacao.respostas if homologacao else [])}
    evidencias = _evidencias_por_item(homologacao, url_evidencia)
    return [
        {
            "chave": secao["chave"],
            "titulo": secao["titulo"],
            "peso": secao["peso"],
            "escala": list(secao["escala"]),
            **_regras_secao(formulario, secao),
            "itens": [
                {
                    "item": i,
                    "texto": texto,
                    "resposta": (respostas.get((secao["chave"], i)).resposta
                                 if respostas.get((secao["chave"], i)) else None),
                    "comentario": (respostas.get((secao["chave"], i)).comentario
                                   if respostas.get((secao["chave"], i)) else None),
                    "evidencias": evidencias.get((secao["chave"], i), []),
                }
                for i, texto in enumerate(secao["itens"], start=1)
            ],
        }
        for secao in formulario.SECOES
    ]


def _obter(homologacao_id: int) -> Homologacao | None:
    return db.session.get(Homologacao, homologacao_id)


def _usuario() -> str:
    return session.get("username", "desconhecido")


@compras_homologacao_bp.route("/compras/homologacao")
@permission_required(PERMISSION)
def homologacao_page():
    return render_template(
        "compras_homologacao.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@compras_homologacao_bp.route("/api/compras/homologacao", methods=["GET"])
@permission_required(PERMISSION)
def api_listar():
    registros = svc.listar(
        status=request.args.get("status") or "",
        busca=request.args.get("busca") or "",
        validade=request.args.get("validade") or "",
    )
    return jsonify({
        "homologacoes": [_fmt(r) for r in registros],
        "metricas": svc.metricas(svc.listar()),
        "formulario": _formulario_payload(svc.formulario_vigente()),
    })


@compras_homologacao_bp.route("/api/compras/homologacao/modelo", methods=["GET"])
@permission_required(PERMISSION)
def api_modelo():
    """Formulario em branco (versao vigente) - usado pra montar a tela de cadastro."""
    formulario = svc.formulario_vigente()
    payload = _formulario_payload(formulario)
    return jsonify({
        **payload,
        "formulario": payload,
        "secoes": _secoes_payload(None, "", formulario=formulario),
    })


@compras_homologacao_bp.route("/api/compras/homologacao/cnpj/<path:cnpj>", methods=["GET"])
@permission_required(PERMISSION)
def api_consultar_cnpj(cnpj):
    """Dados cadastrais do fornecedor pelo cartao CNPJ - mesma consulta da
    Atualizacao Cadastral (BrasilAPI + IE pelo publica.cnpj.ws), traduzida
    pros campos do cabecalho da homologacao."""
    from ..services.cadastro_workflow_service import consultar_cartao_cnpj

    try:
        cartao = consultar_cartao_cnpj(cnpj)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:  # noqa: BLE001
        return jsonify({"error": "Não foi possível consultar o CNPJ agora. Tente novamente em instantes."}), 502

    cidade_estado = " - ".join(p for p in (cartao.get("municipio"), cartao.get("uf")) if p)
    # O endereco do cartao ja' termina com "Cidade - UF"; aqui a cidade tem
    # campo proprio, entao sai do endereco pra nao aparecer duas vezes.
    endereco = cartao.get("endereco") or ""
    if cidade_estado and endereco.endswith(f" - {cidade_estado}"):
        endereco = endereco[: -len(f" - {cidade_estado}")]
    if endereco and cartao.get("cep"):
        endereco = f"{endereco} - CEP {cartao['cep']}"

    return jsonify({
        "cnpj": cartao.get("documento") or "",
        "razao_social": cartao.get("razao_social") or "",
        "nome_fantasia": cartao.get("nome_fantasia") or "",
        "inscricao_estadual": cartao.get("inscricao_estadual") or "",
        "endereco": endereco,
        "cidade_estado": cidade_estado,
        "telefone": cartao.get("telefone") or "",
        "email": (cartao.get("email") or "").lower(),
        "situacao_cadastral": cartao.get("situacao_cadastral") or "",
    })


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>", methods=["GET"])
@permission_required(PERMISSION)
def api_detalhe(homologacao_id):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    return jsonify({"homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao", methods=["POST"])
@permission_required(PERMISSION)
def api_criar():
    payload = request.get_json(silent=True) or {}
    try:
        homologacao = svc.criar(payload, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Homologação criada.", "homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>", methods=["PUT"])
@permission_required(PERMISSION)
def api_atualizar(homologacao_id):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        homologacao = svc.atualizar(homologacao, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Alterações salvas.", "homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>", methods=["DELETE"])
@permission_required(PERMISSION)
def api_excluir(homologacao_id):
    # Exclusao e' definitiva (leva evidencias, fotos e plano de acao): so' Admin.
    if not is_admin_session():
        return jsonify({"error": "Só o administrador pode excluir uma homologação."}), 403
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    try:
        svc.excluir(homologacao)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Homologação excluída."})


def _acao(homologacao_id, funcao, mensagem, **kwargs):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    try:
        homologacao = funcao(homologacao, **kwargs)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": mensagem, "homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/enviar", methods=["POST"])
@permission_required(PERMISSION)
def api_enviar(homologacao_id):
    return _acao(homologacao_id, svc.enviar_para_aprovacao,
                 "Enviado para aprovação.", usuario=_usuario())


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/devolver", methods=["POST"])
@permission_required(PERMISSION)
def api_devolver(homologacao_id):
    payload = request.get_json(silent=True) or {}
    return _acao(homologacao_id, svc.devolver_para_rascunho,
                 "Devolvido para rascunho.", usuario=_usuario(), motivo=payload.get("motivo") or "")


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/homologar", methods=["POST"])
@permission_required(PERMISSION)
def api_homologar(homologacao_id):
    payload = request.get_json(silent=True) or {}
    return _acao(homologacao_id, svc.homologar, "Fornecedor homologado.",
                 usuario=_usuario(), justificativa=payload.get("justificativa") or "")


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/reprovar", methods=["POST"])
@permission_required(PERMISSION)
def api_reprovar(homologacao_id):
    payload = request.get_json(silent=True) or {}
    return _acao(homologacao_id, svc.reprovar, "Fornecedor reprovado.",
                 usuario=_usuario(), justificativa=payload.get("justificativa") or "")


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/reabrir", methods=["POST"])
@permission_required(PERMISSION)
def api_reabrir(homologacao_id):
    return _acao(homologacao_id, svc.reabrir, "Homologação reaberta como rascunho.", usuario=_usuario())


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/pdf", methods=["GET"])
@permission_required(PERMISSION)
def api_pdf(homologacao_id):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    conteudo = pdf_svc.gerar_pdf_homologacao(homologacao)
    nome = f"{svc.formulario_da(homologacao).CODIGO_FORMULARIO}_{(homologacao.razao_social or 'fornecedor')[:40]}.pdf"
    return send_file(
        BytesIO(conteudo), mimetype="application/pdf",
        as_attachment=False, download_name=nome.replace("/", "-"),
    )


# ── Fotos (secao 8) ─────────────────────────────────────────────────────
@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/fotos", methods=["POST"])
@permission_required(PERMISSION)
def api_anexar_foto(homologacao_id):
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    try:
        svc.anexar_foto(
            homologacao, request.files.get("arquivo"), _usuario(),
            legenda=request.form.get("legenda") or "",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Foto anexada.", "homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/fotos/<int:foto_id>", methods=["GET"])
@permission_required(PERMISSION)
def api_baixar_foto(foto_id):
    foto = db.session.get(ComprasHomologacaoFoto, foto_id)
    if not foto or not foto.dados:
        return jsonify({"error": "Foto não encontrada."}), 404
    return send_file(
        BytesIO(foto.dados), mimetype=foto.content_type or "image/jpeg",
        as_attachment=False, download_name=foto.nome_arquivo or f"foto_{foto.id}.jpg",
    )


@compras_homologacao_bp.route("/api/compras/homologacao/fotos/<int:foto_id>", methods=["DELETE"])
@permission_required(PERMISSION)
def api_remover_foto(foto_id):
    foto = db.session.get(ComprasHomologacaoFoto, foto_id)
    if not foto:
        return jsonify({"error": "Foto não encontrada."}), 404
    try:
        svc.remover_foto(foto)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Foto removida."})


# ── Self assessment: lado do comprador ──────────────────────────────────
def _texto_evidencia_email(homologacao: Homologacao) -> str:
    if svc.eh_rev04(homologacao):
        return (
            "Os documentos marcados como <strong>Atende</strong> precisam da cópia anexada (PDF, JPG ou PNG). "
            "Nas questões do sistema de qualidade, anexe as evidências: sem elas a resposta vale no máximo "
            "<strong>Atende Parcial</strong>. Empresas com <strong>ISO 9001</strong> vigente anexam o "
            "certificado e ficam dispensadas do questionário."
        )
    return (
        "Para os itens respondidos como <strong>Sim</strong>, <strong>Parcial</strong> ou "
        "<strong>Conforme</strong> é obrigatório anexar a evidência (PDF, JPG ou PNG)."
    )


def _enviar_email_self_assessment(homologacao: Homologacao, link: str, destinatario: str, validade: str) -> None:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Homologação de Fornecedor – Autoavaliação – {homologacao.razao_social} – Columbia Machine Brasil"
    msg["From"] = f"{current_app.config.get('MAIL_SENDER_NAME', 'Columbia Sync')} <{current_app.config.get('MAIL_SENDER', '')}>"
    msg["To"] = destinatario
    corpo = (
        f"<p>Prezados,</p>"
        f"<p>A Columbia Machine Brasil está conduzindo a homologação da empresa "
        f"<strong>{html.escape(homologacao.razao_social or '')}</strong> como fornecedora.</p>"
        f"<p>Solicitamos, por gentileza, o preenchimento do questionário de autoavaliação "
        f"({svc.formulario_da(homologacao).CODIGO_FORMULARIO}) pelo link abaixo. {_texto_evidencia_email(homologacao)}</p>"
        f"<p><a href=\"{link}\">{link}</a></p>"
        f"<p>É possível salvar e continuar depois pelo mesmo link, válido até {validade}.</p>"
        f"<p>Atenciosamente,</p>"
        f"<p>Compras – Columbia Machine Brasil</p>"
    )
    msg.attach(MIMEText(corpo, "html", "utf-8"))
    enviar_mensagem_smtp(current_app, msg)


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/enviar-fornecedor", methods=["POST"])
@permission_required(PERMISSION)
def api_enviar_fornecedor(homologacao_id):
    """Gera (ou regera) o link de self assessment e manda por e-mail. Se o
    e-mail falhar, o link continua valido e volta na resposta pro comprador
    repassar por outro canal - mesmo comportamento da cotacao do Comex."""
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        convite, token = svc.enviar_para_fornecedor(homologacao, payload.get("email") or "", _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    link = url_for("compras_homologacao.self_assessment_page", token=token, _external=True)
    try:
        _enviar_email_self_assessment(homologacao, link, convite.email, _dt(convite.expira_em))
        email_erro = None
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Falha ao enviar e-mail do self assessment (homologacao %s)", homologacao.id)
        email_erro = str(exc)

    mensagem = (f"Link enviado para {convite.email}." if not email_erro
                else f"Link gerado, mas o e-mail NÃO foi enviado: {email_erro}. Copie o link e envie ao fornecedor.")
    return jsonify({
        "message": mensagem,
        "email_enviado": not email_erro,
        "link": link,
        "homologacao": _fmt(homologacao, completo=True),
    })


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/cancelar-fornecedor", methods=["POST"])
@permission_required(PERMISSION)
def api_cancelar_fornecedor(homologacao_id):
    return _acao(homologacao_id, svc.cancelar_envio_fornecedor,
                 "Envio cancelado - o link do fornecedor foi desativado.", usuario=_usuario())


@compras_homologacao_bp.route("/api/compras/homologacao/evidencias/<int:evidencia_id>", methods=["GET"])
@permission_required(PERMISSION)
def api_baixar_evidencia(evidencia_id):
    evidencia = db.session.get(ComprasHomologacaoEvidencia, evidencia_id)
    if not evidencia or not evidencia.dados:
        return jsonify({"error": "Evidência não encontrada."}), 404
    return _enviar_evidencia(evidencia)


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/evidencias", methods=["POST"])
@permission_required(PERMISSION)
def api_anexar_evidencia_interna(homologacao_id):
    """Comprador anexa a evidencia de um item (documento recebido por fora do link)."""
    homologacao = _obter(homologacao_id)
    if not homologacao:
        return jsonify({"error": "Homologação não encontrada."}), 404
    try:
        svc.anexar_evidencia_interna(
            homologacao, request.form.get("secao"), request.form.get("item"),
            request.files.get("arquivo"), _usuario(),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Evidência anexada.", "homologacao": _fmt(homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/evidencias/<int:evidencia_id>", methods=["DELETE"])
@permission_required(PERMISSION)
def api_remover_evidencia_interna(evidencia_id):
    evidencia = db.session.get(ComprasHomologacaoEvidencia, evidencia_id)
    if not evidencia:
        return jsonify({"error": "Evidência não encontrada."}), 404
    homologacao = evidencia.homologacao
    try:
        svc.remover_evidencia_interna(evidencia)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Evidência removida.", "homologacao": _fmt(homologacao, completo=True)})


def _enviar_evidencia(evidencia: ComprasHomologacaoEvidencia):
    return send_file(
        BytesIO(evidencia.dados), mimetype=evidencia.content_type or "application/octet-stream",
        as_attachment=False, download_name=evidencia.nome_arquivo or f"evidencia_{evidencia.id}",
    )


# ── Self assessment: link publico do fornecedor (sem login) ─────────────
def _convite_ou_404(token):
    convite = svc.obter_convite_por_token(token)
    if not convite:
        return None, (jsonify({"error": "Link inválido."}), 404)
    return convite, None


def _payload_publico(convite, token: str) -> dict:
    homologacao = convite.homologacao
    return {
        "editavel": svc.convite_aceita_edicao(convite),
        "situacao": svc.situacao_convite(convite),
        "expira_em": _dt(convite.expira_em),
        "respondido_em": _dt(convite.respondido_em),
        "codigo": svc.formulario_da(homologacao).CODIGO_FORMULARIO,
        "formulario": _formulario_payload(svc.formulario_da(homologacao)),
        "exigem_evidencia": list(form.RESPOSTAS_EXIGEM_EVIDENCIA),
        "max_evidencia_mb": svc.MAX_EVIDENCIA_BYTES // (1024 * 1024),
        "cadastro": {
            "razao_social": homologacao.razao_social,
            "cnpj": homologacao.cnpj,
            "nome_fantasia": homologacao.nome_fantasia,
            "inscricao_estadual": homologacao.inscricao_estadual,
            "endereco": homologacao.endereco,
            "cidade_estado": homologacao.cidade_estado,
        },
        "contato": {campo: getattr(homologacao, campo) for campo in svc.CAMPOS_FORNECEDOR},
        # O banco so' tem o hash: a URL de download usa o token da propria requisicao.
        "secoes": _secoes_payload(homologacao, f"/api/homologacao-fornecedor/{token}/evidencias/{{id}}"),
        **_dados_rev04(homologacao, f"/api/homologacao-fornecedor/{token}/evidencias/{{id}}"),
    }


@compras_homologacao_bp.route("/homologacao-fornecedor/<token>")
def self_assessment_page(token):
    if not svc.obter_convite_por_token(token):
        return render_template("acesso_negado.html"), 404
    return render_template("compras_homologacao_publica.html", token=token)


@compras_homologacao_bp.route("/api/homologacao-fornecedor/<token>", methods=["GET"])
def api_self_assessment_dados(token):
    convite, erro = _convite_ou_404(token)
    if erro:
        return erro
    return jsonify(_payload_publico(convite, token))


@compras_homologacao_bp.route("/api/homologacao-fornecedor/<token>", methods=["PUT"])
def api_self_assessment_salvar(token):
    convite, erro = _convite_ou_404(token)
    if erro:
        return erro
    try:
        svc.salvar_self_assessment(convite, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Rascunho salvo.", **_payload_publico(convite, token)})


@compras_homologacao_bp.route("/api/homologacao-fornecedor/<token>/enviar", methods=["POST"])
def api_self_assessment_enviar(token):
    convite, erro = _convite_ou_404(token)
    if erro:
        return erro
    try:
        homologacao = svc.concluir_self_assessment(convite, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    # O envio do fornecedor ja' foi gravado: falha no aviso so' vai pro log.
    try:
        _avisar_comprador_resposta(homologacao, convite)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Falha ao avisar o comprador da resposta do fornecedor (homologacao %s)", homologacao.id)
    return jsonify({"message": "Questionário enviado com sucesso. Obrigado!", **_payload_publico(convite, token)})


def _avisar_comprador_resposta(homologacao: Homologacao, convite) -> None:
    """E-mail pro comprador que mandou o link (ou, na falta dele, quem criou
    a homologacao). Sem e-mail no cadastro do usuario, nao ha' pra quem mandar."""
    from sqlalchemy import func

    from ..models import Usuario

    destinatario = None
    for username in (convite.enviado_por, homologacao.criado_por):
        if not username:
            continue
        usuario = Usuario.query.filter(func.lower(Usuario.username) == username.strip().lower()).first()
        if usuario and usuario.email:
            destinatario = usuario.email.strip()
            break
    if not destinatario:
        current_app.logger.warning("Homologacao %s respondida, mas o comprador nao tem e-mail cadastrado.", homologacao.id)
        return

    link = url_for("compras_homologacao.homologacao_page", _external=True)
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Autoavaliação respondida – {homologacao.razao_social}"
    msg["From"] = f"{current_app.config.get('MAIL_SENDER_NAME', 'Columbia Sync')} <{current_app.config.get('MAIL_SENDER', '')}>"
    msg["To"] = destinatario
    nota = f"{(homologacao.nota or 0) * 100:.1f}".replace(".", ",")
    corpo = (
        f"<p>Olá,</p>"
        f"<p>O fornecedor <strong>{html.escape(homologacao.razao_social or '')}</strong> "
        f"({html.escape(homologacao.cnpj or '')}) respondeu a autoavaliação de homologação "
        f"({svc.formulario_da(homologacao).CODIGO_FORMULARIO}) em {_dt(convite.respondido_em)}.</p>"
        f"<p>Nota calculada pelas respostas do fornecedor: <strong>{nota}%</strong> "
        f"({html.escape(homologacao.classificacao or '')}).</p>"
        f"<p>A homologação voltou para <strong>Rascunho</strong>: revise as respostas e as evidências "
        f"e envie para aprovação.</p>"
        f"<p><a href=\"{link}\">{link}</a></p>"
        f"<p>Columbia Sync</p>"
    )
    msg.attach(MIMEText(corpo, "html", "utf-8"))
    enviar_mensagem_smtp(current_app, msg)


@compras_homologacao_bp.route("/api/homologacao-fornecedor/<token>/evidencias", methods=["POST"])
def api_self_assessment_anexar(token):
    convite, erro = _convite_ou_404(token)
    if erro:
        return erro
    # Rota publica e o app nao tem MAX_CONTENT_LENGTH: recusa upload gigante
    # antes de o Werkzeug ler o corpo inteiro.
    if (request.content_length or 0) > svc.MAX_EVIDENCIA_BYTES + 64 * 1024:
        return jsonify({"error": "Arquivo muito grande (máximo 10 MB)."}), 400
    try:
        svc.anexar_evidencia(convite, request.form.get("secao"), request.form.get("item"), request.files.get("arquivo"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Evidência anexada.", **_payload_publico(convite, token)})


@compras_homologacao_bp.route("/api/homologacao-fornecedor/<token>/evidencias/<int:evidencia_id>", methods=["GET"])
def api_self_assessment_baixar(token, evidencia_id):
    convite, erro = _convite_ou_404(token)
    if erro:
        return erro
    evidencia = svc.evidencia_do_convite(convite, evidencia_id)
    if not evidencia or not evidencia.dados:
        return jsonify({"error": "Evidência não encontrada."}), 404
    return _enviar_evidencia(evidencia)


@compras_homologacao_bp.route("/api/homologacao-fornecedor/<token>/evidencias/<int:evidencia_id>", methods=["DELETE"])
def api_self_assessment_remover(token, evidencia_id):
    convite, erro = _convite_ou_404(token)
    if erro:
        return erro
    evidencia = svc.evidencia_do_convite(convite, evidencia_id)
    if not evidencia:
        return jsonify({"error": "Evidência não encontrada."}), 404
    try:
        svc.remover_evidencia(convite, evidencia)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Evidência removida.", **_payload_publico(convite, token)})


# ── Plano de acao (fase 2): lado do comprador ───────────────────────────
def _acao_plano(plano_id, funcao, mensagem, **kwargs):
    plano = plano_svc.obter(plano_id)
    if not plano:
        return jsonify({"error": "Plano de ação não encontrado."}), 404
    try:
        funcao(plano, **kwargs)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": mensagem, "homologacao": _fmt(plano.homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/<int:homologacao_id>/plano-acao", methods=["POST"])
@permission_required(PERMISSION)
def api_plano_criar(homologacao_id):
    def criar(homologacao, usuario):
        plano_svc.criar_manual(homologacao, usuario)
        return homologacao

    return _acao(homologacao_id, criar, "Plano de ação criado - gere o relatório e envie ao fornecedor.",
                 usuario=_usuario())


@compras_homologacao_bp.route("/api/compras/homologacao/planos/<int:plano_id>/gerar", methods=["POST"])
@permission_required(PERMISSION)
def api_plano_gerar(plano_id):
    plano = plano_svc.obter(plano_id)
    if not plano:
        return jsonify({"error": "Plano de ação não encontrado."}), 404
    try:
        plano_svc.gerar_relatorio(plano)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    mensagem = ("Relatório gerado pela IA - revise antes de enviar." if plano.relatorio_origem == "ia"
                else "IA indisponível: relatório gerado pelo modelo padrão - revise antes de enviar.")
    return jsonify({"message": mensagem, "homologacao": _fmt(plano.homologacao, completo=True)})


@compras_homologacao_bp.route("/api/compras/homologacao/planos/<int:plano_id>", methods=["PUT"])
@permission_required(PERMISSION)
def api_plano_salvar(plano_id):
    return _acao_plano(plano_id, plano_svc.salvar, "Plano de ação salvo.", dados=request.get_json(silent=True) or {})


@compras_homologacao_bp.route("/api/compras/homologacao/planos/<int:plano_id>/itens/<int:item_id>/aceitar", methods=["POST"])
@permission_required(PERMISSION)
def api_plano_aceitar(plano_id, item_id):
    return _acao_plano(plano_id, plano_svc.aceitar_item, "Evidência aceita.", item_id=item_id, usuario=_usuario())


@compras_homologacao_bp.route("/api/compras/homologacao/planos/<int:plano_id>/itens/<int:item_id>/recusar", methods=["POST"])
@permission_required(PERMISSION)
def api_plano_recusar(plano_id, item_id):
    payload = request.get_json(silent=True) or {}
    return _acao_plano(plano_id, plano_svc.recusar_item, "Evidência recusada - o item voltou para o fornecedor.",
                       item_id=item_id, usuario=_usuario(), motivo=payload.get("motivo") or "")


@compras_homologacao_bp.route("/api/compras/homologacao/planos/evidencias/<int:evidencia_id>", methods=["GET"])
@permission_required(PERMISSION)
def api_plano_baixar_evidencia(evidencia_id):
    from ..models import ComprasHomologacaoPlanoEvidencia

    ev = db.session.get(ComprasHomologacaoPlanoEvidencia, evidencia_id)
    if not ev or not ev.dados:
        return jsonify({"error": "Evidência não encontrada."}), 404
    return _enviar_evidencia(ev)


def _enviar_email_plano(plano, link: str) -> None:
    homologacao = plano.homologacao
    paragrafos = "".join(f"<p>{html.escape(p)}</p>" for p in (plano.relatorio or "").split("\n") if p.strip())
    linhas = "".join(
        f"<tr><td style='padding:6px;border:1px solid #ddd;'>{html.escape(it.texto)}</td>"
        f"<td style='padding:6px;border:1px solid #ddd;'>{html.escape(it.motivo)}</td>"
        f"<td style='padding:6px;border:1px solid #ddd;'>{html.escape(it.acao or '')}</td>"
        f"<td style='padding:6px;border:1px solid #ddd;white-space:nowrap;'>{it.prazo.strftime('%d/%m/%Y') if it.prazo else ''}</td></tr>"
        for it in plano.itens
    )
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Plano de Ação – Avaliação de Fornecedor – {homologacao.razao_social} – Columbia Machine Brasil"
    msg["From"] = f"{current_app.config.get('MAIL_SENDER_NAME', 'Columbia Sync')} <{current_app.config.get('MAIL_SENDER', '')}>"
    msg["To"] = plano.email
    corpo = (
        f"{paragrafos}"
        f"<table style='border-collapse:collapse;font-size:13px;'>"
        f"<tr><th style='padding:6px;border:1px solid #ddd;'>Requisito</th><th style='padding:6px;border:1px solid #ddd;'>Situação</th>"
        f"<th style='padding:6px;border:1px solid #ddd;'>Ação esperada</th><th style='padding:6px;border:1px solid #ddd;'>Prazo</th></tr>"
        f"{linhas}</table>"
        f"<p>Envie as evidências de cada item pelo link de acompanhamento:</p>"
        f"<p><a href=\"{link}\">{link}</a></p>"
        f"<p>Atenciosamente,</p><p>Compras – Columbia Machine Brasil</p>"
    )
    msg.attach(MIMEText(corpo, "html", "utf-8"))
    enviar_mensagem_smtp(current_app, msg)


@compras_homologacao_bp.route("/api/compras/homologacao/planos/<int:plano_id>/enviar", methods=["POST"])
@permission_required(PERMISSION)
def api_plano_enviar(plano_id):
    """Envia (ou reenvia, com link novo) o plano ao fornecedor. Se o e-mail
    falhar, o link volta na resposta pro comprador repassar."""
    plano = plano_svc.obter(plano_id)
    if not plano:
        return jsonify({"error": "Plano de ação não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        token = plano_svc.enviar(plano, payload.get("email") or "", _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    link = url_for("compras_homologacao.plano_fornecedor_page", token=token, _external=True)
    try:
        _enviar_email_plano(plano, link)
        email_erro = None
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("Falha ao enviar e-mail do plano de acao %s", plano.id)
        email_erro = str(exc)
    mensagem = (f"Plano de ação enviado para {plano.email}." if not email_erro
                else f"Link gerado, mas o e-mail NÃO foi enviado: {email_erro}. Copie o link e envie ao fornecedor.")
    return jsonify({
        "message": mensagem, "email_enviado": not email_erro, "link": link,
        "homologacao": _fmt(plano.homologacao, completo=True),
    })


# ── Plano de acao: link publico do fornecedor (sem login) ───────────────
def _plano_ou_404(token):
    plano = plano_svc.obter_por_token(token)
    if not plano:
        return None, (jsonify({"error": "Link inválido."}), 404)
    return plano, None


def _payload_plano_publico(plano, token: str) -> dict:
    homologacao = plano.homologacao
    return {
        "editavel": plano_svc.aceita_fornecedor(plano),
        "razao_social": homologacao.razao_social,
        "cnpj": homologacao.cnpj,
        "max_evidencia_mb": svc.MAX_EVIDENCIA_BYTES // (1024 * 1024),
        "plano": _fmt_plano(plano, f"/api/plano-acao-fornecedor/{token}/evidencias/{{id}}"),
    }


@compras_homologacao_bp.route("/plano-acao-fornecedor/<token>")
def plano_fornecedor_page(token):
    if not plano_svc.obter_por_token(token):
        return render_template("acesso_negado.html"), 404
    return render_template("compras_homologacao_plano_publico.html", token=token)


@compras_homologacao_bp.route("/api/plano-acao-fornecedor/<token>", methods=["GET"])
def api_plano_fornecedor_dados(token):
    plano, erro = _plano_ou_404(token)
    if erro:
        return erro
    return jsonify(_payload_plano_publico(plano, token))


@compras_homologacao_bp.route("/api/plano-acao-fornecedor/<token>/itens/<int:item_id>", methods=["PUT"])
def api_plano_fornecedor_comentar(token, item_id):
    plano, erro = _plano_ou_404(token)
    if erro:
        return erro
    try:
        plano_svc.comentar(plano, item_id, (request.get_json(silent=True) or {}).get("comentario") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Comentário salvo.", **_payload_plano_publico(plano, token)})


@compras_homologacao_bp.route("/api/plano-acao-fornecedor/<token>/itens/<int:item_id>/evidencias", methods=["POST"])
def api_plano_fornecedor_anexar(token, item_id):
    plano, erro = _plano_ou_404(token)
    if erro:
        return erro
    # Rota publica e o app nao tem MAX_CONTENT_LENGTH: recusa upload gigante
    # antes de o Werkzeug ler o corpo inteiro.
    if (request.content_length or 0) > svc.MAX_EVIDENCIA_BYTES + 64 * 1024:
        return jsonify({"error": "Arquivo muito grande (máximo 10 MB)."}), 400
    try:
        plano_svc.anexar_evidencia(plano, item_id, request.files.get("arquivo"), request.form.get("comentario") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Evidência enviada para análise da Columbia.", **_payload_plano_publico(plano, token)})


@compras_homologacao_bp.route("/api/plano-acao-fornecedor/<token>/evidencias/<int:evidencia_id>", methods=["GET"])
def api_plano_fornecedor_baixar(token, evidencia_id):
    plano, erro = _plano_ou_404(token)
    if erro:
        return erro
    ev = plano_svc.evidencia(plano, evidencia_id)
    if not ev or not ev.dados:
        return jsonify({"error": "Evidência não encontrada."}), 404
    return _enviar_evidencia(ev)


@compras_homologacao_bp.route("/api/plano-acao-fornecedor/<token>/evidencias/<int:evidencia_id>", methods=["DELETE"])
def api_plano_fornecedor_remover(token, evidencia_id):
    plano, erro = _plano_ou_404(token)
    if erro:
        return erro
    try:
        plano_svc.remover_evidencia(plano, evidencia_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Evidência removida.", **_payload_plano_publico(plano, token)})
