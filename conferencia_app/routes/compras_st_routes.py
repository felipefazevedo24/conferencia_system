"""Compras > Servico terceiro: cotacao com desenho tecnico (Engenharia cria,
fornecedor responde pelo link publico, Compras escolhe). Regras em
services/compras_st_service.py."""
from __future__ import annotations

import html
import json
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, session, url_for

from ..auth import has_permission, is_admin_session, permission_required, permission_required_any
from ..extensions import db
from ..models import CompraStCotacao, CompraStDesenho, CompraStFornecedor, CompraStResposta, CompraStTipoServico, Usuario
from ..services import compras_st_service as svc
from ..services.smtp_service import enviar_mensagem_smtp
from .comex_routes import _remetente_comex, _separar_emails

compras_st_bp = Blueprint("compras_st", __name__)

PERM_COTACAO = "PAGE_COMPRAS_ST_COTACAO"
PERM_ANALISE = "PAGE_COMPRAS_ST_ANALISE"
# Desenhos anexados ao e-mail ate' este total; acima disso o e-mail avisa
# para baixar pelo link (servidor de e-mail costuma recusar mensagem grande).
MAX_ANEXOS_EMAIL = 15 * 1024 * 1024


def _usuario() -> str:
    return session.get("username", "desconhecido")


def _pode_analisar() -> bool:
    return is_admin_session() or has_permission(PERM_ANALISE)


# ── Payloads ───────────────────────────────────────────────────────────
def _tipo_payload(t: CompraStTipoServico) -> dict:
    return {"id": t.id, "nome": t.nome, "ativo": t.ativo}


def _fornecedor_payload(f: CompraStFornecedor) -> dict:
    h = f.homologacao
    return {
        "id": f.id, "nome": svc.nome_fornecedor(f), "razao_social": h.razao_social if h else None,
        "cnpj": h.cnpj if h else None, "homologacao_id": f.homologacao_id,
        "homologacao_status": h.status if h else None,
        "homologacao_valido_ate": h.valido_ate.strftime("%d/%m/%Y") if h and h.valido_ate else None,
        "emails": f.emails, "contato": f.contato, "telefone": f.telefone, "ativo": f.ativo,
        "tipos": [_tipo_payload(t) for t in f.tipos],
        "motivo_bloqueio": svc.motivo_nao_autorizado(f),
    }


def _desenho_payload(d: CompraStDesenho, url: str) -> dict:
    return {"id": d.id, "nome": d.nome_arquivo, "tamanho": d.tamanho, "url": url}


def _item_payload(i, url_desenho) -> dict:
    return {"id": i.id, "ordem": i.ordem, "codigo": i.codigo, "descricao": i.descricao, "quantidade": i.quantidade,
            "n_os": i.n_os, "desenhos": [_desenho_payload(d, url_desenho(d)) for d in i.desenhos]}


def _resposta_payload(r: CompraStResposta) -> dict:
    return {
        "id": r.id, "fornecedor": svc.nome_fornecedor(r.fornecedor), "emails": r.emails, "status": r.status,
        "expirado": r.status == "Pendente" and not svc.link_valido(r) and r.cotacao.status == "Aberta",
        "email_enviado": r.email_enviado, "email_erro": r.email_erro,
        "recebida_em": r.recebida_em.strftime("%d/%m/%Y %H:%M") if r.recebida_em else None,
        "prazo_entrega": r.prazo_entrega, "condicao_pagamento": r.condicao_pagamento,
        "validade_proposta": r.validade_proposta.strftime("%d/%m/%Y") if r.validade_proposta else None,
        "frete_tipo": r.frete_tipo, "frete_valor": r.frete_valor, "observacoes": r.observacoes,
        "proposta_url": url_for("compras_st.api_baixar_proposta", resposta_id=r.id) if r.proposta_arquivo else None,
        "proposta_nome": r.proposta_nome,
        "total_net": r.total_net, "total_bruto": r.total_bruto, "divergencia_bruto": bool(r.divergencia_bruto),
        "is_sugerida": bool(r.is_sugerida), "is_vencedora": r.cotacao.vencedora_id == r.id,
        "itens": [{"item_id": l.item_id, "preco_net": l.preco_net, "ipi_pct": l.ipi_pct, "icms_pct": l.icms_pct,
                   "iss_pct": l.iss_pct, "preco_bruto": l.preco_bruto, "bruto_calculado": l.bruto_calculado} for l in r.itens],
    }


