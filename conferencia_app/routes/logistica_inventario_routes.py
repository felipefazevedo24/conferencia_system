"""Rotas da funcionalidade de Inventario Inicial da Logistica."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

from flask import Blueprint, jsonify, render_template, request, session, send_file
from openpyxl import Workbook

from ..auth import permission_required
from ..extensions import db
from ..models import LogisticaInventarioInicial


logistica_inventario_bp = Blueprint("logistica_inventario", __name__)

PERMISSION = "PAGE_LOGISTICA_AGENDAMENTO"
UNIDADES_PADRAO = [
    "UN", "PC", "CX", "PCT", "RL", "KG", "G", "MG", "L", "ML", "M", "CM", "MM", "M2", "M3",
]


def _fmt_registro(row: LogisticaInventarioInicial) -> dict:
    return {
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
    return jsonify({"registros": [_fmt_registro(row) for row in rows]})


@logistica_inventario_bp.route("/api/logistica/inventario-inicial/exportar", methods=["GET"])
@permission_required(PERMISSION)
def exportar_inventario_inicial_excel():
    local = (request.args.get("local") or "").strip().lower()
    codigo = (request.args.get("codigo") or "").strip().lower()

    query = _build_query(local=local, codigo=codigo)
    rows = query.order_by(LogisticaInventarioInicial.criado_em.desc()).all()

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
    ws.append(headers)

    for row in rows:
        ws.append(
            [
                row.criado_em.strftime("%d/%m/%Y %H:%M:%S") if row.criado_em else "",
                row.local_codigo,
                row.codigo_produto,
                row.unidade_medida,
                float(row.quantidade or 0),
                row.lote or "",
                row.observacao or "",
                row.criado_por,
            ]
        )

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
