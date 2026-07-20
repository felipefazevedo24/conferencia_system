"""Geração do Laudo Técnico de Materiais e Tratamento Térmico (Qualidade) em PDF.

Reproduz o modelo Excel (qualidade_certificado_modelo.xlsx) usando ReportLab,
preenchendo os campos editáveis (amarelos) com os dados registrados na tela de
Qualidade: Nº do certificado, OS/Lote-CP e os resultados das linhas Grid e
Sapatas (Dureza, CHD e Resultado).
"""
from io import BytesIO
from pathlib import Path
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Paleta Columbia (extraída do modelo)
COR_ESCURA = colors.HexColor("#1C1C1C")
COR_VERMELHO = colors.HexColor("#C8102E")
COR_CINZA_CLARO = colors.HexColor("#F2F2F2")
COR_CINZA_SECAO = colors.HexColor("#2D2D2D")
COR_CINZA_HEADER = colors.HexColor("#3A3A3A")
COR_AMARELO = colors.HexColor("#FFF9C4")
COR_TEXTO = colors.HexColor("#1C1C1C")

EMPRESA_LINHA1 = "COLUMBIA MACHINE BRASIL"
EMPRESA_LINHA2 = (
    "Av. Carlos Roberto Prataviera, 600 · Hortolândia/SP  |  "
    "CNPJ: 30.482.274/0001-25  |  (19) 3869-4025"
)

_styles = getSampleStyleSheet()


def _p(text, size=8.5, color=COR_TEXTO, bold=False, align=TA_LEFT, leading=None):
    style = ParagraphStyle(
        name=f"s{size}{bold}{align}",
        parent=_styles["Normal"],
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        textColor=color,
        alignment=align,
        leading=leading or (size + 3),
    )
    return Paragraph(text or "", style)


def _valor(v):
    return str(v).strip() if v is not None and str(v).strip() else "—"


def _fmt_data(v):
    """Formata datetime para dd/mm/aaaa; retorna vazio quando ausente."""
    if not v:
        return ""
    if isinstance(v, str):
        txt = v.strip()
        if not txt:
            return ""
        formatos = [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
        ]
        for fmt in formatos:
            try:
                return datetime.strptime(txt, fmt).strftime("%d/%m/%Y")
            except Exception:
                continue
        return ""
    try:
        return v.strftime("%d/%m/%Y")
    except Exception:
        return ""


def _secao(titulo):
    t = Table([[_p(titulo, size=9, color=colors.white, bold=True)]], colWidths=[180 * mm], hAlign="LEFT")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_SECAO),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def _obter_caminho_logo() -> str | None:
    """Retorna o caminho do logo institucional existente no static/ do projeto."""
    raiz = Path(__file__).resolve().parents[2]
    candidatos = [
        raiz / "static" / "columbia_red_molds.png",
        raiz / "static" / "columbia_sync_logo_v2.png",
        raiz / "static" / "columbia_logo.png",
    ]
    for p in candidatos:
        if p.is_file():
            return str(p)
    return None