def _cotacao_payload(c: CompraStCotacao, completo: bool = False) -> dict:
    dados = {
        "id": c.id, "numero": f"ST-{c.id:04d}", "tipo": c.tipo.nome if c.tipo else None, "status": c.status,
        "criado_em": c.criado_em.strftime("%d/%m/%Y %H:%M") if c.criado_em else None, "criado_por": c.criado_por,
        "qtd_itens": len(c.itens), "qtd_fornecedores": len(c.respostas),
        "qtd_recebidas": sum(1 for r in c.respostas if r.status == "Recebida"),
        "vencedora": svc.nome_fornecedor(c.vencedora.fornecedor) if c.vencedora else None,
        "cod_ordem_compra": c.cod_ordem_compra,
    }
    if completo:
        dados.update({
            "texto_fornecedor": c.texto_fornecedor, "justificativa": c.justificativa,
            "escolhida_em": c.escolhida_em.strftime("%d/%m/%Y %H:%M") if c.escolhida_em else None,
            "escolhida_por": c.escolhida_por,
            "itens": [_item_payload(i, lambda d: url_for("compras_st.api_baixar_desenho", desenho_id=d.id)) for i in c.itens],
            "respostas": [_resposta_payload(r) for r in c.respostas],
        })
    return dados


# ── E-mails ────────────────────────────────────────────────────────────
def _email_usuario(username: str | None) -> list[str]:
    u = Usuario.query.filter_by(username=username).first() if username else None
    return [u.email] if u and u.email else []


def _enviar_email_cotacao(resposta: CompraStResposta, link: str) -> None:
    c = resposta.cotacao
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Solicitação de Cotação – Serviço de {c.tipo.nome} – ST-{c.id:04d} – Columbia Machine Brasil"
    remetente, smtp_kwargs = _remetente_comex()
    msg["From"] = remetente
    msg["To"] = ", ".join(_separar_emails(resposta.emails))
    copias = _email_usuario(c.criado_por)
    if copias:
        msg["Cc"] = ", ".join(copias)
    linhas = "".join(
        f"<tr><td>{i.ordem}</td><td>{html.escape(i.codigo or '')}</td><td>{html.escape(i.descricao)}</td>"
        f"<td style='text-align:right'>{i.quantidade:g}</td></tr>" for i in c.itens
    )
    desenhos = [d for i in c.itens for d in i.desenhos]
    total = sum(d.tamanho or 0 for d in desenhos)
    anexar = total <= MAX_ANEXOS_EMAIL
    texto = html.escape(c.texto_fornecedor or "").replace("\n", "<br>")
    corpo = (
        f"<p>Prezados,</p>"
        f"{f'<p>{texto}</p>' if texto else ''}"
        f"<p>Solicitamos cotação do serviço de <strong>{html.escape(c.tipo.nome)}</strong> para as peças abaixo:</p>"
        f"<table border='1' cellpadding='4' cellspacing='0' style='border-collapse:collapse'>"
        f"<tr><th>#</th><th>Código</th><th>Descrição</th><th>Qtde</th></tr>{linhas}</table>"
        f"<p>Os desenhos técnicos {'seguem em anexo e também estão' if anexar else 'estão'} disponíveis no link abaixo, "
        f"onde a cotação deve ser preenchida (preço por peça, impostos, prazo e condições):</p>"
        f"<p><a href=\"{link}\">{link}</a></p>"
        f"<p>Atenciosamente,</p><p>Columbia Machine Brasil</p>"
    )
    msg.attach(MIMEText(corpo, "html", "utf-8"))
    if anexar:
        for d in desenhos:
            parte = MIMEApplication(d.conteudo, Name=d.nome_arquivo)
            parte.add_header("Content-Disposition", "attachment", filename=d.nome_arquivo)
            msg.attach(parte)
    enviar_mensagem_smtp(current_app, msg, **smtp_kwargs)


def _enviar_email_vencedor(c: CompraStCotacao) -> None:
    r = c.vencedora
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Resultado da Cotação – Serviço de {c.tipo.nome} – ST-{c.id:04d}"
    remetente, smtp_kwargs = _remetente_comex()
    msg["From"] = remetente
    msg["To"] = ", ".join(_separar_emails(r.emails))
    copias = _email_usuario(c.criado_por) + _email_usuario(c.escolhida_por)
    if copias:
        msg["Cc"] = ", ".join(dict.fromkeys(copias))
    corpo = (
        f"<p>Prezados,</p>"
        f"<p>Agradecemos o envio da cotação ST-{c.id:04d} (serviço de {html.escape(c.tipo.nome)}).</p>"
        f"<p>Informamos que a proposta da <strong>{html.escape(svc.nome_fornecedor(r.fornecedor))}</strong> foi selecionada. "
        f"O pedido de compra será enviado em seguida.</p>"
        f"<p>Atenciosamente,</p><p>Columbia Machine Brasil</p>"
    )
    msg.attach(MIMEText(corpo, "html", "utf-8"))
    enviar_mensagem_smtp(current_app, msg, **smtp_kwargs)


