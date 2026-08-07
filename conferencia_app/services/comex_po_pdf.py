"""Geracao do PDF da PO (Modulo 2 do Comex) em ReportLab.

Mesmo padrao usado em conferencia_app/services/qualidade_laudo_pdf.py:
SimpleDocTemplate + Table/Paragraph -> bytes, servido via send_file.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

COR_ESCURA = colors.HexColor("#1C1C1C")
COR_VERMELHO = colors.HexColor("#C8102E")
COR_CINZA_CLARO = colors.HexColor("#F2F2F2")

_styles = getSampleStyleSheet()


def _p(text, size=9, bold=False, color=COR_ESCURA):
    style = ParagraphStyle(
        name=f"po{size}{bold}",
        parent=_styles["Normal"],
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        textColor=color,
        alignment=TA_LEFT,
        leading=size + 3,
    )
    return Paragraph(text if text not in (None, "") else "—", style)


def _fmt_data(v):
    if not v:
        return "—"
    try:
        return v.strftime("%d/%m/%Y")
    except AttributeError:
        return str(v)


def _fmt_valor(v):
    if v is None:
        return "—"
    try:
        return f"US$ {float(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    except (TypeError, ValueError):
        return str(v)


def _fmt_qtd(v):
    if v is None:
        return "—"
    try:
        v = float(v)
        return str(int(v)) if v.is_integer() else f"{v:g}"
    except (TypeError, ValueError):
        return str(v)


def gerar_po_pdf(processo, ocs_vinculadas: list | None = None, itens: list | None = None) -> bytes:
    """Gera o PDF da Purchase Order (PO) de um ComexProcesso."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=18 * mm,
        bottomMargin=15 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"PO {processo.po_numero or processo.id_op}",
    )
    el = []

    el.append(_p("COLUMBIA MACHINE BRASIL", size=15, bold=True, color=COR_VERMELHO))
    el.append(_p("Purchase Order (PO) — Comex", size=11, bold=True))
    el.append(Spacer(1, 10))

    cabecalho = Table(
        [
            [_p("ID OP", bold=True), _p(processo.id_op),
             _p("Nº PO", bold=True), _p(processo.po_numero or "—")],
            [_p("Fornecedor", bold=True), _p(processo.fornecedor),
             _p("Tipo de operação", bold=True), _p("Importação Marítima" if processo.tipo_operacao == "IM" else "Importação Aérea")],
            [_p("OC(s) vinculada(s)", bold=True), _p(str(processo.cod_ordem_compra)),
             _p("Pagador do frete", bold=True), _p(processo.pagador_frete or "—")],
            [_p("Comprador", bold=True), _p(processo.comprador),
             _p("Data de lançamento", bold=True), _p(_fmt_data(processo.dt_lancamento_oc))],
        ],
        colWidths=[38 * mm, 55 * mm, 38 * mm, 45 * mm],
    )
    cabecalho.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), COR_CINZA_CLARO),
        ("BACKGROUND", (2, 0), (2, -1), COR_CINZA_CLARO),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    el.append(cabecalho)
    el.append(Spacer(1, 14))

    if itens:
        # Itens de linha da PO, no mesmo formato do modelo (CODE, Quantity,
        # NCM/HS CODE, PN, DESCRIPTION, UNIT US$, Line Total USD).
        el.append(_p("Itens", size=10, bold=True))
        el.append(Spacer(1, 4))
        cabecalho_itens = ["CODE", "Quantity", "NCM/HS", "PN", "DESCRIPTION", "UNIT US$", "Line Total"]
        linhas_itens = [[_p(c, bold=True, color=colors.white) for c in cabecalho_itens]]
        subtotal = 0.0
        for it in itens:
            linhas_itens.append([
                _p(it.codigo),
                _p(_fmt_qtd(it.quantidade)),
                _p(it.ncm),
                _p(it.pn),
                _p(it.descricao),
                _p(_fmt_valor(it.valor_unitario)),
                _p(_fmt_valor(it.valor_total)),
            ])
            subtotal += float(it.valor_total or 0)
        tabela_itens = Table(
            linhas_itens,
            colWidths=[20 * mm, 14 * mm, 18 * mm, 20 * mm, 52 * mm, 22 * mm, 24 * mm],
        )
        tabela_itens.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COR_ESCURA),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        el.append(tabela_itens)
        el.append(Spacer(1, 8))

        totais = Table(
            [[_p("Subtotal", bold=True), _p(_fmt_valor(subtotal), bold=True)],
             [_p("TOTAL", bold=True, color=colors.white), _p(_fmt_valor(subtotal), bold=True, color=colors.white)]],
            colWidths=[30 * mm, 30 * mm],
            hAlign="RIGHT",
        )
        totais.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COR_CINZA_CLARO),
            ("BACKGROUND", (0, 1), (-1, 1), COR_VERMELHO),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        el.append(totais)
        el.append(Spacer(1, 16))
    else:
        el.append(_p("Ordens de Compra vinculadas", size=10, bold=True))
        el.append(Spacer(1, 4))
        el.append(_p(
            "Nenhum item detalhado ainda — adicione os itens na tela da PO "
            "(código, NCM, PN, descrição, quantidade e valores).",
            size=8.5,
        ))
        el.append(Spacer(1, 6))
        linhas_oc = [[_p("Cód. OC", bold=True), _p("Valor produtos", bold=True), _p("Valor total", bold=True)]]
        linhas_oc.append([
            _p(str(processo.cod_ordem_compra)),
            _p(_fmt_valor(processo.total_produtos_oc)),
            _p(_fmt_valor(processo.total_oc)),
        ])
        for oc in (ocs_vinculadas or []):
            linhas_oc.append([
                _p(str(oc.cod_ordem_compra)),
                _p(_fmt_valor(oc.total_produtos_oc)),
                _p(_fmt_valor(oc.total_oc)),
            ])
        tabela_oc = Table(linhas_oc, colWidths=[50 * mm, 50 * mm, 50 * mm])
        tabela_oc.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), COR_ESCURA),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        el.append(tabela_oc)
        el.append(Spacer(1, 16))

    el.append(_p(
        "Este documento consolida os dados da(s) Ordem(ns) de Compra listada(s) acima "
        "para fins de instrução do processo de importação/exportação identificado pelo "
        f"ID OP {processo.id_op}.",
        size=8.5,
    ))

    doc.build(el)
    return buf.getvalue()
