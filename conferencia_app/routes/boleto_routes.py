"""Rotas públicas para consulta de boletos pelos clientes."""

import re
from io import BytesIO
from datetime import datetime

from flask import Blueprint, jsonify, redirect, render_template, request, send_file

from ..services.cliente_portal_service import ler_token_nf
from ..services.erp_nfe_emitidas_service import buscar_nfe_emitida_erp
from ..services.bb_boleto_service import BBBoletoService
from ..services.grv_contas_receber_service import GRVContasReceberService

boleto_bp = Blueprint("boleto", __name__)


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _normalizar_numero_nf(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw.split("-", 1)[0].strip()


def _parse_data_br(value: str):
    raw = str(value or "").strip()
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def _agrupar_titulos_abertos(boletos: list[dict]) -> list[dict]:
    grupos: dict[tuple[str, str], dict] = {}
    avulsos: list[dict] = []

    for boleto in boletos:
        orcamento = str(boleto.get("orcamento") or "").strip()
        nota = str(boleto.get("numero_nota") or "").strip()
        documento = str(boleto.get("documento") or "").strip()

        if orcamento:
            tipo, chave, label = "orçamento", orcamento, f"Orçamento {orcamento}"
        elif nota:
            tipo, chave, label = "nota fiscal", nota, f"NF {nota}"
        elif documento:
            tipo, chave, label = "documento", documento, documento
        else:
            avulsos.append(boleto)
            continue

        grupo = grupos.setdefault(
            (tipo, chave),
            {
                "fonte": boleto.get("fonte") or "",
                "tipo": "grupo_aberto",
                "grupo_tipo": tipo,
                "grupo_chave": chave,
                "titulo": label,
                "orcamento": orcamento,
                "numero_nota": nota,
                "documento": documento,
                "nome_pagador": boleto.get("nome_pagador") or "",
                "cpf_cnpj_pagador": boleto.get("cpf_cnpj_pagador") or "",
                "valor": 0.0,
                "valor_original": 0.0,
                "valor_pago": 0.0,
                "quantidade_titulos": 0,
                "vencimento": boleto.get("vencimento") or "",
                "vencimento_iso": boleto.get("vencimento_iso") or "",
                "status": "Em aberto",
                "banco": boleto.get("banco") or "Banco do Brasil",
                "linha_digitavel": "",
                "codigo_barras": "",
                "url_boleto": "",
                "titulos": [],
                "pode_gerar_boleto": bool(boleto.get("pode_gerar_boleto")),
                "_qtd_pago": 0,
                "_qtd_vencido": 0,
            },
        )
        grupo["valor"] += float(boleto.get("valor") or 0)
        grupo["valor_original"] += float(boleto.get("valor_original") or boleto.get("valor") or 0)
        grupo["valor_pago"] += float(boleto.get("valor_pago") or 0)
        grupo["quantidade_titulos"] += 1
        grupo["pode_gerar_boleto"] = bool(grupo.get("pode_gerar_boleto") or boleto.get("pode_gerar_boleto"))
        grupo["titulos"].append(boleto)

        status_item = str(boleto.get("status") or "").lower()
        if any(p in status_item for p in ("pago", "liquid")):
            grupo["_qtd_pago"] += 1
        elif "venc" in status_item:
            grupo["_qtd_vencido"] += 1

        venc_atual = _parse_data_br(grupo.get("vencimento"))
        venc_item = _parse_data_br(boleto.get("vencimento"))
        if venc_item and (not venc_atual or venc_item < venc_atual):
            grupo["vencimento"] = boleto.get("vencimento") or ""
            grupo["vencimento_iso"] = boleto.get("vencimento_iso") or ""

    for grupo in grupos.values():
        qtd_pago = int(grupo.pop("_qtd_pago", 0) or 0)
        qtd_vencido = int(grupo.pop("_qtd_vencido", 0) or 0)
        total = int(grupo.get("quantidade_titulos") or 0)
        if total and qtd_pago == total:
            grupo["status"] = "Pago"
        elif qtd_vencido > 0:
            grupo["status"] = "Vencido"
        else:
            grupo["status"] = "Em aberto"

    resultado = list(grupos.values()) + avulsos
    resultado.sort(key=lambda item: (_parse_data_br(item.get("vencimento")) or datetime.max.date(), item.get("titulo") or ""))
    return resultado


def _deduplicar_boletos(boletos: list[dict]) -> list[dict]:
    vistos = set()
    resultado = []
    for boleto in boletos:
        chave = (
            boleto.get("fonte") or "",
            boleto.get("id_grv") or "",
            boleto.get("nosso_numero") or "",
            boleto.get("numero_nota") or "",
            boleto.get("documento") or "",
            boleto.get("linha_digitavel") or "",
            round(float(boleto.get("valor") or 0), 2),
        )
        if chave in vistos:
            continue
        vistos.add(chave)
        resultado.append(boleto)
    return resultado


def _filtrar_por_documento_ou_sem_doc(boletos: list[dict], doc: str) -> list[dict]:
    if not doc:
        return boletos
    filtrados = []
    for boleto in boletos:
        boleto_doc = _only_digits(boleto.get("cpf_cnpj_pagador"))
        if not boleto_doc or boleto_doc == doc:
            filtrados.append(boleto)
    return filtrados


@boleto_bp.route("/boletos")
def consulta_boletos_page():
    """Página pública de consulta de boletos pelo cliente."""
    return render_template("consulta_boletos.html")


def _payload_token_ou_404(token: str):
    payload = ler_token_nf(token)
    if not payload or not str(payload.get("numero_nf") or "").strip():
        return None
    return payload


@boleto_bp.route("/portal/cobranca/<token>")
def portal_cobranca_page(token):
    payload = _payload_token_ou_404(token)
    if not payload:
        return render_template("acesso_negado.html"), 404
    return render_template("portal_cobranca.html", token=token)


@boleto_bp.route("/api/portal/cobranca/<token>")
def portal_cobranca_dados(token):
    payload = _payload_token_ou_404(token)
    if not payload:
        return jsonify({"sucesso": False, "error": "Link inválido ou expirado."}), 404

    numero_nf = str(payload.get("numero_nf") or "").strip()
    chave = str(payload.get("chave") or "").strip()
    cnpj = str(payload.get("cnpj") or "").strip()
    # Traz todos os titulos da NF (inclusive os que ainda nao tem boleto emitido e os pagos),
    # para o portal poder avisar o cliente quando o boleto ainda nao esta disponivel.
    boletos = BBBoletoService.consultar_por_nota_valor(
        numero_nf, 0, somente_abertos=False, incluir_pagos=True
    )

    return jsonify({
        "sucesso": True,
        "nota": {
            "numero": numero_nf,
            "chave": chave,
            "cnpj_destinatario": cnpj,
            "download_xml": f"/portal/cobranca/{token}/xml",
            "download_danfe": f"/portal/cobranca/{token}/danfe",
        },
        "boletos": boletos,
        "total_boletos": len(boletos),
    })


@boleto_bp.route("/portal/cobranca/<token>/xml")
def portal_cobranca_xml(token):
    payload = _payload_token_ou_404(token)
    if not payload:
        return jsonify({"sucesso": False, "error": "Link inválido ou expirado."}), 404
    numero_nf = str(payload.get("numero_nf") or "").strip()
    chave = str(payload.get("chave") or "").strip()
    nota = buscar_nfe_emitida_erp(numero_nf, chave)
    if not nota or not nota.get("xml_bytes"):
        return jsonify({"sucesso": False, "error": "XML não encontrado."}), 404
    return send_file(
        BytesIO(nota["xml_bytes"]),
        mimetype="application/xml",
        as_attachment=True,
        download_name=f"NFe-{chave or numero_nf}.xml",
    )


@boleto_bp.route("/portal/cobranca/<token>/danfe")
def portal_cobranca_danfe(token):
    payload = _payload_token_ou_404(token)
    if not payload:
        return jsonify({"sucesso": False, "error": "Link inválido ou expirado."}), 404
    numero_nf = str(payload.get("numero_nf") or "").strip()
    chave = str(payload.get("chave") or "").strip()
    nota = buscar_nfe_emitida_erp(numero_nf, chave)
    if not nota or not nota.get("pdf_bytes"):
        return jsonify({"sucesso": False, "error": "DANFE não encontrado."}), 404
    return send_file(
        BytesIO(nota["pdf_bytes"]),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"DANFE-{numero_nf}.pdf",
    )


@boleto_bp.route("/api/boletos/consultar", methods=["POST"])
def consultar_boletos():
    """Consulta boletos por CPF/CNPJ, número da NF ou orçamento."""
    data = request.json or {}
    modo = str(data.get("modo") or "cpf_cnpj").strip()

    if modo == "nota":
        numero_nota = _normalizar_numero_nf(data.get("numero_nota") or "")
        if not numero_nota:
            return jsonify({"sucesso": False, "error": "Informe o número da nota fiscal."}), 400

        try:
            valor = float(data.get("valor") or 0)
        except (TypeError, ValueError):
            valor = 0.0

        boletos = BBBoletoService.consultar_por_nota_valor(
            numero_nota,
            valor,
            somente_abertos=False,
            incluir_pagos=True,
        )
        return jsonify(
            {
                "sucesso": True,
                "fonte": "grv_postgres+bb_api+local" if BBBoletoService.is_configured() else "grv_postgres+local",
                "boletos": boletos,
                "total": len(boletos),
                "mensagem": "",
            }
        )

    if modo == "orcamento":
        orcamento = str(data.get("orcamento") or data.get("numero_orcamento") or "").strip()
        if not orcamento:
            return jsonify({"sucesso": False, "error": "Informe o número do orçamento."}), 400
        boletos = BBBoletoService.consultar_por_orcamento(orcamento, incluir_pagos=True)
        return jsonify(
            {
                "sucesso": True,
                "fonte": "grv_postgres",
                "boletos": boletos,
                "total": len(boletos),
                "mensagem": "",
            }
        )

    cpf_cnpj = str(data.get("cpf_cnpj") or "").strip()
    doc = _only_digits(cpf_cnpj)

    if not doc or len(doc) < 11:
        return jsonify({"sucesso": False, "error": "Informe um CPF (11 dígitos) ou CNPJ (14 dígitos) válido."}), 400

    if len(doc) not in (11, 14):
        return jsonify({"sucesso": False, "error": "CPF deve ter 11 dígitos e CNPJ 14 dígitos."}), 400

    numero_nota_extra = _normalizar_numero_nf(data.get("numero_nota") or "")
    resultado = BBBoletoService.consultar_boletos(doc, somente_abertos=False, incluir_pagos=True)
    boletos_base = list(resultado["boletos"])
    if numero_nota_extra:
        boletos_nf = BBBoletoService.consultar_por_nota_valor(
            numero_nota_extra,
            0,
            somente_abertos=False,
            incluir_pagos=True,
        )
        boletos_base = _deduplicar_boletos(boletos_base + _filtrar_por_documento_ou_sem_doc(boletos_nf, doc))
    boletos = _agrupar_titulos_abertos(boletos_base) if len(doc) == 14 else boletos_base
    return jsonify(
        {
            "sucesso": True,
            "fonte": resultado["fonte"],
            "boletos": boletos,
            "total": len(boletos),
            "total_titulos": len(boletos_base),
            "agrupado": len(doc) == 14,
            "mensagem": "",
        }
    )


@boleto_bp.route("/api/boletos/grv/<codigo>/pdf")
def baixar_boleto_grv_pdf(codigo):
    return _resolver_download_boleto(
        codigo=str(codigo or "").strip(),
        numero_nota="",
        documento="",
        nosso_numero="",
        cpf_cnpj="",
        valor_raw="",
    )


@boleto_bp.route("/api/boletos/grv/pdf")
def baixar_boleto_grv_pdf_com_contexto():
    return _resolver_download_boleto(
        codigo=str(request.args.get("codigo") or "").strip(),
        numero_nota=str(request.args.get("numero_nota") or "").strip(),
        documento=str(request.args.get("documento") or "").strip(),
        nosso_numero=str(request.args.get("nosso_numero") or "").strip(),
        cpf_cnpj=str(request.args.get("cpf_cnpj") or "").strip(),
        valor_raw=str(request.args.get("valor") or "").strip(),
    )


def _resolver_download_boleto(codigo: str, numero_nota: str, documento: str, nosso_numero: str, cpf_cnpj: str, valor_raw: str):
    codigo_limpo = str(codigo or "").strip()
    pdf_bytes = GRVContasReceberService.obter_boleto_pdf_por_codigo(codigo_limpo) if codigo_limpo else None
    if pdf_bytes:
        return send_file(
            BytesIO(pdf_bytes),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"boleto-grv-{codigo_limpo or 'titulo'}.pdf",
        )

    # Fallback oficial BB: tenta localizar URL do boleto usando contexto do titulo.
    try:
        valor = float(valor_raw or 0)
    except (TypeError, ValueError):
        valor = 0.0

    candidatos = []
    if numero_nota:
        candidatos.extend(BBBoletoService.consultar_por_nota_valor(numero_nota, valor, somente_abertos=True))
    doc = _only_digits(cpf_cnpj)
    if doc and len(doc) in (11, 14):
        resultado_doc = BBBoletoService.consultar_boletos(doc, somente_abertos=True)
        candidatos.extend(resultado_doc.get("boletos") or [])

    nosso_limpo = _only_digits(nosso_numero)
    doc_ref = str(documento or "").strip().lower()
    nota_ref = _normalizar_numero_nf(numero_nota)

    melhor_url = ""
    for item in candidatos:
        url = str(item.get("url_boleto") or "").strip()
        if not url:
            continue
        item_nosso = _only_digits(item.get("nosso_numero"))
        item_doc = str(item.get("documento") or "").strip().lower()
        item_nota = _normalizar_numero_nf(item.get("numero_nota") or item.get("documento"))

        if nosso_limpo and item_nosso and item_nosso == nosso_limpo:
            melhor_url = url
            break
        if doc_ref and item_doc and item_doc == doc_ref:
            melhor_url = url
            break
        if nota_ref and item_nota and item_nota == nota_ref:
            melhor_url = url

    if melhor_url:
        return redirect(melhor_url, code=302)

    return jsonify({"sucesso": False, "error": "PDF do boleto nao encontrado."}), 404
