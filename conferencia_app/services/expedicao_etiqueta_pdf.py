"""Geracao da Etiqueta de Expedicao ("Identificacao de Volume") em ReportLab,
pra impressora termica Zebra - PDF no tamanho exato da etiqueta (nao A4),
que abre numa aba nova e e' impresso pelo driver Windows da impressora,
mesmo fluxo ja usado pra PO/DANFE no resto do sistema.

Modelo "Red Molds" - rolo de 105 x 200 mm, em 5 faixas verticais (topo -> base):
    logo (48.5mm) / titulo (15mm) / NF-Orcamento-OS (35mm) /
    Cliente-Endereco (52mm) / Volume+pictogramas (49.5mm).

O logo "Columbia Red Molds" ja' vem pre-impresso no rolo: a faixa do topo
fica em branco. Os pictogramas (manuseio, este lado pra cima, mantenha seco)
sao as imagens de static/img/etiqueta_red_molds/.
"""
from __future__ import annotations

import os
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import portrait
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

COR_PRETO = colors.HexColor("#000000")

# ── Dimensoes do modelo Red Molds (mm) ──────────────────────────────────
LARGURA = 105 * mm
H_LOGO = 48.5 * mm
H_TITULO = 15 * mm
H_NF = 35 * mm
H_CLIENTE = 52 * mm
H_VOL = 49.5 * mm
ALTURA = H_LOGO + H_TITULO + H_NF + H_CLIENTE + H_VOL  # 200mm

MARGEM = 4 * mm

_PASTA_PICTOGRAMAS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "static", "img", "etiqueta_red_molds",
)
PICTOGRAMAS = ("manuseio.png", "este_lado_para_cima.png", "manter_seco.png")


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


def _desenhar_titulo(c: canvas.Canvas, y0: float):
    c.setFillColor(COR_PRETO)
    c.setFont("Helvetica-Bold", 17)
    c.drawCentredString(LARGURA / 2, y0 + H_TITULO / 2 - 6, "IDENTIFICAÇÃO DE VOLUME")


def _campo(c: canvas.Canvas, x: float, y: float, rotulo: str, valor: str, tamanho_rotulo=12, tamanho_valor=16):
    c.setFont("Helvetica-Bold", tamanho_rotulo)
    c.setFillColor(COR_PRETO)
    c.drawString(x, y, rotulo)
    largura_rotulo = c.stringWidth(rotulo + " ", "Helvetica-Bold", tamanho_rotulo)
    c.setFont("Helvetica-Bold", tamanho_valor)
    largura_max = LARGURA - MARGEM - x - largura_rotulo
    linhas = _quebrar_texto(c, valor or "", "Helvetica-Bold", tamanho_valor, largura_max)
    c.drawString(x + largura_rotulo, y, linhas[0] if linhas else "")


def _desenhar_nf(c: canvas.Canvas, y0: float, numero_nf: str, orcamento: str, os_texto: str):
    x = MARGEM
    passo = 10.5 * mm
    y = y0 + H_NF - 9.5 * mm
    _campo(c, x, y, "NOTA FISCAL:", numero_nf or "—")
    y -= passo
    _campo(c, x, y, "ORÇAMENTO:", orcamento or "—")
    y -= passo
    _campo(c, x, y, "OS:", os_texto or "—")


def _desenhar_cliente(c: canvas.Canvas, y0: float, cliente: str, endereco_linhas: list[str]):
    x = MARGEM
    largura_max = LARGURA - 2 * MARGEM
    y = y0 + H_CLIENTE - 8 * mm

    c.setFillColor(COR_PRETO)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "CLIENTE:")
    y -= 6.5 * mm

    c.setFont("Helvetica-Bold", 15)
    for linha in _quebrar_texto(c, cliente or "—", "Helvetica-Bold", 15, largura_max)[:2]:
        c.drawString(x, y, linha)
        y -= 6.5 * mm

    y -= 2 * mm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x, y, "ENDEREÇO:")
    y -= 6 * mm

    c.setFont("Helvetica", 12)
    sublinhas = [sub for linha in (endereco_linhas or ["—"]) for sub in _quebrar_texto(c, linha, "Helvetica", 12, largura_max)]
    for sub in sublinhas[:3]:
        c.drawString(x, y, sub)
        y -= 5.5 * mm


def _desenhar_volume(c: canvas.Canvas, y0: float, volume_atual: int, volume_total: int):
    c.setFillColor(COR_PRETO)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(LARGURA / 2, y0 + H_VOL - 11 * mm, f"VOL: {volume_atual:02d}/{volume_total:02d}")

    largura_box = (LARGURA - 2 * MARGEM) / 3
    lado = min(largura_box - 4 * mm, H_VOL - 20 * mm)
    base_y = y0 + 4 * mm
    for i, nome in enumerate(PICTOGRAMAS):
        caminho = os.path.join(_PASTA_PICTOGRAMAS, nome)
        if not os.path.isfile(caminho):
            continue
        cx = MARGEM + largura_box * i + largura_box / 2
        c.drawImage(ImageReader(caminho), cx - lado / 2, base_y, lado, lado, mask="auto", preserveAspectRatio=True)


def gerar_etiqueta_red_molds_pdf(
    numero_nf: str,
    orcamento: str,
    os_texto: str,
    cliente: str,
    endereco_linhas: list[str],
    qtde_volumes: int = 1,
) -> bytes:
    """Gera o PDF da etiqueta Red Molds - uma pagina por volume (VOL
    01/N, 02/N, ...), cada pagina no tamanho exato do rolo (105mm x
    200mm), pronta pra imprimir direto na Zebra termica."""
    qtde_volumes = max(1, int(qtde_volumes or 1))
    buf = BytesIO()
    # pageCompression=0: etiqueta e' um arquivo pequeno (poucas paginas, so
    # texto/vetor) - sem compressao, o texto fica legivel no PDF cru, o que
    # ajuda a depurar e permite testar o conteudo sem precisar de uma lib
    # de leitura de PDF (pypdf/pymupdf) so pra isso.
    c = canvas.Canvas(buf, pagesize=portrait((LARGURA, ALTURA)), pageCompression=0)
    c.setTitle("Etiqueta de Expedição")
    # Pede ao visualizador (Chrome, Edge, Acrobat) pra imprimir em tamanho
    # real: sem isso o dialogo "ajusta a pagina" ao papel padrao do driver da
    # Zebra e a etiqueta sai reduzida.
    c.setViewerPreference("PrintScaling", "None")

    for volume_atual in range(1, qtde_volumes + 1):
        # Faixa do logo em branco: o logo vem pre-impresso no rolo.
        y = ALTURA - H_LOGO
        y -= H_TITULO
        _desenhar_titulo(c, y)
        y -= H_NF
        _desenhar_nf(c, y, numero_nf, orcamento, os_texto)
        y -= H_CLIENTE
        _desenhar_cliente(c, y, cliente, endereco_linhas)
        y -= H_VOL
        _desenhar_volume(c, y, volume_atual, qtde_volumes)

        # Moldura so' da parte impressa (abaixo do logo) + divisorias.
        c.setStrokeColor(COR_PRETO)
        c.setLineWidth(1.4)
        c.rect(0, 0, LARGURA, ALTURA - H_LOGO, stroke=1, fill=0)
        c.setLineWidth(1.1)
        y_linha = ALTURA - H_LOGO
        for altura_faixa in (H_TITULO, H_NF, H_CLIENTE):
            y_linha -= altura_faixa
            c.line(0, y_linha, LARGURA, y_linha)

        c.showPage()

    c.save()
    buf.seek(0)
    return buf.getvalue()
