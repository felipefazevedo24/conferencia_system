"""Rotas publicas para consulta de boletos pelos clientes."""

import re

from flask import Blueprint, jsonify, render_template, request

from ..services.bb_boleto_service import BBBoletoService

boleto_bp = Blueprint("boleto", __name__)


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


@boleto_bp.route("/boletos")
def consulta_boletos_page():
    """Pagina publica de consulta de boletos pelo cliente."""
    return render_template("consulta_boletos.html")


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

        boletos = BBBoletoService.consultar_por_nota_valor(numero_nota, valor)
        return jsonify(
            {
                "sucesso": True,
                "fonte": "local",
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

    resultado = BBBoletoService.consultar_boletos(doc)
    return jsonify(
        {
            "sucesso": True,
            "fonte": resultado["fonte"],
            "boletos": resultado["boletos"],
            "total": len(resultado["boletos"]),
            "mensagem": resultado.get("mensagem", ""),
        }
    )
