"""Rotas do Cardex (Logística > Inventário > Cardex). Regra de negócio em
services/logistica_cardex_service.py; exportação em logistica_cardex_export."""
from __future__ import annotations

from io import BytesIO

from flask import Blueprint, jsonify, render_template, request, send_file, session

from ..auth import has_permission, is_admin_session, permission_required
from ..services import logistica_cardex_export as export
from ..services import logistica_cardex_service as svc

logistica_cardex_bp = Blueprint("logistica_cardex", __name__)

PERMISSION = "PAGE_LOGISTICA_CARDEX"
PERMISSION_FECHAR = "PAGE_LOGISTICA_CARDEX_FECHAR"

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _pode_fechar() -> bool:
    return is_admin_session() or has_permission(PERMISSION_FECHAR, session.get("username"), session.get("role"))


def _filtros():
    inicio, fim = svc.parse_periodo(request.args.get("inicio"), request.args.get("fim"))
    return {
        "inicio": inicio,
        "fim": fim,
        "depositos": svc.parse_depositos(request.args.get("depositos")),
        "forcar": request.args.get("atualizar") == "1",
    }


def _resumo(filtros: dict) -> dict:
    return svc.consultar_resumo(
        filtros["inicio"], filtros["fim"], filtros["depositos"],
        familia=request.args.get("familia") or "",
        busca=request.args.get("busca") or "",
        somente_movimento=request.args.get("somente_movimento") == "1",
        forcar=filtros["forcar"],
    )


def _nome_arquivo(prefixo: str, filtros: dict, ext: str) -> str:
    return f"{prefixo}_{filtros['inicio'].strftime('%Y%m%d')}_{filtros['fim'].strftime('%Y%m%d')}.{ext}"


@logistica_cardex_bp.route("/logistica/cardex")
@permission_required(PERMISSION)
def cardex_page():
    inicio, fim = svc.periodo_padrao()
    return render_template(
        "logistica_cardex.html",
        user=session["username"],
        user_role=session.get("role", ""),
        periodo_inicio=inicio.isoformat(),
        periodo_fim=fim.isoformat(),
        pode_fechar=_pode_fechar(),
    )


@logistica_cardex_bp.route("/api/logistica/cardex/resumo", methods=["GET"])
@permission_required(PERMISSION)
def api_cardex_resumo():
    try:
        dados = _resumo(_filtros())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(dados)


