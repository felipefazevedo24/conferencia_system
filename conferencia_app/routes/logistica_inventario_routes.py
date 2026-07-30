"""Rotas da funcionalidade de Inventario Inicial da Logistica."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

from flask import Blueprint, current_app, jsonify, render_template, request, session, send_file
from openpyxl import Workbook

from ..auth import permission_required
from ..extensions import db
from ..models import LogisticaInventarioInicial
from ..services.erp_estoque_service import buscar_estoque_grv, qtde_grv_para


logistica_inventario_bp = Blueprint("logistica_inventario", __name__)

PERMISSION = "PAGE_LOGISTICA_INVENTARIO"
UNIDADES_PADRAO = [
    "UN", "PC", "CX", "PCT", "RL", "KG", "G", "MG", "L", "ML", "M", "CM", "MM", "M2", "M3",
]


def _fmt_registro(row: LogisticaInventarioInicial, estoque_grv: dict | None = None) -> dict:
    dados = {
        "id": row.id,
        "local_codigo": row.local_codigo,
        "codigo_produto": row.codigo_produto,
        "unidade_medida": row.unidade_medida,
        "quantidade": row.quantidade,
        "lote": row.lote or "",
        "observacao": row.observacao or "",
        "criado_por": row.criado_por,
        "criado_em": row.criado_em.isoformat() if row.criado_em else None,
        "atualizado_em": row.atualizado_em.isoformat() if row.atualizado_em else None,
    }
    # So calculado quando explicitamente pedido (tela de consulta/revisao).
    # A tela de contagem (Novo Inventario) NUNCA passa estoque_grv, para nao
    # vesar a conferencia com o saldo esperado - mesma logica da conferencia
    # cega usada no resto do sistema.
    if estoque_grv is not None:
        qtde_grv = qtde_grv_para(row.codigo_produto, row.local_codigo, estoque_grv)
        dados["qtde_grv"] = qtde_grv
        dados["divergente"] = (qtde_grv is not None) and (abs(float(row.quantidade or 0) - qtde_grv) > 0.001)
    return dados


def _build_query(local: str = "", codigo: str = ""):
    query = LogisticaInventarioInicial.query
    if local:
        query = query.filter(LogisticaInventarioInicial.local_codigo.ilike(f"%{local}%"))
    if codigo:
        query = query.filter(LogisticaInventarioInicial.codigo_produto.ilike(f"%{codigo}%"))
    return query


@logistica_inventario_bp.route("/logistica/inventario")
@permission_required(PERMISSION)
def inventario_home_page():
    return render_template(
        "logistica_inventario_home.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@logistica_inventario_bp.route("/logistica/inventario/novo")
@permission_required(PERMISSION)
def inventario_novo_page():
    return render_template(
        "logistica_inventario_inicial.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@logistica_inventario_bp.route("/logistica/inventario/consulta")
@permission_required(PERMISSION)
def inventario_consulta_page():
    return render_template(
        "logistica_inventario_consulta.html",
        user=session["username"],
        user_role=session.get("role", ""),
    )


@logistica_inventario_bp.route("/logistica/inventario-inicial")
@permission_required(PERMISSION)
def inventario_inicial_legacy_redirect():
    return inventario_novo_page()


@logistica_inventario_bp.route("/api/logistica/inventario-inicial/unidades", methods=["GET"])
@permission_required(PERMISSION)
def listar_unidades_padrao():
    return jsonify({"unidades": UNIDADES_PADRAO})


@logistica_inventario_bp.route("/api/logistica/inventario-inicial", methods=["GET"])
@permission_required(PERMISSION)
def listar_inventario_inicial():
    limite = request.args.get("limit", type=int) or 100
    limite = max(1, min(limite, 500))
    local = (request.args.get("local") or "").strip().lower()
    codigo = (request.args.get("codigo") or "").strip().lower()

    query = _build_query(local=local, codigo=codigo)

    rows = query.order_by(LogisticaInventarioInicial.criado_em.desc()).limit(limite).all()

    # A comparacao com o GRV so e calculada quando explicitamente pedida
    # (tela de consulta) - a tela de contagem nunca passa esse parametro,
    # entao nunca recebe o saldo esperado antes de fechar a contagem.
    estoque_grv = None
    erro_grv = None
    if request.args.get("comparar_grv") == "1":
        try:
            estoque_grv = buscar_estoque_grv()
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("Falha ao consultar estoque no GRV para comparacao: %s", exc)
            erro_grv = str(exc)

    resposta = {"registros": [_fmt_registro(row, estoque_grv) for row in rows]}
    if request.args.get("comparar_grv") == "1":
        resposta["grv_indisponivel"] = erro_grv if estoque_grv is None else None
    return jsonify(resposta)


@logistica_inventario_bp.route("/api/logistica/inventario-inicial/exportar", methods=["GET"])
@permission_required(PERMISSION)
def exportar_inventario_inicial_excel():
    local = (request.args.get("local") or "").strip().lower()
    codigo = (request.args.get("codigo") or "").strip().lower()
    comparar_grv = request.args.get("comparar_grv") == "1"

    query = _build_query(local=local, codigo=codigo)
    rows = query.order_by(LogisticaInventarioInicial.criado_em.desc()).all()

    estoque_grv = None
    if comparar_grv:
        try:
            estoque_grv = buscar_estoque_grv()
        except Exception as exc:  # noqa: BLE001
            current_app.logger.warning("Falha ao consultar estoque no GRV para exportar comparacao: %s", exc)

    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario"

    headers = [
        "Data",
        "Local",
        "Codigo Produto",
        "Unidade",
        "Quantidade",
        "Lote",
        "Observacao",
        "Criado Por",
    ]
    if comparar_grv:
        headers += ["Qtde GRV", "Divergente"]
    ws.append(headers)

    for row in rows:
        linha = [
            row.criado_em.strftime("%d/%m/%Y %H:%M:%S") if row.criado_em else "",
            row.local_codigo,
            row.codigo_produto,
            row.unidade_medida,
            float(row.quantidade or 0),
            row.lote or "",
            row.observacao or "",
            row.criado_por,
        ]
        if comparar_grv:
            qtde_grv = qtde_grv_para(row.codigo_produto, row.local_codigo, estoque_grv) if estoque_grv is not None else None
            divergente = (qtde_grv is not None) and (abs(float(row.quantidade or 0) - qtde_grv) > 0.001)
            linha += [qtde_grv if qtde_grv is not None else "N/D", "SIM" if divergente else "NAO"]
        ws.append(linha)

    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            val = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, len(val))
        ws.column_dimensions[col_letter].width = min(max(12, max_len + 2), 60)

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)

    nome_arquivo = f"inventario_logistica_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=nome_arquivo,
    )


@logistica_inventario_bp.route("/api/logistica/inventario-inicial", methods=["POST"])
@permission_required(PERMISSION)
def criar_inventario_inicial():
    payload = request.get_json(silent=True) or {}

    local_codigo = str(payload.get("local_codigo") or "").strip().upper()
    codigo_produto = str(payload.get("codigo_produto") or "").strip().upper()
    unidade_medida = str(payload.get("unidade_medida") or "UN").strip().upper() or "UN"
    lote = str(payload.get("lote") or "").strip()
    observacao = str(payload.get("observacao") or "").strip()

    if not local_codigo:
        return jsonify({"error": "Local e obrigatorio."}), 400
    if not codigo_produto:
        return jsonify({"error": "Codigo do produto e obrigatorio."}), 400

    try:
        quantidade = float(str(payload.get("quantidade") or "0").replace(",", "."))
    except (TypeError, ValueError):
        return jsonify({"error": "Quantidade invalida."}), 400

    if quantidade <= 0:
        return jsonify({"error": "Quantidade deve ser maior que zero."}), 400

    row = LogisticaInventarioInicial(
        local_codigo=local_codigo,
        codigo_produto=codigo_produto,
        unidade_medida=unidade_medida[:20],
        quantidade=quantidade,
        lote=lote[:120] if lote else None,
        observacao=observacao[:800] if observacao else None,
        criado_por=session.get("username", "sistema"),
        atualizado_em=datetime.now(),
    )
    db.session.add(row)
    db.session.commit()

    return jsonify({"sucesso": True, "registro": _fmt_registro(row)}), 201
