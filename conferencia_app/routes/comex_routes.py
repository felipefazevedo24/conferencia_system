"""Rotas do modulo Comex (gestao de processos de importacao/exportacao).

Nesta leva: Modulo 1 (OC, busca sob demanda no ERP) + Modulo 2 (PO: formulario,
PDF, e-mail). Os demais modulos do workflow ja tem schema pronto
(ver conferencia_app/models.py) mas entram em levas futuras.
"""
from __future__ import annotations

import json
from datetime import datetime
from io import BytesIO
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, session, url_for

from ..auth import permission_required
from ..extensions import db
from ..models import ComexCotacao, ComexDocumento, ComexProcesso, ComexPoItem
from ..services import comex_service as svc
from ..services import comex_po_pdf
from ..services.smtp_service import enviar_mensagem_smtp
from .api_routes import _resolver_foto_expedicao, _send_foto_expedicao

comex_bp = Blueprint("comex", __name__)

PERMISSION = "PAGE_COMEX"

# E-mails sempre em copia no envio da PO (requisito do Modulo 2).
PO_EMAIL_CC_FIXO = ["laroli@colmac.com", "filoli@colmac.com"]


def _processo_payload(p: ComexProcesso) -> dict:
    return {
        "id": p.id,
        "id_op": p.id_op,
        "ref_ff": p.ref_ff,
        "tipo_operacao": p.tipo_operacao,
        "status_modulo": p.status_modulo,
        "status_slug": p.status_slug,
        "cod_empresa": p.cod_empresa,
        "cod_ordem_compra": p.cod_ordem_compra,
        "cod_compra": p.cod_compra,
        "numero_os": p.numero_os,
        "fornecedor": p.fornecedor,
        "comprador": p.comprador,
        "dt_lancamento_oc": p.dt_lancamento_oc.isoformat() if p.dt_lancamento_oc else None,
        "dt_recebimento_oc": p.dt_recebimento_oc.isoformat() if p.dt_recebimento_oc else None,
        "total_produtos_oc": p.total_produtos_oc,
        "total_oc": p.total_oc,
        "situacao_oc": p.situacao_oc,
        "po_numero": p.po_numero,
        "pagador_frete": p.pagador_frete,
        "po_status": p.po_status,
        "po_pdf_disponivel": bool(p.po_pdf_file_name),
        "po_enviada_em": p.po_enviada_em.strftime("%d/%m/%Y %H:%M") if p.po_enviada_em else None,
        "po_enviada_por": p.po_enviada_por,
        "po_finalizada_sem_envio": bool(p.po_finalizada_sem_envio),
        "criado_em": p.criado_em.strftime("%d/%m/%Y %H:%M") if p.criado_em else None,
        "criado_por": p.criado_por,
        # Dados gerais de operacao (visiveis a partir da PO, editaveis ao longo do processo)
        "direcao_operacao": p.direcao_operacao,
        "modal_transporte": p.modal_transporte,
        "po_data": p.po_data.isoformat() if p.po_data else None,
        "ref_despachante": p.ref_despachante,
        "bl_awb": p.bl_awb,
        "invoice_numero": p.invoice_numero,
        "etd": p.etd.isoformat() if p.etd else None,
        "eta": p.em_transito_eta.isoformat() if p.em_transito_eta else None,
        "previsao_entrega": p.previsao_entrega.isoformat() if p.previsao_entrega else None,
        "entrega_real": p.entrega_real,
        "nf_impo": p.nf_impo,
        "nf_recebimento": p.nf_recebimento,
        "instrucao_enviada_em": p.instrucao_enviada_em.strftime("%d/%m/%Y %H:%M") if p.instrucao_enviada_em else None,
        "instrucao_enviada_por": p.instrucao_enviada_por,
        "cotacao_vencedora_id": p.cotacao_vencedora_id,
        "cotacao_substatus": svc.cotacao_substatus(p),
        "po_subtotal_usd": svc.subtotal_itens_po(p),
        "taxa_cambio_referencia": p.taxa_cambio_referencia,
    }


@comex_bp.route("/comex")
@permission_required(PERMISSION)
def comex_page():
    return render_template("comex.html", user=session.get("username"), is_admin=session.get("role") == "Admin")


