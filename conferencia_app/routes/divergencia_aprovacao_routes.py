"""Tela publica (fora do menu do Sync) para Compras aprovar/recusar uma
divergencia XML x Pedido de compra.

Seguranca em 2 fatores:
  1. TOKEN na URL (o link enviado no Teams) - so quem tem o link abre a NF.
  2. LOGIN + SENHA do proprio Sync na tela - so quem tem acesso de Compras
     (permissao PAGE_XML_AUDITOR) ou Admin consegue de fato aprovar/recusar.

A pagina mostra: cabecalho da NF, itens do XML, comparacao linha a linha
contra o pedido de compra (ordem de compra), e permite baixar o PDF (DANFE)
da NF-e. O botao Aprovar/Recusar so aparece apos o login e grava a decisao
no mesmo registro DivergenciaPedidoAprovacao usado pelo bloqueio da liberacao.
"""
from __future__ import annotations

import os
import re

from flask import (
    Blueprint,
    current_app,
    jsonify,
    render_template,
    request,
    session,
)
from datetime import datetime

from ..extensions import db
from ..models import DivergenciaPedidoAprovacao, ItemNota, Usuario
from ..auth import has_permission, is_admin_role
from ..services.pedidos_service import comparar_pedido_com_nf, PedidoERPIndisponivelError
from ..services.consyste_service import download_documento_consyste
from ..services.danfe_service import gerar_danfe, parse_nfe_xml


divergencia_aprovacao_bp = Blueprint("divergencia_aprovacao", __name__)


def _sessao_key(token: str) -> str:
    return f"divaprov:{token}"


def _carregar_registro(token: str) -> DivergenciaPedidoAprovacao | None:
    token = str(token or "").strip()
    if not token:
        return None
    return DivergenciaPedidoAprovacao.query.filter_by(token=token).first()


def _aprovador_logado(token: str) -> dict | None:
    """Retorna o aprovador autenticado nesta sessao para este token, ou None."""
    dados = session.get(_sessao_key(token))
    if isinstance(dados, dict) and dados.get("username"):
        return dados
    return None


def _itens_nf_para_comparacao(itens_db: list[ItemNota]) -> list[dict]:
    itens_nf = []
    for i in itens_db:
        qtd_original = float(i.qtd_real or 0)
        valor_total_linha = float(i.valor_produto or 0)
        valor_unit = round(valor_total_linha / qtd_original, 10) if qtd_original > 0 else 0.0
        itens_nf.append(
            {
                "item_id": i.id,
                "codigo": i.codigo or "---",
                "descricao": i.descricao or "---",
                "qtd": qtd_original,
                "qtd_original": qtd_original,
                "unidade_comercial": i.unidade_comercial or "UN",
                "conversao_fator": 1.0,
                "conversao_unidade": i.unidade_comercial or "UN",
                "conversao_manual": False,
                "linha_po_vinculada": i.linha_po_vinculada,
                "valor_unit": valor_unit,
                "valor_total_linha": valor_total_linha,
            }
        )
    return itens_nf


@divergencia_aprovacao_bp.route("/aprovar-divergencia/<token>")
def pagina_aprovacao(token):
    """Pagina standalone (sem menu do Sync). Valida so o token; o login e feito na propria tela."""
    registro = _carregar_registro(token)
    if not registro:
        return render_template("divergencia_aprovacao.html", token=token, invalido=True), 404
    return render_template("divergencia_aprovacao.html", token=token, invalido=False)


