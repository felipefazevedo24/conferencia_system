"""Geracao da Etiqueta de Expedicao ("Identificacao de Volume") em ReportLab,
pra impressora termica Zebra - PDF no tamanho exato da etiqueta (nao A4),
que abre numa aba nova e e' impresso pelo driver Windows da impressora,
mesmo fluxo ja usado pra PO/DANFE no resto do sistema.

Modelo "Red Molds" - dimensoes fornecidas pelo usuario:
    largura 104mm, dividida em 5 faixas verticais (topo -> base):
    logo (48.5mm) / titulo (15mm) / NF-Orcamento-OS (35mm) /
    Cliente-Endereco (52mm) / Volume+pictogramas (50mm).

ATENCAO: o logo "Columbia Red Molds" e os 3 pictogramas (manuseio, este
lado pra cima, mantenha seco) sao desenhados aqui como aproximacao
vetorial simples - nao sao os assets oficiais da marca/ISO 780. Se o
usuario fornecer os arquivos originais (logo em PNG/SVG, pictogramas em
PNG), da pra trocar por eles facilmente (ver _desenhar_logo/_desenhar_*).
"""
from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import portrait
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

COR_VERMELHO = colors.HexColor("#C8102E")
COR_PRETO = colors.HexColor("#000000")
COR_BRANCO = colors.white

# ── Dimensoes do modelo Red Molds (mm) ──────────────────────────────────
LARGURA = 104 * mm
H_LOGO = 48.5 * mm
H_TITULO = 15 * mm
H_NF = 35 * mm
H_CLIENTE = 52 * mm
H_VOL = 50 * mm
ALTURA = H_LOGO + H_TITULO + H_NF + H_CLIENTE + H_VOL  # 200.5mm

MARGEM = 4 * mm


def _quebrar_texto(c: canvas.Canvas, texto: str, fonte: str, tamanho: float, largura_max: float) -> list[str]:
    """Quebra `texto` em linhas que cabem em `largura_max` (pt), medindo
    com a fonte/tamanho atuais - sem depender de Platypus/Paragraph, pra
    manter controle exato de posicionamento em mm."""
    palavras = str(texto or "").split()
    if not palavras:
        return []
    linhas: list[str] = []
    atual = palavras[0]
    for palavra in palavras[1:]:
        candidata = f"{atual} {palavra}"
        if c.stringWidth(candidata, fonte, tamanho) <= largura_max:
            atual = candidata
        else:
            linhas.append(atual)
            atual = palavra
    linhas.append(atual)
    return linhas


def _desenhar_logo(c: canvas.Canvas, y0: float):
    """Faixa do logo (topo) - fundo vermelho, 'Columbia' + 'RED MOLDS' em
    branco. Aproximacao do logo oficial (ver docstring do modulo)."""
    c.setFillColor(COR_VERMELHO)
    c.rect(0, y0, LARGURA, H_LOGO, fill=1, stroke=0)

    centro_x = LARGURA / 2
    c.setFillColor(COR_BRANCO)
    c.setFont("Helvetica-BoldOblique", 26)
    c.drawCentredString(centro_x, y0 + H_LOGO * 0.58, "Columbia")
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(centro_x, y0 + H_LOGO * 0.32, "R E D   M O L D S")


def _desenhar_titulo(c: canvas.Canvas, y0: float):
    c.setFillColor(COR_PRETO)
    c.setFont("Helvetica-Bold", 13)
    c.drawCentredString(LARGURA / 2, y0 + H_TITULO / 2 - 4, "IDENTIFICAÇÃO DE VOLUME")


def _campo(c: canvas.Canvas, x: float, y: float, rotulo: str, valor: str, tamanho_rotulo=10, tamanho_valor=11):
    c.setFont("Helvetica-Bold", tamanho_rotulo)
    c.setFillColor(COR_PRETO)
    c.drawString(x, y, rotulo)
    largura_rotulo = c.stringWidth(rotulo + " ", "Helvetica-Bold", tamanho_rotulo)
    c.setFont("Helvetica-Bold", tamanho_valor)
    c.drawString(x + largura_rotulo, y, valor or "")


