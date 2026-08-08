"""Geracao do PDF da PO (Modulo 2 do Comex) em ReportLab.

Layout replica o modelo real de Purchase Order ja usado pela Columbia
Machine Brasil (COMPANY/fornecedor + numero(s) de PO + data no topo,
tabela CODE/Quantity/NCM/PN/DESCRIPTION/UNIT US$/Line Total, Subtotal +
TOTAL destacado, nota de rodape sobre envio de documentos de embarque).

Mesmo padrao de biblioteca usado em qualidade_laudo_pdf.py: SimpleDocTemplate
+ Table/Paragraph -> bytes, servido via send_file.
"""
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

COR_AZUL_ESCURA = colors.HexColor("#1B3B6F")
COR_AZUL_TABELA = colors.HexColor("#3E6D93")
COR_CINZA_CLARO = colors.HexColor("#F2F2F2")
COR_TEXTO = colors.HexColor("#1C1C1C")

_styles = getSampleStyleSheet()

# Dados fixos da Columbia Machine Brasil (emitente da PO) - mesmo endereco
# usado no restante do sistema (ex.: template de PO/danfe).
EMITENTE_LINHA1 = "Columbia Machine Brasil - Estr. Carlos Roberto Prataviera, 600"
EMITENTE_LINHA2 = "Jardim Nova Europa - Hortolândia/SP- Brazil - 13.184-889"
EMITENTE_LINHA3 = "Phone: (+55) 19 3869 4025"

# Instrucao fixa de envio de documentos de embarque - a mesma que ja consta
# no modelo de PO usado hoje pela empresa.
NOTA_EMBARQUE = (
    "Once the shipment has been dispatched, please ensure that Larissa "
    "receives copies of the signed commercial invoice, copy AWB, and "
    "packing list at <font color='#C0392B'><b>laroli@colmac.com</b></font>. "
    "It is crucial to adhere to these instructions. Failure to comply may "
    "lead to complications and delays in payment for any and all freight "
    "costs associated with this order."
)


def _p(text, size=9, bold=False, color=COR_TEXTO, align=TA_LEFT, leading=None):
    style = ParagraphStyle(
        name=f"po{size}{bold}{align}",
        parent=_styles["Normal"],
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        textColor=color,
        alignment=align,
        leading=leading or (size + 3),
    )
    return Paragraph(text if text not in (None, "") else "—", style)


def _fmt_data(v):
    if not v:
        return "—"
    try:
        return v.strftime("%B %-d, %Y").upper()
    except (AttributeError, ValueError):
        try:
            return v.strftime("%d/%m/%Y")
        except AttributeError:
            return str(v)


def _fmt_qtd(v):
    if v is None:
        return "—"
    try:
        v = float(v)
        return str(int(v)) if v.is_integer() else f"{v:g}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_valor(v, com_simbolo=True):
    if v is None:
        return "—"
    try:
        texto = f"{float(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        return f"$ {texto}" if com_simbolo else texto
    except (TypeError, ValueError):
        return str(v)


def _obter_caminho_logo() -> str | None:
    raiz = Path(__file__).resolve().parents[2]
    for nome in ("columbia_sync_logo_v2.png", "columbia_logo.png"):
        caminho = raiz / "static" / nome
        if caminho.is_file():
            return str(caminho)
    return None