@divergencia_aprovacao_bp.route("/aprovar-divergencia/<token>/login", methods=["POST"])
def login_aprovacao(token):
    registro = _carregar_registro(token)
    if not registro:
        return jsonify({"sucesso": False, "msg": "Link de aprovação inválido ou expirado."}), 404

    data = request.get_json() or {}
    username = str(data.get("username") or "").strip().upper()
    password = str(data.get("password") or "")
    if not username or not password:
        return jsonify({"sucesso": False, "msg": "Informe usuário e senha."}), 400

    from ..routes.auth_routes import _password_matches_and_upgrade

    user = Usuario.query.filter_by(username=username).first()
    if not user or not bool(getattr(user, "ativo", True)) or not _password_matches_and_upgrade(user, password):
        return jsonify({"sucesso": False, "msg": "Usuário ou senha inválidos."}), 401
    db.session.commit()  # persiste possivel upgrade de hash de senha

    role = str(user.role or "")
    autorizado = is_admin_role(role) or has_permission("PAGE_XML_AUDITOR", username=username, role=role)
    if not autorizado:
        return jsonify({"sucesso": False, "msg": "Seu usuário não tem permissão de Compras para aprovar divergências."}), 403

    nome = str(getattr(user, "nome_exibicao", "") or user.username)
    session[_sessao_key(token)] = {"username": user.username, "role": role, "nome": nome}
    return jsonify({"sucesso": True, "nome": nome, "username": user.username})


@divergencia_aprovacao_bp.route("/aprovar-divergencia/<token>/dados", methods=["GET"])
def dados_aprovacao(token):
    registro = _carregar_registro(token)
    if not registro:
        return jsonify({"sucesso": False, "msg": "Link inválido."}), 404
    if not _aprovador_logado(token):
        return jsonify({"sucesso": False, "erro": "login_necessario"}), 401

    itens_db = (
        ItemNota.query.filter_by(numero_nota=registro.numero_nota)
        .order_by(ItemNota.id.asc())
        .all()
    )
    chave_acesso = ""
    itens_xml = []
    for i in itens_db:
        if not chave_acesso and i.chave_acesso:
            chave_acesso = re.sub(r"\D", "", str(i.chave_acesso))[:44]
        itens_xml.append(
            {
                "codigo": i.codigo or "---",
                "descricao": i.descricao or "---",
                "qtd": float(i.qtd_real or 0),
                "unidade": i.unidade_comercial or "UN",
                "valor_total": float(i.valor_produto or 0),
                "cfop": i.cfop or "",
                "ncm": i.ncm or "",
            }
        )

    comparacao = None
    erro_comparacao = None
    if registro.pedido_compra:
        try:
            comparacao = comparar_pedido_com_nf(
                registro.pedido_compra, _itens_nf_para_comparacao(itens_db)
            )
        except PedidoERPIndisponivelError as exc:
            erro_comparacao = f"ERP indisponível para comparar o pedido: {exc}"
        except Exception as exc:  # pragma: no cover - best effort
            erro_comparacao = f"Não foi possível comparar com o pedido: {exc}"

    nf_valor_total = 0.0
    fornecedor = registro.fornecedor or ""
    cnpj_emitente = ""
    data_emissao = ""
    for i in itens_db:
        nf_valor_total = float(i.valor_nf or 0) or nf_valor_total
        cnpj_emitente = cnpj_emitente or (i.cnpj_emitente or "")
        fornecedor = fornecedor or (i.fornecedor or "")
        if not data_emissao and i.data_emissao:
            data_emissao = i.data_emissao.strftime("%d/%m/%Y")

    return jsonify(
        {
            "sucesso": True,
            "registro": {
                "numero_nota": registro.numero_nota,
                "pedido_compra": registro.pedido_compra,
                "fornecedor": fornecedor,
                "cnpj_emitente": cnpj_emitente,
                "data_emissao": data_emissao,
                "nf_valor_total": nf_valor_total,
                "detalhe": registro.detalhe or "",
                "status": registro.status,
                "solicitado_em": registro.solicitado_em.strftime("%d/%m/%Y %H:%M") if registro.solicitado_em else "",
                "respondido_por": registro.respondido_por or "",
                "respondido_em": registro.respondido_em.strftime("%d/%m/%Y %H:%M") if registro.respondido_em else "",
                "motivo_resposta": registro.motivo_resposta or "",
            },
            "itens_xml": itens_xml,
            "comparacao": comparacao,
            "erro_comparacao": erro_comparacao,
            "tem_pdf": bool(chave_acesso),
        }
    )