@comex_bp.route("/api/comex/metricas")
@permission_required(PERMISSION)
def api_metricas():
    return jsonify({"metricas": svc.metricas_por_modulo()})


@comex_bp.route("/api/comex/processos")
@permission_required(PERMISSION)
def api_listar_processos():
    status_modulo = request.args.get("status") or None
    busca = request.args.get("q") or ""
    processos = svc.listar_processos(status_modulo=status_modulo, busca=busca)
    return jsonify({
        "processos": [_processo_payload(p) for p in processos],
        "metricas": svc.metricas_por_modulo(),
    })


@comex_bp.route("/api/comex/processos/<int:processo_id>")
@permission_required(PERMISSION)
def api_obter_processo(processo_id):
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    return jsonify({"processo": _processo_payload(processo)})


@comex_bp.route("/api/comex/processos/<int:processo_id>/ocs-mesmo-fornecedor")
@permission_required(PERMISSION)
def api_ocs_mesmo_fornecedor(processo_id):
    """Outras OCs ja importadas (ainda em estagio OC) do mesmo fornecedor,
    para o operador escolher combinar na mesma PO (Modulo 2, requisito de
    selecionar mais de uma OC desde que do mesmo fornecedor)."""
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    candidatas = (
        ComexProcesso.query
        .filter(
            ComexProcesso.id != processo.id,
            ComexProcesso.fornecedor == processo.fornecedor,
            ComexProcesso.status_modulo == "OC",
        )
        .order_by(ComexProcesso.criado_em.desc())
        .all()
    )
    return jsonify({"processos": [_processo_payload(p) for p in candidatas]})


@comex_bp.route("/api/comex/oc/buscar")
@permission_required(PERMISSION)
def api_buscar_oc():
    """Modulo 1 - busca sob demanda no ERP (nao importa nada sozinho)."""
    termo = request.args.get("termo") or ""
    try:
        candidatas = svc.buscar_ocs_para_importar(termo=termo)
    except Exception as exc:
        current_app.logger.exception("Falha ao buscar OCs no ERP para o Comex")
        return jsonify({"error": f"Falha ao consultar o ERP: {exc}"}), 502
    return jsonify({"ocs": candidatas})


@comex_bp.route("/api/comex/oc/importar", methods=["POST"])
@permission_required(PERMISSION)
def api_importar_oc():
    """O tipo de operação (IM/IA) não é perguntado aqui — só fica claro se é
    marítimo ou aéreo mais perto da definição do frete, então essa escolha
    acontece no Módulo 2 (PO), ver `api_salvar_po`."""
    payload = request.get_json(silent=True) or {}
    oc_header = payload.get("oc") or {}
    usuario = session.get("username", "desconhecido")
    try:
        processo = svc.importar_oc(oc_header, usuario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "OC importada com sucesso.", "processo": _processo_payload(processo)})


@comex_bp.route("/api/comex/processos/<int:processo_id>/oc", methods=["POST"])
@permission_required(PERMISSION)
def api_editar_oc(processo_id):
    """Módulo 1 - Salvar/Editar: corrige manualmente os campos da OC
    importada, enquanto o processo ainda está no módulo OC."""
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    usuario = session.get("username", "desconhecido")
    try:
        processo = svc.editar_oc(processo, payload, usuario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "OC atualizada.", "processo": _processo_payload(processo)})


@comex_bp.route("/api/comex/processos/<int:processo_id>/oc", methods=["DELETE"])
@permission_required(PERMISSION)
def api_apagar_oc(processo_id):
    """Módulo 1 - Apagar: remove o processo, liberando a OC para importação
    novamente."""
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    try:
        svc.apagar_oc(processo)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "OC apagada. A ordem de compra está disponível para importação novamente."})


