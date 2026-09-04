"""Geracao do PDF do FORM-08.52 (Ajuste para Faturamento / Formulario para
Ajuste de Inventario) em ReportLab - documento formal gerado antes de
mandar um lote de divergencias do Inventario pro Finance.

Layout replica o modelo em Excel usado ate hoje pela empresa
(DOC_INVENT_2025_Rev_00): cabecalho com numero do documento, blocos de
Tipo de Ajuste / Motivo do Ajuste / Deposito-Local (com as opcoes fixas
impressas e a escolhida marcada, igual as caixinhas "( X )" do modelo),
tabela de itens ajustados, e rodape com as tres assinaturas (Solicitado
por / Aprovador Producao / Aprovador Contabil-Financeiro).

Mesmo padrao de biblioteca usado em comex_po_pdf.py: SimpleDocTemplate +
Table/Paragraph -> bytes, servido via send_file."""
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
COR_MUTED = colors.HexColor("#6B7280")

_styles = getSampleStyleSheet()


def _p(text, size=8.5, bold=False, color=COR_TEXTO, align=TA_LEFT, leading=None):
    style = ParagraphStyle(
        name=f"ria{size}{bold}{align}{color}",
        parent=_styles["Normal"],
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        textColor=color,
        alignment=align,
        leading=leading or (size + 3),
    )
    return Paragraph(text if text not in (None, "") else "—", style)


def _fmt_qtd(v):
    if v is None:
        return "—"
    try:
        v = float(v)
        return str(int(v)) if v.is_integer() else f"{v:g}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_valor(v):
    if v is None:
        return "—"
    try:
        texto = f"{float(v):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
        return f"R$ {texto}"
    except (TypeError, ValueError):
        return str(v)


def _obter_caminho_logo() -> str | None:
    raiz = Path(__file__).resolve().parents[2]
    for nome in ("columbia_sync_logo_v2.png", "columbia_logo.png"):
        caminho = raiz / "static" / nome
        if caminho.is_file():
            return str(caminho)
    return None


def _lista_opcoes(opcoes: list[str], selecionada: str, largura: float) -> Table:
    """Lista de opcoes fixas com a escolhida marcada com "( X )" - mesmo
    estilo de caixinha de selecao do formulario original em Excel."""
    linhas = []
    for opcao in opcoes:
        marcado = opcao == selecionada
        prefixo = "( X )" if marcado else "(    )"
        linhas.append([_p(f"{prefixo}  {opcao}", size=8, bold=marcado)])
    tabela = Table(linhas, colWidths=[largura])
    tabela.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return tabela


