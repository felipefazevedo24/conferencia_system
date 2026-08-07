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

from flask import Blueprint, current_app, jsonify, render_template, request, send_file, session

from ..auth import permission_required
from ..extensions import db
from ..models import ComexProcesso, ComexPoItem
from ..services import comex_service as svc
from ..services import comex_po_pdf
from ..services.smtp_service import enviar_mensagem_smtp

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
    tipo_operacao = str(payload.get("tipo_operacao") or "").strip().upper()
    ocs_vinculadas_ids = payload.get("ocs_vinculadas_ids") or []
    finalizar = bool(payload.get("finalizar"))
    usuario = session.get("username", "desconhecido")

    try:
        processo = svc.salvar_po(
            processo,
            pagador_frete=pagador_frete,
            tipo_operacao=tipo_operacao,
            ocs_vinculadas_ids=ocs_vinculadas_ids,
            usuario=usuario,
            finalizar=finalizar,
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
    processo.atualizado_em = agora
    processo.atualizado_por = session.get("username", "desconhecido")
    db.session.commit()

    return jsonify({"message": "E-mail da PO enviado com sucesso.", "processo": _processo_payload(processo)})
