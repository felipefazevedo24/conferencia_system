"""Pagina publica (sem login) para o proprio cliente/fornecedor atualizar o
cadastro. Fluxo:

    1) escolhe se e Cliente ou Fornecedor
    2) informa o CNPJ e busca os dados automaticamente (BrasilAPI)
    3) revisa/edita os campos, escolhe o Regime Tributario (obrigatorio)
    4) confirma que o e-mail e o contato real da empresa
    5) envia -> fica em fila de revisao interna (CadastroAtualizacaoPublica)
"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify, render_template, request

from ..extensions import db
from ..models import CadastroAtualizacaoPublica
from ..services import cadastro_workflow_service as cad_svc

atualizacao_cadastral_bp = Blueprint("atualizacao_cadastral", __name__)

REGIMES_VALIDOS = {"Lucro Real", "Lucro Presumido", "Simples Nacional", "MEI"}
TIPOS_VALIDOS = {"cliente", "fornecedor"}


@atualizacao_cadastral_bp.route("/atualizacao-cadastral")
def pagina_atualizacao_cadastral():
    return render_template("atualizacao_cadastral.html")


@atualizacao_cadastral_bp.route("/api/atualizacao-cadastral/consultar-cnpj", methods=["POST"])
def consultar_cnpj_publico():
    payload = request.get_json(silent=True) or {}
    cnpj = str(payload.get("cnpj") or "").strip()
    try:
        dados = cad_svc.consultar_cartao_cnpj(cnpj)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception:  # noqa: BLE001
        return jsonify({"error": "Não foi possível consultar o CNPJ agora. Tente novamente em instantes."}), 502
    return jsonify({"sucesso": True, "dados": dados})


@atualizacao_cadastral_bp.route("/api/atualizacao-cadastral", methods=["POST"])
def enviar_atualizacao_cadastral():
    payload = request.get_json(silent=True) or {}

    tipo = str(payload.get("tipo") or "").strip().lower()
    documento = cad_svc.normalizar_documento(str(payload.get("documento") or ""))
    regime = str(payload.get("regime_tributario") or "").strip()
    email = str(payload.get("email") or "").strip()
    email_confirmado = bool(payload.get("email_confirmado"))

    if tipo not in TIPOS_VALIDOS:
        return jsonify({"error": "Selecione se você é Cliente ou Fornecedor."}), 400
    if not cad_svc.cnpj_valido(documento):
        return jsonify({"error": "Informe um CNPJ válido com 14 dígitos."}), 400
    if regime not in REGIMES_VALIDOS:
        return jsonify({"error": "Selecione o Regime Tributário."}), 400
    if not email:
        return jsonify({"error": "Informe o e-mail de contato da empresa."}), 400
    if not email_confirmado:
        return jsonify({"error": "Confirme que o e-mail informado é o contato real da empresa."}), 400

    def _campo(nome, limite=None):
        valor = str(payload.get(nome) or "").strip()
        if limite:
            valor = valor[:limite]
        return valor or None

    registro = CadastroAtualizacaoPublica(
        tipo=tipo,
        documento=cad_svc.formatar_cnpj(documento),
        razao_social=_campo("razao_social", 220),
        nome_fantasia=_campo("nome_fantasia", 220),
        inscricao_estadual=_campo("inscricao_estadual", 40),
        regime_tributario=regime,
        endereco=_campo("endereco", 300),
        cep=_campo("cep", 12),
        municipio=_campo("municipio", 120),
        uf=(str(payload.get("uf") or "").strip().upper()[:2] or None),
        telefone=_campo("telefone", 40),
        email=email[:160],
        email_confirmado=email_confirmado,
        contato=_campo("contato", 120),
        observacoes=_campo("observacoes", 500),
        situacao_cadastral=_campo("situacao_cadastral", 60),
        fonte_cnpj=_campo("fonte_cnpj", 40),
        dados_json=json.dumps(payload, ensure_ascii=False),
        status="Pendente de revisão",
        origem_ip=(request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:60],
    )
    db.session.add(registro)
    db.session.commit()
    return jsonify({"sucesso": True, "id": registro.id})