# ── Tela interna ───────────────────────────────────────────────────────
@compras_st_bp.route("/compras/servico-terceiro")
@permission_required_any(PERM_COTACAO, PERM_ANALISE)
def page_servico_terceiro():
    return render_template("compras_servico_terceiro.html", user=session.get("username"), pode_analisar=_pode_analisar(),
                           pode_cotar=is_admin_session() or has_permission(PERM_COTACAO))


@compras_st_bp.route("/api/compras/st/tipos")
@permission_required_any(PERM_COTACAO, PERM_ANALISE)
def api_tipos():
    return jsonify({"tipos": [_tipo_payload(t) for t in svc.listar_tipos()]})


@compras_st_bp.route("/api/compras/st/tipos", methods=["POST"])
@compras_st_bp.route("/api/compras/st/tipos/<int:tipo_id>", methods=["PUT"])
@permission_required(PERM_ANALISE)
def api_salvar_tipo(tipo_id=None):
    dados = request.get_json(silent=True) or {}
    try:
        if tipo_id and "ativo" in dados and "nome" not in dados:
            tipo = db.session.get(CompraStTipoServico, tipo_id)
            if not tipo:
                return jsonify({"error": "Tipo não encontrado."}), 404
            tipo = svc.alternar_tipo(tipo, dados["ativo"])
        else:
            tipo = svc.salvar_tipo(dados.get("nome"), _usuario(), tipo_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Tipo de serviço salvo.", "tipo": _tipo_payload(tipo)})


@compras_st_bp.route("/api/compras/st/homologacoes")
@permission_required(PERM_ANALISE)
def api_homologacoes():
    return jsonify({"homologacoes": [
        {"id": h.id, "razao_social": h.razao_social, "nome_fantasia": h.nome_fantasia, "cnpj": h.cnpj, "email": h.email,
         "contato": h.contato_principal, "telefone": h.telefone,
         "valido_ate": h.valido_ate.strftime("%d/%m/%Y") if h.valido_ate else None}
        for h in svc.homologacoes_para_vincular()
    ]})


@compras_st_bp.route("/api/compras/st/fornecedores")
@permission_required_any(PERM_COTACAO, PERM_ANALISE)
def api_fornecedores():
    tipo_id = request.args.get("tipo_id", type=int)
    fornecedores = svc.fornecedores_do_tipo(tipo_id) if tipo_id else svc.listar_fornecedores()
    return jsonify({"fornecedores": [_fornecedor_payload(f) for f in fornecedores]})


@compras_st_bp.route("/api/compras/st/fornecedores", methods=["POST"])
@compras_st_bp.route("/api/compras/st/fornecedores/<int:fornecedor_id>", methods=["PUT"])
@permission_required(PERM_ANALISE)
def api_salvar_fornecedor(fornecedor_id=None):
    try:
        f = svc.salvar_fornecedor(request.get_json(silent=True) or {}, _usuario(), fornecedor_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Fornecedor salvo.", "fornecedor": _fornecedor_payload(f)})


@compras_st_bp.route("/api/compras/st/produto")
@permission_required_any(PERM_COTACAO, PERM_ANALISE)
def api_produto():
    """Descricao do produto no CPS. Integracao nunca derruba a tela: falhou
    ou nao achou, a engenharia digita."""
    codigo = str(request.args.get("codigo") or "").strip().upper()
    if not codigo:
        return jsonify({"descricao": None})
    try:
        from ..services.erp_estoque_service import buscar_estoque_produtos_grv
        dados = buscar_estoque_produtos_grv([codigo])
        item = (dados.get("por_codigo") or {}).get(codigo) or {}
    except Exception:
        current_app.logger.warning("Servico terceiro: consulta do produto %s no CPS falhou", codigo, exc_info=True)
        return jsonify({"descricao": None, "aviso": "Não consegui consultar o CPS - digite a descrição."})
    if not item.get("item"):
        return jsonify({"descricao": None, "aviso": "Código não encontrado no CPS - digite a descrição."})
    return jsonify({"descricao": item["item"]})


@compras_st_bp.route("/api/compras/st/cotacoes")
@permission_required_any(PERM_COTACAO, PERM_ANALISE)
def api_cotacoes():
    return jsonify({"cotacoes": [_cotacao_payload(c) for c in svc.listar_cotacoes()]})


@compras_st_bp.route("/api/compras/st/cotacoes/<int:cotacao_id>")
@permission_required_any(PERM_COTACAO, PERM_ANALISE)
def api_cotacao(cotacao_id):
    c = db.session.get(CompraStCotacao, cotacao_id)
    if not c:
        return jsonify({"error": "Cotação não encontrada."}), 404
    return jsonify({"cotacao": _cotacao_payload(c, completo=True)})


@compras_st_bp.route("/api/compras/st/cotacoes", methods=["POST"])
@permission_required(PERM_COTACAO)
def api_criar_cotacao():
    """multipart: `payload` (JSON) + um arquivo por campo; cada item cita os
    nomes dos campos dos seus desenhos em `arquivos`."""
    try:
        dados = json.loads(request.form.get("payload") or "{}")
    except ValueError:
        return jsonify({"error": "Dados inválidos."}), 400
    arquivos = {chave: (f.filename, f.mimetype, f.read()) for chave, f in request.files.items()}
    try:
        cotacao, links = svc.criar_cotacao(dados, arquivos, _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    falhas = []
    for resposta, token in links:
        link = url_for("compras_st.page_publica", token=token, _external=True)
        try:
            _enviar_email_cotacao(resposta, link)
            resposta.email_enviado, resposta.email_erro = True, None
        except Exception as exc:
            current_app.logger.exception("Servico terceiro: falha no e-mail da cotacao ST-%s", cotacao.id)
            resposta.email_enviado, resposta.email_erro = False, str(exc)[:500]
            falhas.append(svc.nome_fornecedor(resposta.fornecedor))
    db.session.commit()
    mensagem = f"Cotação ST-{cotacao.id:04d} criada e enviada para {len(links) - len(falhas)} fornecedor(es)."
    if falhas:
        mensagem += f" Falha no e-mail de: {', '.join(falhas)}."
    return jsonify({"message": mensagem, "falhas": falhas, "cotacao": _cotacao_payload(cotacao, completo=True)})


@compras_st_bp.route("/api/compras/st/desenhos/<int:desenho_id>")
# Expedicao tambem baixa: a ordem do Servico Terceiro mostra os desenhos.
@permission_required_any(PERM_COTACAO, PERM_ANALISE, "PAGE_EXPEDICAO_CONF_CEGA")
def api_baixar_desenho(desenho_id):
    d = db.session.get(CompraStDesenho, desenho_id)
    if not d:
        return jsonify({"error": "Desenho não encontrado."}), 404
    return send_file(BytesIO(d.conteudo), mimetype=d.content_type or "application/octet-stream", download_name=d.nome_arquivo)


@compras_st_bp.route("/api/compras/st/respostas/<int:resposta_id>/proposta")
@permission_required_any(PERM_COTACAO, PERM_ANALISE)
def api_baixar_proposta(resposta_id):
    r = db.session.get(CompraStResposta, resposta_id)
    if not r or not r.proposta_arquivo:
        return jsonify({"error": "Proposta não encontrada."}), 404
    return send_file(BytesIO(r.proposta_arquivo), mimetype=r.proposta_content_type or "application/octet-stream",
                     download_name=r.proposta_nome or "proposta")


@compras_st_bp.route("/api/compras/st/cotacoes/<int:cotacao_id>/escolher", methods=["POST"])
@permission_required(PERM_ANALISE)
def api_escolher(cotacao_id):
    c = db.session.get(CompraStCotacao, cotacao_id)
    if not c:
        return jsonify({"error": "Cotação não encontrada."}), 404
    dados = request.get_json(silent=True) or {}
    try:
        svc.escolher(c, int(dados.get("resposta_id") or 0), dados.get("justificativa"), _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    mensagem = "Vencedor registrado e avisado por e-mail."
    try:
        _enviar_email_vencedor(c)
    except Exception as exc:
        current_app.logger.exception("Servico terceiro: falha no e-mail do vencedor ST-%s", c.id)
        mensagem = f"Vencedor registrado, mas o e-mail NÃO foi enviado: {exc}"
    return jsonify({"message": mensagem, "cotacao": _cotacao_payload(c, completo=True)})


@compras_st_bp.route("/api/compras/st/cotacoes/<int:cotacao_id>/oc", methods=["POST"])
@permission_required(PERM_ANALISE)
def api_informar_oc(cotacao_id):
    c = db.session.get(CompraStCotacao, cotacao_id)
    if not c:
        return jsonify({"error": "Cotação não encontrada."}), 404
    try:
        svc.informar_oc(c, (request.get_json(silent=True) or {}).get("cod_ordem_compra"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "OC registrada.", "cotacao": _cotacao_payload(c, completo=True)})


@compras_st_bp.route("/api/compras/st/cotacoes/<int:cotacao_id>/cancelar", methods=["POST"])
@permission_required_any(PERM_COTACAO, PERM_ANALISE)
def api_cancelar(cotacao_id):
    c = db.session.get(CompraStCotacao, cotacao_id)
    if not c:
        return jsonify({"error": "Cotação não encontrada."}), 404
    try:
        svc.cancelar(c)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Cotação cancelada.", "cotacao": _cotacao_payload(c, completo=True)})


@compras_st_bp.route("/api/compras/st/ordem-st/<path:cod_ordem_compra>")
@permission_required_any(PERM_COTACAO, PERM_ANALISE, "PAGE_EXPEDICAO_CONF_CEGA")
def api_cotacoes_da_ordem_st(cod_ordem_compra):
    """Cotacoes ligadas a uma ordem do Servico Terceiro (pela OC ou pela OS
    dos itens) - a expedicao mostra os desenhos ao conferir."""
    from ..models import ExpedicaoOrdemST
    ordem = ExpedicaoOrdemST.query.filter_by(cod_ordem_compra=cod_ordem_compra, excluido=False).first()
    numeros_os = [it.n_os for it in ordem.itens] if ordem else []
    cotacoes = svc.cotacoes_da_ordem_st(cod_ordem_compra, numeros_os)
    return jsonify({"cotacoes": [_cotacao_payload(c, completo=True) for c in cotacoes]})


# ── Link publico (sem login) ───────────────────────────────────────────
@compras_st_bp.route("/servico-terceiro/cotacao/<token>")
def page_publica(token):
    if not svc.resposta_por_token(token):
        return render_template("acesso_negado.html"), 404
    return render_template("compras_st_cotacao_publica.html", token=token)


@compras_st_bp.route("/api/st-cotacao/<token>")
def api_publica_dados(token):
    r = svc.resposta_por_token(token)
    if not r:
        return jsonify({"error": "Link inválido."}), 404
    c = r.cotacao
    return jsonify({
        "valido": svc.link_valido(r), "status": r.status, "numero": f"ST-{c.id:04d}", "tipo": c.tipo.nome,
        "fornecedor": svc.nome_fornecedor(r.fornecedor), "texto": c.texto_fornecedor,
        "itens": [_item_payload(i, lambda d: url_for("compras_st.api_publica_desenho", token=token, desenho_id=d.id)) for i in c.itens],
    })


@compras_st_bp.route("/api/st-cotacao/<token>/desenho/<int:desenho_id>")
def api_publica_desenho(token, desenho_id):
    r = svc.resposta_por_token(token)
    d = db.session.get(CompraStDesenho, desenho_id)
    # So' desenho da propria cotacao, e enquanto o link vale.
    if not r or not d or d.item.cotacao_id != r.cotacao_id or not svc.link_valido(r):
        return jsonify({"error": "Desenho indisponível."}), 404
    return send_file(BytesIO(d.conteudo), mimetype=d.content_type or "application/octet-stream",
                     as_attachment=True, download_name=d.nome_arquivo)


@compras_st_bp.route("/api/st-cotacao/<token>", methods=["POST"])
def api_publica_enviar(token):
    r = svc.resposta_por_token(token)
    if not r:
        return jsonify({"error": "Link inválido."}), 404
    try:
        dados = json.loads(request.form.get("payload") or "{}")
    except ValueError:
        return jsonify({"error": "Dados inválidos."}), 400
    arquivo = request.files.get("proposta")
    proposta = (arquivo.filename, arquivo.mimetype, arquivo.read()) if arquivo else None
    try:
        svc.submeter_resposta(r, dados, proposta)
    except svc.DivergenciaBruto as exc:
        return jsonify({"error": str(exc), "confirmar": True}), 409
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Cotação enviada com sucesso. Obrigado!"})
