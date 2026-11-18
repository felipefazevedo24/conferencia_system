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
        requisitante=args.get("requisitante"),
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


def _filtros_notas() -> dict:
    a = request.args
    return {
        "de": a.get("de"), "ate": a.get("ate"), "filtro_situacao": a.get("situacao"), "busca": a.get("busca"),
        "familia": a.get("familia"), "classificacoes": a.getlist("classificacao"), "requisitante": a.get("requisitante"),
    }


@compras_spend_bp.route("/api/compras/spend/notas")
@permission_required(PERM)
def spend_notas():
    return jsonify(spend.base_notas(offset=max(0, request.args.get("offset", 0, type=int)), **_filtros_notas()))


_COLUNAS_XLSX = (
    ("Lançamento", "data_lancamento", 12), ("NF", "nota_fiscal", 12), ("Cód. fornecedor", "cod_fornecedor", 10),
    ("Fornecedor", "fornecedor", 36), ("CFOP", "cfop", 8), ("Descrição do CFOP", "cfop_descricao", 40),
    ("Código do item", "codigo_item", 16), ("Descrição", "descricao", 45), ("Família", "familia", 28),
    ("Conta", "conta", 30), ("Valor (R$)", "valor", 14),
    ("OC", "ordem_compra", 10), ("Origem da OC", "oc_origem", 18),("Solicitação", "solicitacao", 14), ("Requisitante", "requisitante", 26),
    ("Comprador", "comprador", 18), ("Situação", "situacao_texto", 26),
    ("Motivo da decisão manual", "motivo_manual", 36), ("Decidido por", "manual_por", 14),
    ("Classificação", "classificacao", 28), ("Origem da classificação", "origem_cls", 18),
)


@compras_spend_bp.route("/api/compras/spend/notas.xlsx")
@permission_required(PERM)
def spend_notas_xlsx():
    """A Base de notas inteira do filtro (sem paginar), para conciliar no Excel."""
    from datetime import datetime
    from io import BytesIO

    from flask import send_file
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    dados = spend.base_notas(todas=True, **_filtros_notas())
    wb = Workbook()
    ws = wb.active
    ws.title = "Base de notas"
    for col, (titulo, _, largura) in enumerate(_COLUNAS_XLSX, start=1):
        cel = ws.cell(row=1, column=col, value=titulo)
        cel.font = Font(bold=True, color="FFFFFF")
        cel.fill = PatternFill(start_color="0F62C9", end_color="0F62C9", fill_type="solid")
        ws.column_dimensions[cel.column_letter].width = largura
    for linha, l in enumerate(dados["linhas"], start=2):
        l = {**l, "origem_cls": "Confirmado" if l["classificacao_manual"] else ("Palpite" if l["palpite"] else "Planilha do Custos")}
        for col, (_, chave, _) in enumerate(_COLUNAS_XLSX, start=1):
            valor = l.get(chave)
            if chave == "data_lancamento" and valor:
                valor = datetime.strptime(valor, "%d/%m/%Y").date()
            cel = ws.cell(row=linha, column=col, value=valor)
            if chave == "data_lancamento":
                cel.number_format = "DD/MM/YYYY"
            elif chave == "valor":
                cel.number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name=f"spend-base-notas-{dados['de']}-a-{dados['ate']}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@compras_spend_bp.route("/api/compras/spend/inclusao", methods=["POST"])
@permission_required(PERM)
def spend_salvar_inclusao():
    payload = request.get_json(silent=True) or {}
    try:
        spend.salvar_inclusao(
            payload.get("cod_fornecedor"), payload.get("codigo_item"), payload.get("incluir"),
            payload.get("motivo"), _usuario(),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


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