def gerar_laudo_pdf(registro) -> bytes:
    """Gera o PDF do laudo a partir de um QualidadeCertificado (ou objeto com os
    mesmos atributos). Retorna os bytes do PDF."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title=f"Laudo Técnico - NF {getattr(registro, 'numero_nota', '')}",
    )
    largura = 180 * mm
    el = []

    # ---- Cabeçalho ----
    logo_path = _obter_caminho_logo()
    logo_cell = ""
    if logo_path:
        # Encaixa a logo no box do modelo Excel (~45,0mm x 13,8mm) sem distorcer.
        max_w = 45 * mm
        max_h = 13.8 * mm
        iw, ih = ImageReader(logo_path).getSize()
        escala = min(max_w / float(iw), max_h / float(ih)) if iw and ih else 1.0
        logo_cell = Image(logo_path, width=iw * escala, height=ih * escala)

    header = Table(
        [[logo_cell, _p(EMPRESA_LINHA1, size=11, color=colors.white, bold=True, align=TA_CENTER)],
         ["", _p(EMPRESA_LINHA2, size=7.5, color=colors.HexColor("#D0D0D0"), align=TA_CENTER)]],
        colWidths=[40 * mm, largura - 40 * mm],
        hAlign="LEFT",
    )
    header.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COR_ESCURA),
        ("SPAN", (0, 0), (0, 1)),
        ("VALIGN", (0, 0), (0, 1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    el.append(header)
    barra = Table([[""]], colWidths=[largura], rowHeights=[2.5 * mm], hAlign="LEFT")
    barra.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), COR_VERMELHO)]))
    el.append(barra)
    el.append(Spacer(1, 6))

    # ---- Título ----
    el.append(_p("LAUDO TÉCNICO DE MATERIAIS E TRATAMENTO TÉRMICO", size=13, bold=True, align=TA_CENTER))
    el.append(_p(
        f"Modelo vinculado ao Orçamento nº {_valor(getattr(registro, 'numero_orcamento', ''))}",
        size=8.5,
        align=TA_CENTER,
        color=colors.HexColor("#555555"),
    ))
    el.append(Spacer(1, 8))

    # ---- 1. IDENTIFICAÇÃO ----
    el.append(_secao("1.  IDENTIFICAÇÃO"))
    ident = Table(
        [
            [_p("Cliente:", bold=True), _p("")],
            [_p("CNPJ:", bold=True), _p("")],
            [_p("Produto:", bold=True), _p("")],
            [_p("Escopo:", bold=True), _p("")],
        ],
        colWidths=[28 * mm, largura - 28 * mm],
        hAlign="LEFT",
    )
    ident.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_CLARO),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    el.append(ident)
    el.append(Spacer(1, 8))

    # ---- 2. ESPECIFICAÇÕES TÉCNICAS ----
    el.append(_secao("2.  ESPECIFICAÇÕES TÉCNICAS"))
    especs_grid = [
        "Tratamento térmico: Cementação, têmpera e revenimento",
        "Camada cementada nominal: 1,5 a 1,8 mm",
        "Dureza superficial requerida: 62 a 64 HRC",
        "Profundidade efetiva da camada: mínima de 1,5 mm",
        "Critério de determinação: Perfil de microdureza Vickers até 550 HV",
    ]
    especs_sapatas = [
        "Tratamento térmico: Cementação, têmpera e revenimento",
        "Camada cementada nominal: 1,0 a 1,2 mm",
        "Dureza superficial requerida: 55 a 58 HRC",
        "Profundidade efetiva da camada: mínima de 1,0 mm",
        "Critério de determinação: Perfil de microdureza Vickers até 550 HV",
    ]

    def _bloco_espec(titulo, itens):
        linhas = [[_p(titulo, size=9, bold=True, align=TA_CENTER, color=colors.white)]]
        for it in itens:
            linhas.append([_p(f"• {it}", size=8)])
        t = Table(linhas, colWidths=[largura / 2], hAlign="LEFT")
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), COR_CINZA_HEADER),
            ("BACKGROUND", (0, 1), (-1, -1), COR_CINZA_CLARO),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.white),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        return t

    especs = Table(
        [[_bloco_espec("GRID", especs_grid), _bloco_espec("SAPATAS", especs_sapatas)]],
        colWidths=[(largura) / 2, (largura) / 2],
        hAlign="LEFT",
    )
    especs.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(especs)
    el.append(Spacer(1, 6))

    # Composição química + Nº Laudo do Material (numero_certificado)
    el.append(_p("Composição Química do Material (elementos relevantes para cementação)",
                 size=8, bold=True))
    quim = Table(
        [
            [_p("C (%)", bold=True, color=colors.white, align=TA_CENTER),
             _p("Mn (%)", bold=True, color=colors.white, align=TA_CENTER),
             _p("Si (%)", bold=True, color=colors.white, align=TA_CENTER),
             _p("Cr (%)", bold=True, color=colors.white, align=TA_CENTER),
             _p("Ni (%)", bold=True, color=colors.white, align=TA_CENTER),
             _p("Mo (%)", bold=True, color=colors.white, align=TA_CENTER),
             _p("Nº Laudo do Material", bold=True, color=colors.white, align=TA_CENTER)],
            [_p("0,25", align=TA_CENTER), _p("1,25", align=TA_CENTER), _p("0,17", align=TA_CENTER),
             _p("0,25", align=TA_CENTER), _p("0,15", align=TA_CENTER), _p("0,18", align=TA_CENTER),
             _p(_valor(getattr(registro, "numero_certificado", "")), bold=True, align=TA_CENTER)],
        ],
        colWidths=[18 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, largura - 108 * mm],
        hAlign="LEFT",
    )
    quim.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COR_CINZA_HEADER),
        ("BACKGROUND", (0, 1), (5, 1), colors.white),
        ("BACKGROUND", (6, 1), (6, 1), COR_AMARELO),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    el.append(quim)
    el.append(Spacer(1, 8))

    # ---- 3. CONTROLE DE QUALIDADE E RASTREABILIDADE ----
    el.append(_secao("3.  CONTROLE DE QUALIDADE E RASTREABILIDADE"))
    controles = [
        "1.  Cada lote de material e tratamento térmico deverá ser acompanhado por corpo de prova representativo.",
        "2.  A verificação da camada efetiva será realizada por perfil de microdureza Vickers, considerando o limite de 550 HV.",
        "3.  O molde será entregue acompanhado do laudo do corpo de prova, contendo identificação do lote, dureza superficial, perfil de microdureza e profundidade da camada cementada.",
        "4.  Os resultados efetivamente medidos deverão ser registrados nos campos abaixo antes da emissão final do laudo.",
    ]
    ctrl = Table([[_p(c, size=8)] for c in controles], colWidths=[largura], hAlign="LEFT")
    ctrl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_CLARO),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.white),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    el.append(ctrl)
    el.append(Spacer(1, 8))

    # ---- 4. REGISTRO DOS RESULTADOS DO CORPO DE PROVA ----
    el.append(_secao("4.  REGISTRO DOS RESULTADOS DO CORPO DE PROVA"))
    os_lote = _valor(getattr(registro, "os", ""))
    grid_tem = bool(getattr(registro, "grid_resultado", None) or getattr(registro, "grid_dureza", None))
    sapatas_tem = bool(getattr(registro, "sapatas_resultado", None) or getattr(registro, "sapatas_dureza", None))

    def _res_cell(valor):
        return _p(_valor(valor), bold=True, align=TA_CENTER)

    reg = Table(
        [
            [_p("Componente", bold=True, color=colors.white, align=TA_CENTER),
             _p("Lote / CP", bold=True, color=colors.white, align=TA_CENTER),
             _p("Dureza medida", bold=True, color=colors.white, align=TA_CENTER),
             _p("CHD medida", bold=True, color=colors.white, align=TA_CENTER),
             _p("Resultado", bold=True, color=colors.white, align=TA_CENTER)],
            [_p("Grid", bold=True, align=TA_CENTER), _res_cell(os_lote if grid_tem else ""),
             _res_cell(getattr(registro, "grid_dureza", "")),
             _res_cell(getattr(registro, "grid_chd", "")),
             _res_cell(getattr(registro, "grid_resultado", ""))],
            [_p("Sapatas", bold=True, align=TA_CENTER), _res_cell(os_lote if sapatas_tem else ""),
             _res_cell(getattr(registro, "sapatas_dureza", "")),
             _res_cell(getattr(registro, "sapatas_chd", "")),
             _res_cell(getattr(registro, "sapatas_resultado", ""))],
        ],
        colWidths=[32 * mm, 32 * mm, 40 * mm, 40 * mm, largura - 144 * mm],
        hAlign="LEFT",
    )
    reg.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COR_CINZA_HEADER),
        ("BACKGROUND", (1, 1), (-1, -1), COR_AMARELO),
        ("BACKGROUND", (0, 1), (0, -1), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    el.append(reg)
    el.append(Spacer(1, 8))

    # ---- 5. CONCLUSÃO ----
    el.append(_secao("5.  CONCLUSÃO"))
    conclusao = (
        "A conformidade final do grid e das sapatas será confirmada após o "
        "preenchimento dos resultados reais dos corpos de prova e a verificação de "
        "atendimento às faixas especificadas neste documento."
    )
    conc = Table([[_p(conclusao, size=8)]], colWidths=[largura], hAlign="LEFT")
    conc.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COR_CINZA_CLARO),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    el.append(conc)
    el.append(Spacer(1, 14))

    # ---- Assinaturas ----
    analista = _valor(getattr(registro, "analista", ""))
    data_esquerda = _fmt_data(getattr(registro, "analisado_em", None) or getattr(registro, "criado_em", None))
    data_direita = _fmt_data(getattr(registro, "aprovado_em", None))
    assin = Table(
        [
            [_p("_______________________________", align=TA_CENTER),
             _p("_______________________________", align=TA_CENTER)],
            [_p("Responsável Técnico", bold=True, align=TA_CENTER),
             _p("Gerente de Produção", bold=True, align=TA_CENTER)],
            [_p(f"Nome: {analista}", size=8, align=TA_CENTER),
             _p("Nome: _______________________________", size=8, align=TA_CENTER)],
            [_p(f"Data: {data_esquerda or '____/____/________'}", size=8, align=TA_CENTER),
             _p(f"Data: {data_direita or '____/____/________'}", size=8, align=TA_CENTER)],
        ],
        colWidths=[largura / 2, largura / 2],
        hAlign="LEFT",
    )
    assin.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    el.append(assin)
    el.append(Spacer(1, 10))

    # ---- Rodapé ----
    barra2 = Table([[""]], colWidths=[largura], rowHeights=[2.5 * mm], hAlign="LEFT")
    barra2.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), COR_VERMELHO)]))
    el.append(barra2)
    el.append(_p("Documento emitido pelo módulo de Qualidade · Columbia Sync", size=7.5,
                 color=colors.HexColor("#777777")))

    doc.build(el)
    return buf.getvalue()
