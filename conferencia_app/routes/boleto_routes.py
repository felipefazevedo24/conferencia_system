"""Rotas publicas para consulta de boletos pelos clientes."""

import re
from io import BytesIO

from flask import Blueprint, jsonify, render_template, request, send_file

from ..services.cliente_portal_service import ler_token_nf
from ..services.erp_nfe_emitidas_service import buscar_nfe_emitida_erp
from ..services.bb_boleto_service import BBBoletoService

boleto_bp = Blueprint("boleto", __name__)


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


@boleto_bp.route("/boletos")
def consulta_boletos_page():
    """Pagina publica de consulta de boletos pelo cliente."""
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
        return jsonify({"sucesso": False, "error": "Link invalido ou expirado."}), 404

    numero_nf = str(payload.get("numero_nf") or "").strip()
    chave = str(payload.get("chave") or "").strip()
    cnpj = str(payload.get("cnpj") or "").strip()
    boletos = BBBoletoService.consultar_por_nota_valor(numero_nf, 0, somente_abertos=True)

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
        return jsonify({"sucesso": False, "error": "Link invalido ou expirado."}), 404
    numero_nf = str(payload.get("numero_nf") or "").strip()
    chave = str(payload.get("chave") or "").strip()
    nota = buscar_nfe_emitida_erp(numero_nf, chave)
    if not nota or not nota.get("xml_bytes"):
        return jsonify({"sucesso": False, "error": "XML nao encontrado."}), 404
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
        return jsonify({"sucesso": False, "error": "Link invalido ou expirado."}), 404
    numero_nf = str(payload.get("numero_nf") or "").strip()
    chave = str(payload.get("chave") or "").strip()
    nota = buscar_nfe_emitida_erp(numero_nf, chave)
    if not nota or not nota.get("pdf_bytes"):
        return jsonify({"sucesso": False, "error": "DANFE nao encontrado."}), 404
    return send_file(
        BytesIO(nota["pdf_bytes"]),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"DANFE-{numero_nf}.pdf",
    )


@boleto_bp.route("/api/boletos/consultar", methods=["POST"])
def consultar_boletos():
    """Consulta boletos por CPF/CNPJ ou por numero da NF mais valor."""
    data = request.json or {}
    modo = str(data.get("modo") or "cpf_cnpj").strip()

    if modo == "nota":
        numero_nota = str(data.get("numero_nota") or "").strip()
        if not numero_nota:
            return jsonify({"sucesso": False, "error": "Informe o numero da nota fiscal."}), 400

        try:
            valor = float(data.get("valor") or 0)
        except (TypeError, ValueError):
            valor = 0.0

        boletos = BBBoletoService.consultar_por_nota_valor(numero_nota, valor, somente_abertos=True)
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
            return jsonify({"sucesso": False, "error": "Informe o numero do orcamento."}), 400
        boletos = BBBoletoService.consultar_por_orcamento(orcamento)
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
        return jsonify({"sucesso": False, "error": "Informe um CPF (11 digitos) ou CNPJ (14 digitos) valido."}), 400

    if len(doc) not in (11, 14):
        return jsonify({"sucesso": False, "error": "CPF deve ter 11 digitos e CNPJ 14 digitos."}), 400

    resultado = BBBoletoService.consultar_boletos(doc, somente_abertos=True)
    return jsonify(
        {
            "sucesso": True,
            "fonte": resultado["fonte"],
            "boletos": resultado["boletos"],
            "total": len(resultado["boletos"]),
            "mensagem": resultado.get("mensagem", ""),
        }
    )