def gerar_po_pdf(processo, ocs_vinculadas: list | None = None, itens: list | None = None) -> bytes:
    """Gera o PDF da Purchase Order (PO) de um ComexProcesso, no layout do
    modelo real de PO da empresa."""
    buf = BytesIO()
    largura = 174 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"PO {processo.po_numero or processo.id_op}",
    )
    el = []

    # ---- Cabecalho: logo + dados do emitente ----
    logo_path = _obter_caminho_logo()
    logo_cell = ""
    if logo_path:
        max_w, max_h = 45 * mm, 16 * mm
        iw, ih = ImageReader(logo_path).getSize()
        escala = min(max_w / float(iw), max_h / float(ih)) if iw and ih else 1.0
        logo_cell = Image(logo_path, width=iw * escala, height=ih * escala)

    cabecalho = Table(
        [[
            logo_cell,
            [
                _p(EMITENTE_LINHA1, size=8.5, bold=True, color=COR_AZUL_ESCURA, align=TA_RIGHT),
                _p(EMITENTE_LINHA2, size=8.5, bold=True, color=COR_AZUL_ESCURA, align=TA_RIGHT),
                _p(EMITENTE_LINHA3, size=8.5, bold=True, color=COR_AZUL_ESCURA, align=TA_RIGHT),
            ],
        ]],
        colWidths=[largura * 0.4, largura * 0.6],
    )
    cabecalho.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(cabecalho)
    el.append(Spacer(1, 6))

    barra = Table([[""]], colWidths=[largura], rowHeights=[1.6 * mm])
    barra.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), COR_AZUL_ESCURA)]))
    el.append(barra)
    el.append(Spacer(1, 10))

    el.append(_p("PURCHASE ORDER", size=20, bold=True, color=COR_AZUL_ESCURA, align=TA_CENTER))
    el.append(Spacer(1, 14))

    # ---- COMPANY (fornecedor) + numero(s) da PO / data ----
    po_numeros = str(processo.cod_ordem_compra or "")
    if processo.po_ocs_vinculadas:
        import json as _json
        try:
            codigos = _json.loads(processo.po_ocs_vinculadas)
            po_numeros = " and ".join(str(c) for c in codigos if c)
        except (TypeError, ValueError):
            pass

    bloco_info = Table(
        [[
            [
                _p("COMPANY", size=7.5, bold=True, color=colors.HexColor("#6B7280")),
                Spacer(1, 3),
                _p(processo.fornecedor or "—", size=10, bold=True),
            ],
            [
                _p(f"Purchase Order: {po_numeros}", size=10, bold=True, align=TA_RIGHT),
                _p(_fmt_data(processo.dt_lancamento_oc), size=9, align=TA_RIGHT),
            ],
        ]],
        colWidths=[largura * 0.55, largura * 0.45],
    )
    bloco_info.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(bloco_info)
    el.append(Spacer(1, 16))

    # ---- Tabela de itens (CODE / Quantity / NCM-HS / PN / DESCRIPTION / UNIT US$ / Line Total) ----
    itens = itens or []
    cabecalho_itens = [
        _p("CODE", size=8, bold=True, color=colors.white),
        _p("Quantity", size=8, bold=True, color=colors.white),
        _p("NCM/<br/>HS CODE", size=8, bold=True, color=colors.white),
        _p("PN", size=8, bold=True, color=colors.white),
        _p("DESCRIPTION", size=8, bold=True, color=colors.white),
        _p("UNIT US$", size=8, bold=True, color=colors.white, align=TA_RIGHT),
        _p("Line Total USD", size=8, bold=True, color=colors.white, align=TA_RIGHT),
    ]
    linhas = [cabecalho_itens]
    subtotal = 0.0
    for it in itens:
        linhas.append([
            _p(it.codigo, size=8.5),
            _p(_fmt_qtd(it.quantidade), size=8.5),
            _p(it.ncm, size=8.5),
            _p(it.pn, size=8.5),
            _p(it.descricao, size=8.5),
            _p(_fmt_valor(it.valor_unitario, com_simbolo=False), size=8.5, align=TA_RIGHT),
            _p(_fmt_valor(it.valor_total), size=8.5, align=TA_RIGHT),
        ])
        subtotal += float(it.valor_total or 0)

    if not itens:
        linhas.append([_p("Nenhum item cadastrado ainda.", size=8.5)] + [""] * 6)

    larguras_fixas = [26 * mm, 16 * mm, 20 * mm, 16 * mm, 20 * mm, 24 * mm]
    largura_descricao = largura - sum(larguras_fixas)
    tabela_itens = Table(
        linhas,
        colWidths=[
            larguras_fixas[0], larguras_fixas[1], larguras_fixas[2], larguras_fixas[3],
            largura_descricao, larguras_fixas[4], larguras_fixas[5],
        ],
    )
    tabela_itens.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COR_AZUL_TABELA),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CINZA_CLARO]),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    el.append(tabela_itens)
    el.append(Spacer(1, 10))

    # ---- Subtotal / TOTAL ----
    totais = Table(
        [
            [_p("Subtotal", size=9, bold=True, align=TA_RIGHT), _p(_fmt_valor(subtotal), size=9, bold=True, align=TA_RIGHT)],
            [_p("TOTAL", size=10, bold=True, color=colors.white, align=TA_RIGHT),
             _p(_fmt_valor(subtotal), size=10, bold=True, color=colors.white, align=TA_RIGHT)],
        ],
        colWidths=[largura - 45 * mm, 45 * mm],
    )
    totais.setStyle(TableStyle([
        ("BACKGROUND", (0, 1), (-1, 1), COR_AZUL_ESCURA),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    el.append(totais)
    el.append(Spacer(1, 26))

    # ---- Nota de rodape sobre documentos de embarque ----
    el.append(_p(NOTA_EMBARQUE, size=8, color=colors.HexColor("#374151")))
    el.append(Spacer(1, 14))
    el.append(_p("THANK YOU FOR YOUR BUSINESS!", size=11, bold=True, color=COR_AZUL_ESCURA, align=TA_CENTER))

    doc.build(el)
    return buf.getvalue()