def _desenhar_nf(c: canvas.Canvas, y0: float, numero_nf: str, orcamento: str, os_texto: str):
    x = MARGEM
    y = y0 + H_NF - 11 * mm
    passo = 11 * mm
    _campo(c, x, y, "NOTA FISCAL:", numero_nf or "—")
    y -= passo
    _campo(c, x, y, "ORÇAMENTO:", orcamento or "—")
    y -= passo
    _campo(c, x, y, "OS:", os_texto or "")


def _desenhar_cliente(c: canvas.Canvas, y0: float, cliente: str, endereco_linhas: list[str]):
    x = MARGEM
    largura_max = LARGURA - 2 * MARGEM
    y = y0 + H_CLIENTE - 11 * mm

    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(COR_PRETO)
    c.drawString(x, y, "CLIENTE:")
    y -= 6 * mm

    c.setFont("Helvetica-Bold", 13)
    for linha in _quebrar_texto(c, cliente or "—", "Helvetica-Bold", 13, largura_max)[:2]:
        c.drawString(x, y, linha)
        y -= 6 * mm

    y -= 3 * mm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x, y, "ENDEREÇO:")
    y -= 6 * mm

    c.setFont("Helvetica", 10)
    linhas_endereco = endereco_linhas or ["—"]
    for linha in linhas_endereco[:3]:
        for sub in _quebrar_texto(c, linha, "Helvetica", 10, largura_max):
            c.drawString(x, y, sub)
            y -= 5 * mm


def _icone_manuseio(c: canvas.Canvas, cx: float, cy: float, lado: float):
    """Pictograma simplificado de equipamento de manuseio (carrinho +
    caixa) - aproximacao, nao e' o simbolo ISO 780 oficial."""
    c.saveState()
    c.setStrokeColor(COR_PRETO)
    c.setLineWidth(1.1)
    base = cy - lado * 0.32
    # rodas
    r = lado * 0.07
    c.circle(cx - lado * 0.18, base, r, stroke=1, fill=0)
    c.circle(cx + lado * 0.05, base, r, stroke=1, fill=0)
    # estrutura em L do carrinho
    p = c.beginPath()
    p.moveTo(cx - lado * 0.30, base)
    p.lineTo(cx - lado * 0.02, base)
    p.lineTo(cx - lado * 0.02, cy + lado * 0.28)
    c.drawPath(p, stroke=1, fill=0)
    # caixa apoiada
    c.rect(cx - lado * 0.02, base + lado * 0.02, lado * 0.34, lado * 0.30, stroke=1, fill=0)
    c.restoreState()


def _icone_este_lado_cima(c: canvas.Canvas, cx: float, cy: float, lado: float):
    """Pictograma 'este lado para cima' - duas setas verticais, mesmo
    desenho do simbolo ISO 780 padrao de embalagem."""
    c.saveState()
    c.setFillColor(COR_PRETO)
    for dx in (-lado * 0.16, lado * 0.16):
        largura_haste = lado * 0.10
        altura_haste = lado * 0.26
        base_y = cy - lado * 0.30
        c.rect(cx + dx - largura_haste / 2, base_y, largura_haste, altura_haste, stroke=0, fill=1)
        ponta = c.beginPath()
        topo = base_y + altura_haste
        ponta.moveTo(cx + dx - lado * 0.20, topo)
        ponta.lineTo(cx + dx + lado * 0.20, topo)
        ponta.lineTo(cx + dx, topo + lado * 0.20)
        ponta.close()
        c.drawPath(ponta, stroke=0, fill=1)
    c.restoreState()