@comex_bp.route("/api/comex/processos/<int:processo_id>/po", methods=["POST"])
@permission_required(PERMISSION)
def api_salvar_po(processo_id):
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404

    payload = request.get_json(silent=True) or {}
    pagador_frete = str(payload.get("pagador_frete") or "").strip()
    direcao_operacao = str(payload.get("direcao_operacao") or "").strip().upper()
    modal_transporte = str(payload.get("modal_transporte") or "").strip()
    ocs_vinculadas_ids = payload.get("ocs_vinculadas_ids") or []
    finalizar = bool(payload.get("finalizar"))
    usuario = session.get("username", "desconhecido")

    try:
        processo = svc.salvar_po(
            processo,
            pagador_frete=pagador_frete,
            direcao_operacao=direcao_operacao,
            modal_transporte=modal_transporte,
            ocs_vinculadas_ids=ocs_vinculadas_ids,
            usuario=usuario,
            finalizar=finalizar,
            dados_operacionais=payload,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify({"message": "PO salva com sucesso.", "processo": _processo_payload(processo)})


@comex_bp.route("/api/comex/processos/<int:processo_id>/po", methods=["DELETE"])
@permission_required(PERMISSION)
def api_apagar_po(processo_id):
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    usuario = session.get("username", "desconhecido")
    processo = svc.apagar_po(processo, usuario)
    return jsonify({"message": "PO apagada.", "processo": _processo_payload(processo)})


@comex_bp.route("/api/comex/processos/<int:processo_id>/instrucao", methods=["POST"])
@permission_required(PERMISSION)
def api_enviar_instrucao(processo_id):
    """Módulo 4 (versão mínima) — só disponível depois que uma cotação foi
    escolhida; libera Ref. Despachante/BL-AWB/Invoice/ETD/ETA/Previsão
    Entrega/Entrega Real/NF Impo/NF Recebimento para edição."""
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    usuario = session.get("username", "desconhecido")
    try:
        processo = svc.enviar_instrucao(processo, payload, usuario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Instrução de embarque salva.", "processo": _processo_payload(processo)})


def _item_payload(it: ComexPoItem) -> dict:
    return {
        "id": it.id,
        "codigo": it.codigo,
        "ncm": it.ncm,
        "pn": it.pn,
        "descricao": it.descricao,
        "quantidade": it.quantidade,
        "valor_unitario": it.valor_unitario,
        "valor_total": it.valor_total,
    }


@comex_bp.route("/api/comex/processos/<int:processo_id>/po/itens", methods=["GET"])
@permission_required(PERMISSION)
def api_listar_itens_po(processo_id):
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    itens = svc.listar_itens_po(processo)
    return jsonify({"itens": [_item_payload(it) for it in itens]})


@comex_bp.route("/api/comex/processos/<int:processo_id>/po/itens", methods=["POST"])
@permission_required(PERMISSION)
def api_salvar_itens_po(processo_id):
    """Substitui a lista de itens de linha da PO (código/NCM/PN/descrição/
    quantidade/valores) — preenchidos manualmente pelo operador enquanto o
    ERP não expõe preço unitário/NCM por item via bridge."""
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    itens = payload.get("itens") or []
    itens_salvos = svc.salvar_itens_po(processo, itens)
    return jsonify({"message": "Itens da PO salvos.", "itens": [_item_payload(it) for it in itens_salvos]})


@comex_bp.route("/api/comex/processos/<int:processo_id>/oc-itens-erp", methods=["GET"])
@permission_required(PERMISSION)
def api_buscar_itens_oc_erp(processo_id):
    """Puxa do ERP os itens da OC do processo (código/NCM/PN/descrição/
    quantidade/valores, quando encontrados) para pré-preencher a tabela de
    itens da PO. São só sugestões — não salva nada sozinho, o operador
    revisa e clica em Salvar."""
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    try:
        itens = svc.buscar_itens_oc_no_erp(processo)
    except Exception as exc:
        current_app.logger.exception("Falha ao buscar itens da OC %s no ERP", processo.cod_ordem_compra)
        return jsonify({"error": f"Falha ao consultar o ERP: {exc}"}), 502
    return jsonify({"itens": itens})


@comex_bp.route("/api/comex/processos/<int:processo_id>/estornar", methods=["POST"])
@permission_required(PERMISSION)
def api_estornar(processo_id):
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    usuario = session.get("username", "desconhecido")
    try:
        processo = svc.estornar(processo, usuario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Processo estornado.", "processo": _processo_payload(processo)})


def _ocs_vinculadas_do_processo(processo: ComexProcesso) -> list[ComexProcesso]:
    if not processo.po_ocs_vinculadas:
        return []
    try:
        codigos = json.loads(processo.po_ocs_vinculadas)
    except (TypeError, ValueError):
        return []
    outras = [c for c in codigos if c != processo.cod_ordem_compra]
    if not outras:
        return []
    return ComexProcesso.query.filter(
        ComexProcesso.cod_ordem_compra.in_(outras),
        ComexProcesso.fornecedor == processo.fornecedor,
    ).all()


@comex_bp.route("/api/comex/processos/<int:processo_id>/po.pdf")
@permission_required(PERMISSION)
def api_po_pdf(processo_id):
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    if not processo.po_numero:
        return jsonify({"error": "Este processo ainda não tem uma PO criada."}), 400

    ocs_vinculadas = _ocs_vinculadas_do_processo(processo)
    itens = svc.listar_itens_po(processo)
    pdf_bytes = comex_po_pdf.gerar_po_pdf(processo, ocs_vinculadas, itens)
    return send_file(
        BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"{processo.po_numero}.pdf",
    )


@comex_bp.route("/api/comex/processos/<int:processo_id>/po/enviar-email", methods=["POST"])
@permission_required(PERMISSION)
def api_enviar_email_po(processo_id):
    """Envia a PO em PDF para o fornecedor por e-mail. Se o fornecedor nao
    tiver e-mail cadastrado, o operador informa na hora (payload
    `destinatarios`). Suporta multiplos destinatarios separados por ";" e
    sempre copia os e-mails fixos do Modulo 2."""
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    if not processo.po_numero:
        return jsonify({"error": "Este processo ainda não tem uma PO criada."}), 400

    payload = request.get_json(silent=True) or {}
    destinatarios_raw = str(payload.get("destinatarios") or "").strip()
    destinatarios = [d.strip() for d in destinatarios_raw.split(";") if d.strip()]
    if not destinatarios:
        return jsonify({"error": "Informe ao menos um e-mail de destinatário."}), 400

    ocs_vinculadas = _ocs_vinculadas_do_processo(processo)
    itens = svc.listar_itens_po(processo)
    pdf_bytes = comex_po_pdf.gerar_po_pdf(processo, ocs_vinculadas, itens)

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"PO {processo.po_numero} — {processo.id_op} — Columbia Machine Brasil"
    msg["From"] = f"{current_app.config.get('MAIL_SENDER_NAME', 'Columbia Sync')} <{current_app.config.get('MAIL_SENDER', '')}>"
    msg["To"] = ", ".join(destinatarios)
    msg["Cc"] = ", ".join(PO_EMAIL_CC_FIXO)

    corpo = (
        f"<p>Prezados,</p>"
        f"<p>Segue em anexo a Purchase Order <strong>{processo.po_numero}</strong> "
        f"referente ao processo <strong>{processo.id_op}</strong>.</p>"
        f"<p>Atenciosamente,<br>Columbia Machine Brasil</p>"
    )
    msg.attach(MIMEText(corpo, "html", "utf-8"))

    parte_pdf = MIMEApplication(pdf_bytes, _subtype="pdf")
    parte_pdf.add_header("Content-Disposition", "attachment", filename=f"{processo.po_numero}.pdf")
    msg.attach(parte_pdf)

    todos_destinatarios = destinatarios + PO_EMAIL_CC_FIXO
    try:
        enviar_mensagem_smtp(current_app, msg)
    except Exception as exc:
        current_app.logger.exception("Falha ao enviar e-mail da PO %s", processo.po_numero)
        return jsonify({"error": f"Falha ao enviar e-mail: {exc}"}), 502

    agora = datetime.now()
    processo.po_enviada_em = agora
    processo.po_enviada_por = session.get("username", "desconhecido")
    processo.po_destinatarios_email = "; ".join(destinatarios)
    processo.po_finalizada_sem_envio = False
    # Enviar o e-mail da PO finaliza ela (mesmo efeito de "Finalizar sem
    # enviar e-mail") e avanca o processo pro proximo modulo certo (Cotacao
    # se a Columbia paga o frete, Instrucao se nao paga).
    processo.po_status = "Finalizada"
    svc.avancar_modulo_apos_po_finalizada(processo)
    processo.atualizado_em = agora
    processo.atualizado_por = session.get("username", "desconhecido")
    db.session.commit()

    return jsonify({"message": "E-mail da PO enviado com sucesso.", "processo": _processo_payload(processo)})


# ── Anexo de documentos (requisito geral: todo modulo precisa ter uma ─────
# funcao de anexar documento) ───────────────────────────────────────────
def _documento_payload(d: ComexDocumento) -> dict:
    return {
        "id": d.id,
        "modulo": d.modulo,
        "titulo": d.titulo,
        "file_name": d.file_name,
        "uploaded_at": d.uploaded_at.strftime("%d/%m/%Y %H:%M") if d.uploaded_at else None,
        "uploaded_by": d.uploaded_by,
        "url": f"/api/comex/processos/{d.processo_id}/documentos/{d.id}",
    }


@comex_bp.route("/api/comex/processos/<int:processo_id>/documentos", methods=["GET"])
@permission_required(PERMISSION)
def api_listar_documentos(processo_id):
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    modulo = request.args.get("modulo") or None
    documentos = svc.listar_documentos(processo, modulo=modulo)
    return jsonify({"documentos": [_documento_payload(d) for d in documentos]})


@comex_bp.route("/api/comex/processos/<int:processo_id>/documentos", methods=["POST"])
@permission_required(PERMISSION)
def api_anexar_documento(processo_id):
    """Anexa um documento ao processo. Funciona em qualquer modulo do
    workflow — o modulo de origem vem no campo `modulo` do form."""
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404

    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        return jsonify({"error": "Nenhum arquivo recebido."}), 400
    modulo = str(request.form.get("modulo") or "").strip()
    titulo = request.form.get("titulo")
    usuario = session.get("username", "desconhecido")

    try:
        dados = arquivo.read()
        documento = svc.anexar_documento(
            processo,
            modulo=modulo,
            dados=dados,
            file_name=arquivo.filename,
            usuario=usuario,
            titulo=titulo,
            mimetype=arquivo.mimetype,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        current_app.logger.exception("Falha ao anexar documento ao processo %s", processo.id_op)
        return jsonify({"error": f"Falha ao anexar documento: {exc}"}), 502

    return jsonify({"message": "Documento anexado.", "documento": _documento_payload(documento)})


@comex_bp.route("/api/comex/processos/<int:processo_id>/documentos/<int:documento_id>", methods=["GET"])
@permission_required(PERMISSION)
def api_baixar_documento(processo_id, documento_id):
    documento = ComexDocumento.query.filter_by(id=documento_id, processo_id=processo_id).first()
    if not documento:
        return jsonify({"error": "Documento não encontrado."}), 404
    pasta = current_app.config.get("COMEX_DOCUMENTOS_DIR", "") or None
    caminho = _resolver_foto_expedicao(pasta, documento.file_name, documento.file_path)
    if not caminho:
        return jsonify({"error": "Arquivo não encontrado."}), 404
    try:
        return _send_foto_expedicao(caminho, documento.file_name)
    except Exception as exc:
        current_app.logger.exception("Falha ao baixar documento Comex %s", documento_id)
        return jsonify({"error": f"Falha ao baixar documento: {exc}"}), 502


@comex_bp.route("/api/comex/processos/<int:processo_id>/documentos/<int:documento_id>", methods=["DELETE"])
@permission_required(PERMISSION)
def api_apagar_documento(processo_id, documento_id):
    documento = ComexDocumento.query.filter_by(id=documento_id, processo_id=processo_id).first()
    if not documento:
        return jsonify({"error": "Documento não encontrado."}), 404
    svc.apagar_documento(documento)
    return jsonify({"message": "Documento apagado."})


# ── Cotação (Módulo 3) — link público (sem login) para o prestador de ─────
# frete preencher, no formato do "modelo de cotação.xlsx" da empresa. ─────
def _cotacao_payload(c: ComexCotacao) -> dict:
    custos = {}
    for prefixo, _ in svc.ETAPAS_CUSTO_COTACAO:
        custos[f"{prefixo}_usd"] = getattr(c, f"{prefixo}_usd")
        custos[f"{prefixo}_brl"] = getattr(c, f"{prefixo}_brl")
    return {
        "id": c.id,
        "tipo_frete": c.tipo_frete,
        "status": c.status,
        "fornecedor_frete": c.fornecedor_frete,
        "origem": c.origem,
        "destino": c.destino,
        "incoterm": c.incoterm,
        "qtd_40hc": c.qtd_40hc,
        "qtd_20dry": c.qtd_20dry,
        "imo_classe": c.imo_classe,
        "un_numero": c.un_numero,
        "valor_mercadoria_usd": c.valor_mercadoria_usd,
        "transit_time": c.transit_time,
        "rota": c.rota,
        "validade": c.validade.isoformat() if c.validade else None,
        "ptax": c.ptax,
        **custos,
        "custo_total_usd": c.custo_total_usd,
        "custo_total_brl": c.custo_total_brl,
        "custo_total_consolidado_brl": svc.custo_total_consolidado_brl(c),
        "is_sugerida_pelo_sistema": bool(c.is_sugerida_pelo_sistema),
        "is_escolhida": bool(c.is_escolhida),
        "email_instrucao_embarque": c.email_instrucao_embarque,
        "link_gerado_em": c.link_gerado_em.strftime("%d/%m/%Y %H:%M") if c.link_gerado_em else None,
        "recebida_em": c.recebida_em.strftime("%d/%m/%Y %H:%M") if c.recebida_em else None,
        "expirado": bool(c.token_publico_expira_em and c.token_publico_expira_em < datetime.now() and c.status == "Pendente"),
        "criado_por": c.criado_por,
        "volumes": [
            {"numero": v.numero, "comprimento": v.comprimento, "largura": v.largura, "altura": v.altura, "peso": v.peso}
            for v in c.volumes
        ],
    }


def _enviar_email_link_cotacao(processo: ComexProcesso, cotacao: ComexCotacao, link: str, destinatario: str) -> None:
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Solicitação de cotação — {processo.id_op} — Columbia Machine Brasil"
    msg["From"] = f"{current_app.config.get('MAIL_SENDER_NAME', 'Columbia Sync')} <{current_app.config.get('MAIL_SENDER', '')}>"
    msg["To"] = destinatario
    corpo = (
        f"<p>Prezados,</p>"
        f"<p>Solicitamos cotação de frete ({'FCL' if cotacao.tipo_frete == 'FCL' else 'LCL/Aéreo'}) "
        f"referente ao processo <strong>{processo.id_op}</strong>.</p>"
        f"<p>Preencha a cotação pelo link abaixo:</p>"
        f"<p><a href=\"{link}\">{link}</a></p>"
        f"<p>Atenciosamente,<br>Columbia Machine Brasil</p>"
    )
    msg.attach(MIMEText(corpo, "html", "utf-8"))
    enviar_mensagem_smtp(current_app, msg)


@comex_bp.route("/api/comex/processos/<int:processo_id>/cotacoes", methods=["GET"])
@permission_required(PERMISSION)
def api_listar_cotacoes(processo_id):
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    cotacoes = svc.listar_cotacoes(processo)
    return jsonify({"cotacoes": [_cotacao_payload(c) for c in cotacoes]})


@comex_bp.route("/api/comex/processos/<int:processo_id>/taxa-cambio", methods=["POST"])
@permission_required(PERMISSION)
def api_definir_taxa_cambio(processo_id):
    """Taxa de câmbio de referência do processo (ex.: PTAX do dia) - usada
    pra comparar o custo total de todas as cotações na mesma moeda,
    independente da taxa que cada fornecedor informou por conta própria."""
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    usuario = session.get("username", "desconhecido")
    try:
        processo = svc.definir_taxa_cambio(processo, payload.get("taxa_cambio_referencia"), usuario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Taxa de câmbio salva.", "processo": _processo_payload(processo)})


@comex_bp.route("/api/comex/processos/<int:processo_id>/cotacoes", methods=["POST"])
@permission_required(PERMISSION)
def api_criar_link_cotacao(processo_id):
    processo = ComexProcesso.query.get(processo_id)
    if not processo:
        return jsonify({"error": "Processo não encontrado."}), 404

    payload = request.get_json(silent=True) or {}
    tipo_frete = str(payload.get("tipo_frete") or "").strip().upper()
    usuario = session.get("username", "desconhecido")

    try:
        cotacao, token = svc.criar_link_cotacao(
            processo,
            tipo_frete=tipo_frete,
            usuario=usuario,
            email_instrucao_embarque=payload.get("email_instrucao_embarque"),
            pre_preenchido=payload,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    link = url_for("comex.cotacao_publica_page", token=token, _external=True)

    destinatario = str(payload.get("enviar_para") or "").strip()
    email_enviado = None
    email_erro = None
    if destinatario:
        try:
            _enviar_email_link_cotacao(processo, cotacao, link, destinatario)
            email_enviado = True
        except Exception as exc:
            current_app.logger.exception("Falha ao enviar e-mail do link de cotação (processo %s)", processo.id_op)
            email_enviado = False
            email_erro = str(exc)

    if destinatario and email_enviado:
        mensagem = "Link de cotação gerado. E-mail enviado."
    elif destinatario:
        mensagem = f"Link de cotação gerado, mas o e-mail NÃO foi enviado: {email_erro}"
    else:
        mensagem = "Link de cotação gerado."

    return jsonify({
        "message": mensagem,
        "email_enviado": email_enviado,
        "email_erro": email_erro,
        "cotacao": _cotacao_payload(cotacao),
        "link": link,
        "processo": _processo_payload(processo),
    })


@comex_bp.route("/api/comex/cotacoes/<int:cotacao_id>/escolher", methods=["POST"])
@permission_required(PERMISSION)
def api_escolher_cotacao(cotacao_id):
    cotacao = ComexCotacao.query.get(cotacao_id)
    if not cotacao:
        return jsonify({"error": "Cotação não encontrada."}), 404
    payload = request.get_json(silent=True) or {}
    usuario = session.get("username", "desconhecido")
    try:
        processo = svc.escolher_cotacao(cotacao, usuario=usuario, justificativa=payload.get("justificativa"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Cotação escolhida.", "processo": _processo_payload(processo)})


# ── Formulário público (sem login) ─────────────────────────────────────
@comex_bp.route("/cotacao-fornecedor/<token>")
def cotacao_publica_page(token):
    cotacao = svc.obter_cotacao_por_token(token)
    if not cotacao:
        return render_template("acesso_negado.html"), 404
    return render_template("comex_cotacao_publica.html", token=token)


@comex_bp.route("/api/cotacao-fornecedor/<token>")
def api_cotacao_publica_dados(token):
    cotacao = svc.obter_cotacao_por_token(token)
    if not cotacao:
        return jsonify({"error": "Link inválido ou expirado."}), 404
    processo = cotacao.processo
    return jsonify({
        "valido": svc.link_cotacao_valido(cotacao),
        "status": cotacao.status,
        "tipo_frete": cotacao.tipo_frete,
        "id_op": processo.id_op,
        "termos": svc.TERMOS_COTACAO,
        "etapas_custo": [{"campo": p, "label": l} for p, l in svc.ETAPAS_CUSTO_COTACAO],
        "cotacao": _cotacao_payload(cotacao) if cotacao.status == "Recebida" else None,
        # Dados de embarque que a Columbia ja preencheu ao gerar o link -
        # aparecem mesmo antes da submissao, pra pre-preencher o formulario
        # do prestador (ele so confirma/ajusta, nao digita do zero).
        "pre_preenchido": {
            "origem": cotacao.origem,
            "destino": cotacao.destino,
            "incoterm": cotacao.incoterm,
            "imo_classe": cotacao.imo_classe,
            "un_numero": cotacao.un_numero,
            "qtd_40hc": cotacao.qtd_40hc,
            "qtd_20dry": cotacao.qtd_20dry,
            "valor_mercadoria_usd": cotacao.valor_mercadoria_usd,
            "volumes": [
                {"comprimento": v.comprimento, "largura": v.largura, "altura": v.altura, "peso": v.peso}
                for v in cotacao.volumes
            ],
        },
    })


@comex_bp.route("/api/cotacao-fornecedor/<token>", methods=["POST"])
def api_cotacao_publica_submeter(token):
    cotacao = svc.obter_cotacao_por_token(token)
    if not cotacao:
        return jsonify({"error": "Link inválido ou expirado."}), 404
    payload = request.get_json(silent=True) or {}
    try:
        svc.submeter_cotacao_publica(cotacao, payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Cotação enviada com sucesso. Obrigado!"})
