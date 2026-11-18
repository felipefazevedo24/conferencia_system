from datetime import datetime
from io import BytesIO

from flask import Blueprint, jsonify, render_template, send_file, session

from ..auth import permission_required
from ..services import compras_solicitacoes_service as solicitacoes


compras_solicitacoes_bp = Blueprint("compras_solicitacoes", __name__)
PERM = "PAGE_COMPRAS_SOLICITACOES"


@compras_solicitacoes_bp.route("/compras/solicitacoes")
@permission_required(PERM)
def solicitacoes_page():
    return render_template(
        "compras_solicitacoes.html",
        user=session.get("username", "Compras"),
        user_role=session.get("role", ""),
    )


@compras_solicitacoes_bp.route("/api/compras/solicitacoes")
@permission_required(PERM)
def solicitacoes_api():
    try:
        return jsonify(solicitacoes.analisar())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


_COLUNAS_XLSX = (
    ("Veredito", "veredito_label", 20), ("Solicitação", "solicitacao", 11), ("Situação", "situacao", 11),
    ("Data", "data", 11), ("Solicitante", "solicitante", 18), ("Código", "codigo", 14),
    ("Descrição", "descricao", 45), ("Família", "familia", 28), ("Unidade", "unidade", 8),
    ("Pedido", "pedido", 10), ("Certo", "certo", 10), ("Custo unitário (R$)", "custo", 14),
    ("Valor pedido (R$)", "valor_pedido", 14), ("Valor certo (R$)", "valor_certo", 14),
    ("Excesso (R$)", "excesso_valor", 13), ("Disponível", "disponivel", 11), ("Em OC", "em_oc", 10),
    ("Mínimo", "minimo", 10), ("Máximo", "maximo", 10), ("Mínimo GRV", "grv_minimo", 11),
    ("Lote GRV", "grv_lote", 10), ("Motivo", "motivo", 70),
)


@compras_solicitacoes_bp.route("/api/compras/solicitacoes.xlsx")
@permission_required(PERM)
def solicitacoes_xlsx():
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    try:
        dados = solicitacoes.analisar()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    wb = Workbook()
    ws = wb.active
    ws.title = "Solicitações"
    for col, (titulo, _, largura) in enumerate(_COLUNAS_XLSX, start=1):
        cel = ws.cell(row=1, column=col, value=titulo)
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill(start_color="0F62C9", end_color="0F62C9", fill_type="solid")
        ws.column_dimensions[cel.column_letter].width = largura
    for linha, item in enumerate(dados["itens"], start=2):
        for col, (_, chave, _) in enumerate(_COLUNAS_XLSX, start=1):
            valor = item.get(chave)
            if chave == "data" and valor:
                valor = datetime.strptime(valor, "%Y-%m-%d").date()
            cel = ws.cell(row=linha, column=col, value=valor)
            if chave == "data":
                cel.number_format = "DD/MM/YYYY"
            elif chave in ("custo", "valor_pedido", "valor_certo", "excesso_valor"):
                cel.number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="solicitacoes-de-compra.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
