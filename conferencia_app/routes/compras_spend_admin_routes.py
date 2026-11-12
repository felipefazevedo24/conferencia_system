"""TEMPORARIO - segundo Spend (query do administrador) para comparar com o
Spend atual em producao. Ver services/compras_spend_admin_service.py."""
import re

from flask import Blueprint, current_app, jsonify, render_template, request, session

from ..auth import permission_required
from ..services import compras_spend_admin_service as admin
from ..services import compras_spend_service as spend

compras_spend_admin_bp = Blueprint("compras_spend_admin", __name__)
PERM = "PAGE_COMPRAS_CPS"
_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _periodo():
    de, ate = request.args.get("de", ""), request.args.get("ate", "")
    if not (_MES.match(de) and _MES.match(ate)):
        raise ValueError("Informe o período no formato AAAA-MM.")
    return (de, ate) if de <= ate else (ate, de)


@compras_spend_admin_bp.route("/compras/spend-admin")
@permission_required(PERM)
def spend_admin_page():
    return render_template("compras_spend_admin.html", user=session.get("username", "Compras"))


@compras_spend_admin_bp.route("/api/compras/spend-admin/painel")
@permission_required(PERM)
def spend_admin_painel():
    try:
        de, ate = _periodo()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    try:
        dados = admin.painel(de, ate)
    except Exception:  # noqa: BLE001 - ERP fora nao derruba a tela
        current_app.logger.exception("Spend admin: falha na consulta ao ERP")
        return jsonify({"error": "Não deu para consultar o ERP agora. Tente de novo em instantes."}), 502
    # O Spend atual, no mesmo periodo, lido do espelho (nao consulta o ERP).
    atual = spend.painel(de=de, ate=ate)
    dados["sync"] = {"vazio": True} if atual.get("vazio") else {
        "total": atual["kpis"]["total"], "nfs": atual["kpis"]["nfs"],
        "por_mes": {m["competencia"]: m["valor"] for m in atual["por_mes"]},
        "fora_spend": atual["fora_spend"],
    }
    return jsonify(dados)


@compras_spend_admin_bp.route("/api/compras/spend-admin/composicao")
@permission_required(PERM)
def spend_admin_composicao():
    try:
        de, ate = _periodo()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    a = request.args
    try:
        linhas = admin.composicao(de, ate, cnpj=a.get("cnpj"), fornecedor=a.get("fornecedor"), tipo_item=a.get("tipo_item"))
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Spend admin: falha na composicao")
        return jsonify({"error": "Não deu para consultar o ERP agora."}), 502
    return jsonify({"notas": [{k: (str(v) if k == "data_entrada" and v else v) for k, v in r.items()} for r in linhas]})