def _icone_manter_seco(c: canvas.Canvas, cx: float, cy: float, lado: float):
    """Pictograma 'manter seco' - guarda-chuva simplificado sobre gotas."""
    c.saveState()
    c.setStrokeColor(COR_PRETO)
    c.setFillColor(COR_PRETO)
    c.setLineWidth(1.1)
    topo_y = cy + lado * 0.20
    raio = lado * 0.26
    # cupula do guarda-chuva
    c.arc(cx - raio, topo_y - raio, cx + raio, topo_y + raio, 0, 180)
    p = c.beginPath()
    p.moveTo(cx - raio, topo_y)
    p.lineTo(cx + raio, topo_y)
    c.drawPath(p, stroke=1)
    # cabo
    cabo = c.beginPath()
    cabo.moveTo(cx, topo_y)
    cabo.lineTo(cx, topo_y - lado * 0.34)
    c.drawPath(cabo, stroke=1)
    # gotas
    c.setLineWidth(0.9)
    for dx in (-lado * 0.16, 0, lado * 0.16):
        gy = cy - lado * 0.30
        c.line(cx + dx, gy, cx + dx, gy - lado * 0.08)
    c.restoreState()


_ICONES = (_icone_manuseio, _icone_este_lado_cima, _icone_manter_seco)


def _desenhar_volume(c: canvas.Canvas, y0: float, volume_atual: int, volume_total: int):
    x = MARGEM
    y = y0 + H_VOL - 11 * mm
    c.setFillColor(COR_PRETO)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x, y, f"VOL: {volume_atual:02d}/{volume_total:02d}")

    largura_util = LARGURA - 2 * MARGEM
    largura_box = largura_util / 3
    lado_icone = min(largura_box, H_VOL - 16 * mm) * 0.86
    cy = y0 + (H_VOL - 16 * mm) / 2 - 2 * mm

    c.setStrokeColor(COR_PRETO)
    c.setLineWidth(1)
    for i, desenhar in enumerate(_ICONES):
        cx = MARGEM + largura_box * i + largura_box / 2
        c.rect(cx - largura_box / 2 + 1.5 * mm, cy - lado_icone / 2, largura_box - 3 * mm, lado_icone, stroke=1, fill=0)
        desenhar(c, cx, cy, lado_icone)


def gerar_etiqueta_red_molds_pdf(
    numero_nf: str,
    orcamento: str,
    os_texto: str,
    cliente: str,
    endereco_linhas: list[str],
    qtde_volumes: int = 1,
) -> bytes:
    """Gera o PDF da etiqueta Red Molds - uma pagina por volume (VOL
    01/N, 02/N, ...), cada pagina no tamanho exato da etiqueta (104mm x
    200.5mm), pronta pra imprimir direto na Zebra termica."""
    qtde_volumes = max(1, int(qtde_volumes or 1))
    buf = BytesIO()
    # pageCompression=0: etiqueta e' um arquivo pequeno (poucas paginas, so
    # texto/vetor) - sem compressao, o texto fica legivel no PDF cru, o que
    # ajuda a depurar e permite testar o conteudo sem precisar de uma lib
    # de leitura de PDF (pypdf/pymupdf) so pra isso.
    c = canvas.Canvas(buf, pagesize=portrait((LARGURA, ALTURA)), pageCompression=0)
    c.setTitle("Etiqueta de Expedição")

    for volume_atual in range(1, qtde_volumes + 1):
        y = ALTURA
        y -= H_LOGO
        _desenhar_logo(c, y)
        y -= H_TITULO
        _desenhar_titulo(c, y)
        y -= H_NF
        _desenhar_nf(c, y, numero_nf, orcamento, os_texto)
        y -= H_CLIENTE
        _desenhar_cliente(c, y, cliente, endereco_linhas)
        y -= H_VOL
        _desenhar_volume(c, y, volume_atual, qtde_volumes)

        # Moldura externa + linhas divisorias entre as faixas.
        c.setStrokeColor(COR_PRETO)
        c.setLineWidth(1.4)
        c.rect(0, 0, LARGURA, ALTURA, stroke=1, fill=0)
        c.setLineWidth(1.1)
        y_linha = ALTURA
        for altura_faixa in (H_LOGO, H_TITULO, H_NF, H_CLIENTE):
            y_linha -= altura_faixa
            c.line(0, y_linha, LARGURA, y_linha)

        c.showPage()

    c.save()
    buf.seek(0)
    return buf.getvalue()
