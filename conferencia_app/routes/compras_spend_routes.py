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


@compras_spend_bp.route("/api/compras/spend/importar", methods=["POST"])
@permission_required(PERM)
def spend_importar():
    try:
        resultado = spend.importar_planilha(request.files.get("arquivo"), _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(resultado)


@compras_spend_bp.route("/api/compras/spend/contas")
@permission_required(PERM)
def spend_contas():
    return jsonify(spend.listar_contas())


@compras_spend_bp.route("/api/compras/spend/contas", methods=["POST"])
@permission_required(PERM)
def spend_salvar_conta():
    payload = request.get_json(silent=True) or {}
    try:
        spend.salvar_classificacao_conta(payload.get("conta"), payload.get("classificacao"), _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@compras_spend_bp.route("/api/compras/spend/contas/<path:conta>/itens")
@permission_required(PERM)
def spend_itens_conta(conta):
    return jsonify(spend.itens_da_conta(conta))


@compras_spend_bp.route("/api/compras/spend/excecoes", methods=["POST"])
@permission_required(PERM)
def spend_salvar_excecao():
    payload = request.get_json(silent=True) or {}
    try:
        spend.salvar_excecao(payload.get("conta"), payload.get("codigo_item"), payload.get("classificacao"), _usuario())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})