@divergencia_aprovacao_bp.route("/aprovar-divergencia/<token>/danfe", methods=["GET"])
def danfe_aprovacao(token):
    registro = _carregar_registro(token)
    if not registro:
        return jsonify({"error": "Link inválido."}), 404
    if not _aprovador_logado(token):
        return jsonify({"error": "Login necessário."}), 401

    item = (
        ItemNota.query.filter_by(numero_nota=registro.numero_nota)
        .filter(ItemNota.chave_acesso.isnot(None))
        .first()
    )
    chave = re.sub(r"\D", "", str(getattr(item, "chave_acesso", "") or ""))[:44]
    if len(chave) != 44:
        return jsonify({"error": "NF sem chave de acesso para gerar o PDF."}), 404

    try:
        ok, status_code, xml_bytes = download_documento_consyste(
            modelo="nfe", formato="xml", chave=chave, timeout=30
        )
    except Exception as exc:
        return jsonify({"error": f"Erro ao buscar XML na Consyste: {exc}"}), 502
    if not ok or not xml_bytes:
        return jsonify({"error": f"XML não encontrado na Consyste (HTTP {status_code})."}), 404

    try:
        logo_path = os.path.normpath(os.path.join(current_app.root_path, "..", "static", "columbia_logo.png"))
        pdf_bytes = gerar_danfe(xml_bytes, logo_path=logo_path, logo_url=current_app.config.get("EMPRESA_LOGO_URL", ""))
    except Exception as exc:
        current_app.logger.exception("Erro ao gerar DANFE (aprovacao divergencia): %s", exc)
        return jsonify({"error": f"Erro ao gerar DANFE: {exc}"}), 500

    nf_num = ""
    try:
        nf_num = str(parse_nfe_xml(xml_bytes).get("nNF") or "").strip()
    except Exception:
        pass
    filename = f"DANFE_{nf_num or chave[:10]}.pdf"
    return current_app.response_class(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@divergencia_aprovacao_bp.route("/aprovar-divergencia/<token>/decidir", methods=["POST"])
def decidir_aprovacao(token):
    registro = _carregar_registro(token)
    if not registro:
        return jsonify({"sucesso": False, "msg": "Link inválido."}), 404
    aprovador = _aprovador_logado(token)
    if not aprovador:
        return jsonify({"sucesso": False, "erro": "login_necessario", "msg": "Faça login para decidir."}), 401

    if registro.status != "Pendente":
        return jsonify(
            {
                "sucesso": False,
                "msg": f"Esta divergência já foi {registro.status.lower()} por {registro.respondido_por or 'outro usuário'}.",
            }
        ), 409

    data = request.get_json() or {}
    decisao = str(data.get("decisao") or "").strip().lower()
    motivo = str(data.get("motivo") or "").strip()[:500]
    if decisao not in ("aprovado", "rejeitado"):
        return jsonify({"sucesso": False, "msg": "Escolha aprovar ou recusar."}), 400
    if decisao == "rejeitado" and not motivo:
        return jsonify({"sucesso": False, "msg": "Informe o motivo da recusa."}), 400

    registro.status = "Aprovado" if decisao == "aprovado" else "Rejeitado"
    registro.respondido_por = aprovador.get("nome") or aprovador.get("username")
    registro.respondido_em = datetime.now()
    registro.motivo_resposta = motivo or None
    db.session.commit()

    return jsonify(
        {
            "sucesso": True,
            "status": registro.status,
            "msg": (
                "Divergência aprovada. A NF já pode ser liberada para conferência."
                if registro.status == "Aprovado"
                else "Divergência recusada. A NF continuará bloqueada."
            ),
        }
    )
