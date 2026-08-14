"""Rotas do cadastro de fornecedores do Comex (pagina + API).

Compartilhado pelos Modulos 2 (PO) e 3 (Cotacao) - ver
conferencia_app/services/comex_fornecedor_service.py.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request, session

from ..auth import permission_required
from ..models import ComexFornecedor
from ..services import comex_fornecedor_service as svc

comex_fornecedor_bp = Blueprint("comex_fornecedor", __name__)

# Mesma permissao do modulo Comex - o cadastro de fornecedor e um
# complemento do modulo, nao uma pagina separada no catalogo de permissoes.
PERMISSION = "PAGE_COMEX"


def _fornecedor_payload(f: ComexFornecedor) -> dict:
    return {
        "id": f.id,
        "razao_social": f.razao_social,
        "nome_fantasia": f.nome_fantasia,
        "cnpj": f.cnpj,
        "email": f.email,
        "telefone": f.telefone,
        "tipo_fornecedor": f.tipo_fornecedor,
        "ativo": f.ativo,
        "criado_em": f.criado_em.isoformat() if f.criado_em else None,
        "criado_por": f.criado_por,
        "atualizado_em": f.atualizado_em.isoformat() if f.atualizado_em else None,
        "atualizado_por": f.atualizado_por,
    }


@comex_fornecedor_bp.route("/comex/fornecedores")
@permission_required(PERMISSION)
def fornecedores_page():
    return render_template("comex_fornecedores.html", user=session.get("username"))


@comex_fornecedor_bp.route("/api/comex/fornecedores", methods=["GET"])
@permission_required(PERMISSION)
def api_listar_fornecedores():
    tipo = request.args.get("tipo") or None
    busca = request.args.get("busca") or ""
    ativo_param = request.args.get("ativo")
    if ativo_param in (None, ""):
        ativo = True  # padrao: so ativos (tela de selecao)
    elif ativo_param == "todos":
        ativo = None
    else:
        ativo = ativo_param.lower() in ("1", "true", "sim")
    fornecedores = svc.listar_fornecedores(tipo=tipo, ativo=ativo, busca=busca)
    return jsonify({
        "fornecedores": [_fornecedor_payload(f) for f in fornecedores],
        "tipos": list(svc.TIPOS_FORNECEDOR),
    })


@comex_fornecedor_bp.route("/api/comex/fornecedores", methods=["POST"])
@permission_required(PERMISSION)
def api_criar_fornecedor():
    payload = request.get_json(silent=True) or {}
    usuario = session.get("username", "desconhecido")
    try:
        fornecedor = svc.criar_fornecedor(payload, usuario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Fornecedor cadastrado.", "fornecedor": _fornecedor_payload(fornecedor)})


@comex_fornecedor_bp.route("/api/comex/fornecedores/<int:fornecedor_id>", methods=["PUT"])
@permission_required(PERMISSION)
def api_atualizar_fornecedor(fornecedor_id):
    fornecedor = svc.obter_fornecedor(fornecedor_id)
    if not fornecedor:
        return jsonify({"error": "Fornecedor não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    usuario = session.get("username", "desconhecido")
    try:
        fornecedor = svc.atualizar_fornecedor(fornecedor, payload, usuario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"message": "Fornecedor atualizado.", "fornecedor": _fornecedor_payload(fornecedor)})


@comex_fornecedor_bp.route("/api/comex/fornecedores/<int:fornecedor_id>/ativo", methods=["POST"])
@permission_required(PERMISSION)
def api_alternar_ativo_fornecedor(fornecedor_id):
    fornecedor = svc.obter_fornecedor(fornecedor_id)
    if not fornecedor:
        return jsonify({"error": "Fornecedor não encontrado."}), 404
    payload = request.get_json(silent=True) or {}
    usuario = session.get("username", "desconhecido")
    fornecedor = svc.alternar_ativo(fornecedor, bool(payload.get("ativo", True)), usuario)
    return jsonify({"message": "Status atualizado.", "fornecedor": _fornecedor_payload(fornecedor)})