@logistica_cardex_bp.route("/api/logistica/cardex/item", methods=["GET"])
@permission_required(PERMISSION)
def api_cardex_item():
    try:
        filtros = _filtros()
        dados = svc.consultar_item(request.args.get("codigo") or "", filtros["inicio"], filtros["fim"], filtros["depositos"], forcar=filtros["forcar"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if dados is None:
        return jsonify({"error": "Item sem saldo e sem movimento no período (ou código inexistente no GRV)."}), 404
    return jsonify(dados)


@logistica_cardex_bp.route("/api/logistica/cardex/conciliacao", methods=["GET"])
@permission_required(PERMISSION)
def api_cardex_conciliacao():
    try:
        filtros = _filtros()
        dados = svc.conciliar(filtros["inicio"], filtros["fim"], forcar=filtros["forcar"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(dados)


@logistica_cardex_bp.route("/api/logistica/cardex/exportar.xlsx", methods=["GET"])
@permission_required(PERMISSION)
def api_cardex_exportar_xlsx():
    """tipo=resumo (só o resumo), completo (resumo + Modelo 7 de cada item
    com movimento, respeitando os filtros) ou item (Modelo 7 de um item)."""
    tipo = request.args.get("tipo") or "resumo"
    try:
        filtros = _filtros()
        if tipo == "item":
            dados = svc.consultar_item(request.args.get("codigo") or "", filtros["inicio"], filtros["fim"], filtros["depositos"], forcar=filtros["forcar"])
            if dados is None:
                return jsonify({"error": "Item sem saldo e sem movimento no período."}), 404
            if not dados["disponivel"]:
                return jsonify({"error": dados.get("erro") or "GRV indisponível."}), 502
            conteudo = export.gerar_xlsx(None, dados, [dados["item"]])
            nome = _nome_arquivo(f"cardex_{dados['item']['codigo']}", filtros, "xlsx")
        elif tipo in ("resumo", "completo"):
            resumo = _resumo(filtros)
            if not resumo["disponivel"]:
                return jsonify({"error": resumo.get("erro") or "GRV indisponível."}), 502
            detalhes = None
            if tipo == "completo":
                codigos = {l["codigo"] for l in resumo["linhas"]}
                base = svc.obter_periodo(filtros["inicio"], filtros["fim"])
                detalhes = [
                    d for d in (svc.detalhe(i, filtros["depositos"]) for i in base["itens"] if i["codigo"] in codigos)
                    if d["linhas"] or abs(d["inicial"]["qtde"]) > svc.EPS
                ]
            conteudo = export.gerar_xlsx(resumo, resumo, detalhes)
            nome = _nome_arquivo(f"cardex_{tipo}", filtros, "xlsx")
        else:
            return jsonify({"error": "Tipo de exportação inválido."}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return send_file(BytesIO(conteudo), mimetype=MIME_XLSX, as_attachment=True, download_name=nome)


@logistica_cardex_bp.route("/api/logistica/cardex/exportar.pdf", methods=["GET"])
@permission_required(PERMISSION)
def api_cardex_exportar_pdf():
    tipo = request.args.get("tipo") or "resumo"
    try:
        filtros = _filtros()
        if tipo == "item":
            dados = svc.consultar_item(request.args.get("codigo") or "", filtros["inicio"], filtros["fim"], filtros["depositos"], forcar=filtros["forcar"])
            if dados is None:
                return jsonify({"error": "Item sem saldo e sem movimento no período."}), 404
            if not dados["disponivel"]:
                return jsonify({"error": dados.get("erro") or "GRV indisponível."}), 502
            conteudo = export.gerar_pdf_item(dados, dados["item"])
            nome = _nome_arquivo(f"cardex_{dados['item']['codigo']}", filtros, "pdf")
        elif tipo == "resumo":
            resumo = _resumo(filtros)
            if not resumo["disponivel"]:
                return jsonify({"error": resumo.get("erro") or "GRV indisponível."}), 502
            conteudo = export.gerar_pdf_resumo(resumo)
            nome = _nome_arquivo("cardex_resumo", filtros, "pdf")
        else:
            return jsonify({"error": "Tipo de exportação inválido."}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return send_file(BytesIO(conteudo), mimetype="application/pdf", as_attachment=True, download_name=nome)


# Desconsiderar é de quem tem acesso à página (decisão do dono do módulo),
# não só de quem fecha o mês. Fica registrado quem marcou e o motivo.
@logistica_cardex_bp.route("/api/logistica/cardex/desconsiderados", methods=["GET"])
@permission_required(PERMISSION)
def api_cardex_desconsiderados():
    return jsonify({"desconsiderados": svc.listar_desconsiderados()})


@logistica_cardex_bp.route("/api/logistica/cardex/desconsiderados", methods=["POST"])
@permission_required(PERMISSION)
def api_cardex_desconsiderar():
    payload = request.get_json(silent=True) or {}
    try:
        registro = svc.desconsiderar(
            payload.get("tipo"), payload.get("chave"), payload.get("motivo"),
            session["username"], descricao=payload.get("descricao") or "",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"desconsiderado": registro}), 201


@logistica_cardex_bp.route("/api/logistica/cardex/desconsiderados/<int:desconsiderado_id>", methods=["DELETE"])
@permission_required(PERMISSION)
def api_cardex_reconsiderar(desconsiderado_id: int):
    try:
        registro = svc.reconsiderar(desconsiderado_id, session["username"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if registro is None:
        return jsonify({"error": "Registro não encontrado."}), 404
    return jsonify({"desconsiderado": registro})


@logistica_cardex_bp.route("/api/logistica/cardex/fechamentos", methods=["GET"])
@permission_required(PERMISSION)
def api_cardex_fechamentos():
    return jsonify({"fechamentos": svc.listar_fechamentos(), "pode_fechar": _pode_fechar()})


@logistica_cardex_bp.route("/api/logistica/cardex/fechamentos", methods=["POST"])
@permission_required(PERMISSION_FECHAR)
def api_cardex_fechar():
    payload = request.get_json(silent=True) or {}
    try:
        fechamento = svc.fechar_mes(payload.get("ano"), payload.get("mes"), session["username"])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"fechamento": fechamento}), 201


@logistica_cardex_bp.route("/api/logistica/cardex/fechamentos/<int:fechamento_id>/reabrir", methods=["POST"])
@permission_required(PERMISSION_FECHAR)
def api_cardex_reabrir(fechamento_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        fechamento = svc.reabrir_fechamento(fechamento_id, session["username"], payload.get("motivo") or "")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if fechamento is None:
        return jsonify({"error": "Fechamento não encontrado."}), 404
    return jsonify({"fechamento": fechamento})


@logistica_cardex_bp.route("/api/logistica/cardex/fechamentos/<int:fechamento_id>/conferir", methods=["GET"])
@permission_required(PERMISSION)
def api_cardex_conferir(fechamento_id: int):
    try:
        dados = svc.conferir_fechamento(fechamento_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if dados is None:
        return jsonify({"error": "Fechamento não encontrado."}), 404
    return jsonify(dados)
