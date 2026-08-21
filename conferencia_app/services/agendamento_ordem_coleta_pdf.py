from __future__ import annotations

from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


_styles = getSampleStyleSheet()
COR_PRIMARIA = colors.HexColor("#1B3B6F")
COR_TEXTO = colors.HexColor("#1C1C1C")
COR_LINHA = colors.HexColor("#D1D5DB")
COR_ZEBRA = colors.HexColor("#F8FAFC")


def _p(text, *, size=9, bold=False, color=COR_TEXTO, align=TA_LEFT, leading=None):
    style = ParagraphStyle(
        name=f"oc{size}{bold}{align}",
        parent=_styles["Normal"],
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        textColor=color,
        alignment=align,
        leading=leading or (size + 3),
    )
    return Paragraph(str(text if text not in (None, "") else "-"), style)


def _fmt_data(value):
    if not value:
        return "-"
    try:
        return value.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(value)


def _fmt_num(value):
    try:
        n = float(value)
        if n.is_integer():
            return str(int(n))
        return f"{n:.2f}".replace(".", ",")
    except Exception:
        return str(value or "-")


def _logo_path() -> str | None:
    raiz = Path(__file__).resolve().parents[2]
    for nome in ("columbia_sync_logo_v2.png", "columbia_logo.png"):
        p = raiz / "static" / nome
        if p.is_file():
            return str(p)
    return None


def _endereco(row) -> str:
    campos = [
        str(getattr(row, "logradouro", "") or "").strip(),
        str(getattr(row, "numero", "") or "").strip(),
        str(getattr(row, "bairro", "") or "").strip(),
        str(getattr(row, "cidade", "") or "").strip(),
        str(getattr(row, "uf", "") or "").strip(),
        str(getattr(row, "cep", "") or "").strip(),
    ]
    return " | ".join([c for c in campos if c]) or "Endereço não informado"


def gerar_ordem_coleta_pdf(solicitacao, itens: list) -> bytes:
    buf = BytesIO()
    largura = 175 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"Ordem de Coleta {getattr(solicitacao, 'codigo', '') or getattr(solicitacao, 'id', '')}",
    )

    el = []

    logo = _logo_path()
    logo_cell = ""
    if logo:
        iw, ih = ImageReader(logo).getSize()
        max_w, max_h = 48 * mm, 16 * mm
        escala = min(max_w / float(iw), max_h / float(ih)) if iw and ih else 1.0
        logo_cell = Image(logo, width=iw * escala, height=ih * escala)

    head = Table(
        [[
            logo_cell,
            [
                _p("COLUMBIA MACHINE BRASIL", bold=True, color=COR_PRIMARIA, align=TA_RIGHT),
                _p("ORDEM DE COLETA", size=14, bold=True, color=COR_PRIMARIA, align=TA_RIGHT),
                _p(f"Emitido em: {_fmt_data(getattr(solicitacao, 'criado_em', None))}", size=8, align=TA_RIGHT),
            ],
        ]],
        colWidths=[largura * 0.42, largura * 0.58],
    )
    head.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(head)
    el.append(Spacer(1, 7))

    barra = Table([[""]], colWidths=[largura], rowHeights=[1.6 * mm])
    barra.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), COR_PRIMARIA)]))
    el.append(barra)
    el.append(Spacer(1, 10))

    dados = Table(
        [
            [_p("Código", bold=True), _p(getattr(solicitacao, "codigo", "-") or "-")],
            [_p("OC", bold=True), _p(getattr(solicitacao, "numero_oc", "-") or getattr(solicitacao, "documento_numero", "-") or "-")],
            [_p("Fornecedor", bold=True), _p(getattr(solicitacao, "parceiro_nome", "-") or "-")],
            [_p("Razão Social", bold=True), _p(getattr(solicitacao, "parceiro_razao_social", "-") or "-")],
            [_p("CNPJ", bold=True), _p(getattr(solicitacao, "parceiro_documento", "-") or "-")],
            [_p("Endereço de Coleta", bold=True), _p(_endereco(solicitacao))],
            [_p("Contato", bold=True), _p((getattr(solicitacao, "contato", "") or "-") + "  |  " + (getattr(solicitacao, "telefone", "") or "-"))],
        ],
        colWidths=[40 * mm, largura - 40 * mm],
    )
    dados.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, COR_LINHA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    el.append(dados)
    el.append(Spacer(1, 10))

    linhas = [[
        _p("Seq.", size=8, bold=True, color=colors.white),
        _p("Descrição", size=8, bold=True, color=colors.white),
        _p("Qtd.", size=8, bold=True, color=colors.white, align=TA_RIGHT),
        _p("Un.", size=8, bold=True, color=colors.white, align=TA_RIGHT),
        _p("Volumes", size=8, bold=True, color=colors.white, align=TA_RIGHT),
    ]]

    total_itens = 0
    total_vol = 0.0
    for idx, item in enumerate(itens or [], start=1):
        qtd = getattr(item, "quantidade", 0) or 0
        vol = getattr(item, "volumes", 0) or 0
        total_itens += 1
        try:
            total_vol += float(vol)
        except Exception:
            pass
        linhas.append([
            _p(str(idx), size=8),
            _p(getattr(item, "descricao", "Item"), size=8),
            _p(_fmt_num(qtd), size=8, align=TA_RIGHT),
            _p(getattr(item, "unidade", "") or "-", size=8, align=TA_RIGHT),
            _p(_fmt_num(vol), size=8, align=TA_RIGHT),
        ])

    if len(linhas) == 1:
        linhas.append([_p("1", size=8), _p("Coleta sem item detalhado", size=8), _p("1", size=8, align=TA_RIGHT), _p("UN", size=8, align=TA_RIGHT), _p("1", size=8, align=TA_RIGHT)])
        total_itens = 1
        total_vol = 1.0

    tabela = Table(linhas, colWidths=[12 * mm, largura - 62 * mm, 16 * mm, 14 * mm, 20 * mm])
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_ZEBRA]),
        ("GRID", (0, 0), (-1, -1), 0.4, COR_LINHA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    el.append(tabela)
    el.append(Spacer(1, 8))

    resumo = Table(
        [[
            _p(f"Total de itens: {total_itens}", bold=True),
            _p(f"Total de volumes: {_fmt_num(total_vol)}", bold=True, align=TA_RIGHT),
        ]],
        colWidths=[largura * 0.6, largura * 0.4],
    )
    resumo.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(resumo)
    el.append(Spacer(1, 12))

    obs = str(getattr(solicitacao, "observacoes_logistica", "") or "").strip()
    if obs:
        el.append(_p("Observações da logística", bold=True, color=COR_PRIMARIA))
        el.append(_p(obs, size=8.5))
        el.append(Spacer(1, 12))

    el.append(_p("Assinatura do responsável pela coleta: ______________________________________________", size=9))

    doc.build(el)
    return buf.getvalue()