def gerar_relatorio_ajuste_pdf(relatorio, ajustes: list) -> bytes:
    """Gera o PDF do FORM-08.52 pra um relatorio (LogisticaInventarioRelatorioAjuste)
    e o lote de ajustes vinculados a ele."""
    from ..models import RELATORIO_AJUSTE_DEPOSITO_TIPOS, RELATORIO_AJUSTE_MOTIVOS, RELATORIO_AJUSTE_TIPOS

    buf = BytesIO()
    largura = 174 * mm
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        title=f"FORM-08.52 {relatorio.numero_documento}",
        pageCompression=0,  # mantem o texto legivel nos bytes crus (facilita teste, sem precisar de lib de leitura de PDF)
    )
    el = []

    # ---- Cabecalho: logo + FORM-08.52/Rev/Data ----
    logo_path = _obter_caminho_logo()
    logo_cell = _p("COLUMBIA MACHINE BRASIL", size=13, bold=True, color=COR_AZUL_ESCURA)
    if logo_path:
        max_w, max_h = 45 * mm, 14 * mm
        iw, ih = ImageReader(logo_path).getSize()
        escala = min(max_w / float(iw), max_h / float(ih)) if iw and ih else 1.0
        logo_cell = Image(logo_path, width=iw * escala, height=ih * escala)

    caixa_form = Table(
        [
            [_p("FORM-08.52", size=8, bold=True, align=TA_CENTER)],
            [_p("Rev.: 00", size=7.5, align=TA_CENTER)],
            [_p(f"Data: {relatorio.criado_em.strftime('%d/%m/%Y') if relatorio.criado_em else ''}", size=7.5, align=TA_CENTER)],
        ],
        colWidths=[38 * mm],
    )
    caixa_form.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.7, COR_AZUL_ESCURA),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D1D5DB")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    cabecalho = Table([[logo_cell, caixa_form]], colWidths=[largura - 38 * mm, 38 * mm])
    cabecalho.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(cabecalho)
    el.append(Spacer(1, 6))

    barra = Table([[""]], colWidths=[largura], rowHeights=[1.6 * mm])
    barra.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), COR_AZUL_ESCURA)]))
    el.append(barra)
    el.append(Spacer(1, 8))

    el.append(_p("AJUSTE PARA FATURAMENTO", size=15, bold=True, color=COR_AZUL_ESCURA, align=TA_CENTER))
    el.append(_p("Formulário para Ajuste de Inventário", size=9.5, color=COR_MUTED, align=TA_CENTER))
    el.append(Spacer(1, 4))
    el.append(_p(f"Documento: <b>{relatorio.numero_documento}</b>", size=10, align=TA_CENTER))
    el.append(Spacer(1, 12))

    # ---- Bloco: dados gerais (esquerda) + Tipo de Ajuste (direita) ----
    largura_esq = largura * 0.55
    largura_dir = largura - largura_esq

    dados_gerais = [
        [_p("Responsável:", size=8, bold=True), _p(relatorio.responsavel, size=8.5)],
        [_p("Solicitante:", size=8, bold=True), _p(relatorio.solicitante, size=8.5)],
        [_p("Depto:", size=8, bold=True), _p(relatorio.depto, size=8.5)],
    ]
    tabela_dados_gerais = Table(dados_gerais, colWidths=[28 * mm, largura_esq - 28 * mm])
    tabela_dados_gerais.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))

    bloco_topo = Table(
        [[
            tabela_dados_gerais,
            [_p("Tipo de Ajuste", size=8, bold=True, color=COR_MUTED), _lista_opcoes(RELATORIO_AJUSTE_TIPOS, relatorio.tipo_ajuste, largura_dir)],
        ]],
        colWidths=[largura_esq, largura_dir],
    )
    bloco_topo.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(bloco_topo)
    if relatorio.tipo_ajuste == "Outros" and relatorio.tipo_ajuste_detalhe:
        el.append(Spacer(1, 3))
        el.append(_p(f"<i>Detalhe:</i> {relatorio.tipo_ajuste_detalhe}", size=8))
    el.append(Spacer(1, 10))

    # ---- Bloco: Motivo do Ajuste (esquerda) + Depósito/Local (direita) ----
    motivo_bloco = [
        _p("Motivo do Ajuste:", size=8, bold=True, color=COR_MUTED),
        _lista_opcoes(RELATORIO_AJUSTE_MOTIVOS, relatorio.motivo_ajuste, largura_esq),
        Spacer(1, 3),
        _p(f"<i>Detalhe:</i> {relatorio.motivo_ajuste_detalhe}", size=8),
    ]
    deposito_bloco = [
        _p("Depósito/Local:", size=8, bold=True, color=COR_MUTED),
        _lista_opcoes(RELATORIO_AJUSTE_DEPOSITO_TIPOS, relatorio.deposito_tipo, largura_dir),
    ]
    if relatorio.deposito_local:
        deposito_bloco += [Spacer(1, 3), _p(f"<i>Local:</i> {relatorio.deposito_local}", size=8)]

    bloco_motivo = Table([[motivo_bloco, deposito_bloco]], colWidths=[largura_esq, largura_dir])
    bloco_motivo.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    el.append(bloco_motivo)
    el.append(Spacer(1, 10))

    if relatorio.observacoes_ajuste:
        el.append(_p("Observações do Ajuste:", size=8, bold=True, color=COR_MUTED))
        el.append(_p(relatorio.observacoes_ajuste, size=8.5))
        el.append(Spacer(1, 10))

    # ---- Tabela de itens ----
    el.append(_p("Detalhe dos itens a serem ajustados", size=9.5, bold=True, color=COR_AZUL_ESCURA))
    el.append(Spacer(1, 4))

    cabecalho_itens = [
        _p("Item", size=7.5, bold=True, color=colors.white, align=TA_CENTER),
        _p("Código", size=7.5, bold=True, color=colors.white),
        _p("Local", size=7.5, bold=True, color=colors.white),
        _p("UN", size=7.5, bold=True, color=colors.white, align=TA_CENTER),
        _p("Qtd Contábil", size=7.5, bold=True, color=colors.white, align=TA_RIGHT),
        _p("Qtd Física", size=7.5, bold=True, color=colors.white, align=TA_RIGHT),
        _p("Diferença", size=7.5, bold=True, color=colors.white, align=TA_RIGHT),
        _p("Vlr. Unit.", size=7.5, bold=True, color=colors.white, align=TA_RIGHT),
        _p("Vlr. Total", size=7.5, bold=True, color=colors.white, align=TA_RIGHT),
    ]
    linhas = [cabecalho_itens]
    valor_total_geral = 0.0
    for idx, a in enumerate(ajustes, start=1):
        valor_total_item = (a.diferenca or 0) * a.custo_medio if a.custo_medio is not None else None
        if valor_total_item is not None:
            valor_total_geral += valor_total_item
        linhas.append([
            _p(str(idx), size=8, align=TA_CENTER),
            _p(a.codigo_produto, size=8),
            _p(a.local_codigo, size=8),
            _p(a.unidade_medida, size=8, align=TA_CENTER),
            _p(_fmt_qtd(a.qtde_estoque_no_momento), size=8, align=TA_RIGHT),
            _p(_fmt_qtd(a.qtde_contada), size=8, align=TA_RIGHT),
            _p(_fmt_qtd(a.diferenca), size=8, align=TA_RIGHT),
            _p(_fmt_valor(a.custo_medio), size=8, align=TA_RIGHT),
            _p(_fmt_valor(valor_total_item), size=8, align=TA_RIGHT),
        ])

    # Colunas fixas (item/codigo/un/qtds/valores) - Descricao ocupa o resto
    # da largura da pagina, pra caber nome de produto grande sem estourar.
    # Ordem das colunas: Item, Codigo, Descricao, UN, Qtd Contabil,
    # Qtd Fisica, Diferenca, Vlr Unit., Vlr Total.
    larguras_fixas_mm = [10, 22, 10, 20, 20, 18, 22, 22]  # tudo exceto Descricao
    largura_desc = largura - sum(larguras_fixas_mm) * mm
    col_widths = [
        larguras_fixas_mm[0] * mm, larguras_fixas_mm[1] * mm, largura_desc, larguras_fixas_mm[2] * mm,
        larguras_fixas_mm[3] * mm, larguras_fixas_mm[4] * mm, larguras_fixas_mm[5] * mm,
        larguras_fixas_mm[6] * mm, larguras_fixas_mm[7] * mm,
    ]

    tabela_itens = Table(linhas, colWidths=col_widths, repeatRows=1)
    tabela_itens.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), COR_AZUL_TABELA),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D1D5DB")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_CINZA_CLARO]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    el.append(tabela_itens)
    el.append(Spacer(1, 6))

    if relatorio.observacoes_itens:
        el.append(_p(f"<b>OBS dos itens:</b> {relatorio.observacoes_itens}", size=8))
        el.append(Spacer(1, 6))

    total_tbl = Table(
        [[_p("VALOR Total do Ajuste", size=9.5, bold=True, color=colors.white, align=TA_RIGHT),
          _p(_fmt_valor(valor_total_geral), size=9.5, bold=True, color=colors.white, align=TA_RIGHT)]],
        colWidths=[largura - 40 * mm, 40 * mm],
    )
    total_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), COR_AZUL_ESCURA),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    el.append(total_tbl)
    el.append(Spacer(1, 22))

    # ---- Rodape: assinaturas ----
    linha_assinatura = "_" * 38
    rodape = Table(
        [
            [_p(linha_assinatura, size=9, align=TA_CENTER), _p(linha_assinatura, size=9, align=TA_CENTER), _p(linha_assinatura, size=9, align=TA_CENTER)],
            [_p(relatorio.criado_por or "", size=8.5, align=TA_CENTER), _p("", size=8.5, align=TA_CENTER), _p("", size=8.5, align=TA_CENTER)],
            [_p("Solicitado por", size=7.5, color=COR_MUTED, align=TA_CENTER),
             _p("Aprovador Produção", size=7.5, color=COR_MUTED, align=TA_CENTER),
             _p("Aprovador Contábil/Financeiro", size=7.5, color=COR_MUTED, align=TA_CENTER)],
        ],
        colWidths=[largura / 3.0] * 3,
    )
    rodape.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    el.append(rodape)
    el.append(Spacer(1, 6))
    el.append(_p(f"Data: {relatorio.criado_em.strftime('%d/%m/%Y %H:%M') if relatorio.criado_em else ''}", size=8, color=COR_MUTED))

    doc.build(el)
    return buf.getvalue()
