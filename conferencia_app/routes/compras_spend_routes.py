from flask import Blueprint, jsonify, render_template, request, session

from ..auth import permission_required
from ..services import compras_spend_service as spend


compras_spend_bp = Blueprint("compras_spend", __name__)
PERM = "PAGE_COMPRAS_CPS"


def _usuario() -> str:
    return session.get("username", "desconhecido")


@compras_spend_bp.route("/compras/spend")
@permission_required(PERM)
def spend_page():
    return render_template(
        "compras_spend.html",
        user=session.get("username", "Compras"),
        user_role=session.get("role", ""),
    )


@compras_spend_bp.route("/api/compras/spend/painel")
@permission_required(PERM)
def spend_painel():
    args = request.args
    return jsonify(spend.painel(
        de=args.get("de"),
        ate=args.get("ate"),
        familia=args.get("familia"),
        classificacao=args.get("classificacao"),
        atual=args.get("atual"),
        anterior=args.get("anterior"),
    ))


@compras_spend_bp.route("/api/compras/spend/status")
@permission_required(PERM)
def spend_status():
    return jsonify(spend.status())


@compras_spend_bp.route("/api/compras/spend/atualizar", methods=["POST"])
@permission_required(PERM)
def spend_atualizar():
    try:
        resultado = spend.atualizar(_usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(resultado)


@compras_spend_bp.route("/api/compras/spend/revisao")
@permission_required(PERM)
def spend_revisao():
    return jsonify(spend.revisao(de=request.args.get("de"), ate=request.args.get("ate")))


@compras_spend_bp.route("/api/compras/spend/classificacao", methods=["POST"])
@permission_required(PERM)
def spend_salvar_classificacao():
    payload = request.get_json(silent=True) or {}
    try:
        spend.salvar_classificacao(
            payload.get("cod_fornecedor"), payload.get("codigo_item"), payload.get("classificacao"), _usuario(),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})
