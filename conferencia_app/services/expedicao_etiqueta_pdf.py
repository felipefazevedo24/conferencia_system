"""Geracao das Etiquetas de Expedicao ("Identificacao de Volume") em ReportLab,
pra impressora termica Zebra - PDF no tamanho exato da etiqueta (nao A4),
que abre numa aba nova e e' impresso pelo driver Windows da impressora,
mesmo fluxo ja usado pra PO/DANFE no resto do sistema.

Dois modelos, mesmo desenho em 5 faixas verticais (topo -> base): logo
(pre-impresso no rolo, fica em branco) / titulo / NF-Orcamento(-OS) /
Cliente-Endereco / Volume+pictogramas.
    Red Molds - rolo 105 x 200 mm, com OS.
    Columbia  - rolo 80 x 120 mm, sem OS.

Os pictogramas (manuseio, este lado pra cima, mantenha seco) sao as imagens
de static/img/etiqueta_red_molds/.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import portrait
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

COR_PRETO = colors.HexColor("#000000")

_PASTA_PICTOGRAMAS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "static", "img", "etiqueta_red_molds",
)
PICTOGRAMAS = ("manuseio.png", "este_lado_para_cima.png", "manter_seco.png")


@dataclass(frozen=True)
class Modelo:
    """Medidas (mm) e fontes (pt) de um modelo de etiqueta."""
    largura: float
    h_logo: float
    h_titulo: float
    h_nf: float
    h_cliente: float
    h_vol: float
    margem: float
    com_os: bool
    fonte_titulo: float
    fonte_rotulo: float
    fonte_valor: float
    passo_nf: float
    topo_nf: float
    fonte_cliente: float
    passo_cliente: float
    fonte_endereco: float
    passo_endereco: float
    linhas_endereco: int
    topo_cliente: float
    fonte_vol: float
    vol_topo: float
    lado_pictograma: float
    base_pictograma: float

    @property
    def altura(self) -> float:
        return self.h_logo + self.h_titulo + self.h_nf + self.h_cliente + self.h_vol


RED_MOLDS = Modelo(
    largura=105, h_logo=48.5, h_titulo=15, h_nf=35, h_cliente=52, h_vol=49.5, margem=4, com_os=True,
    fonte_titulo=17, fonte_rotulo=12, fonte_valor=16, passo_nf=10.5, topo_nf=9.5,
    fonte_cliente=15, passo_cliente=6.5, fonte_endereco=12, passo_endereco=5.5, linhas_endereco=3, topo_cliente=8,
    fonte_vol=22, vol_topo=11, lado_pictograma=28.33, base_pictograma=4,
)

# 17 mm de faixa de volume: o VOL fica em cima e os pictogramas pequenos embaixo.
COLUMBIA = Modelo(
    largura=80, h_logo=40, h_titulo=7, h_nf=24, h_cliente=32, h_vol=17, margem=3, com_os=False,
    fonte_titulo=12, fonte_rotulo=10, fonte_valor=14, passo_nf=9, topo_nf=7.5,
    fonte_cliente=11, passo_cliente=4.2, fonte_endereco=8.5, passo_endereco=3.5, linhas_endereco=3, topo_cliente=5,
    fonte_vol=12, vol_topo=5, lado_pictograma=9.5, base_pictograma=1.2,
)


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


def _desenhar_titulo(c: canvas.Canvas, m: Modelo, y0: float):
    c.setFillColor(COR_PRETO)
    c.setFont("Helvetica-Bold", m.fonte_titulo)
    # Centraliza verticalmente pela altura da fonte (cap height ~0.7 do corpo).
    y = y0 + m.h_titulo * mm / 2 - m.fonte_titulo * 0.35
    c.drawCentredString(m.largura * mm / 2, y, "IDENTIFICAÇÃO DE VOLUME")


def _campo(c: canvas.Canvas, m: Modelo, y: float, rotulo: str, valor: str):
    x = m.margem * mm
    c.setFont("Helvetica-Bold", m.fonte_rotulo)
    c.setFillColor(COR_PRETO)
    c.drawString(x, y, rotulo)
    largura_rotulo = c.stringWidth(rotulo + " ", "Helvetica-Bold", m.fonte_rotulo)
    c.setFont("Helvetica-Bold", m.fonte_valor)
    largura_max = m.largura * mm - m.margem * mm - x - largura_rotulo
    linhas = _quebrar_texto(c, valor or "", "Helvetica-Bold", m.fonte_valor, largura_max)
    c.drawString(x + largura_rotulo, y, linhas[0] if linhas else "")


def _desenhar_nf(c: canvas.Canvas, m: Modelo, y0: float, numero_nf: str, orcamento: str, os_texto: str):
    y = y0 + (m.h_nf - m.topo_nf) * mm
    _campo(c, m, y, "NOTA FISCAL:", numero_nf or "—")
    y -= m.passo_nf * mm
    _campo(c, m, y, "ORÇAMENTO:", orcamento or "—")
    if m.com_os:
        y -= m.passo_nf * mm
        _campo(c, m, y, "OS:", os_texto or "—")


def _desenhar_cliente(c: canvas.Canvas, m: Modelo, y0: float, cliente: str, endereco_linhas: list[str]):
    x = m.margem * mm
    largura_max = (m.largura - 2 * m.margem) * mm
    y = y0 + (m.h_cliente - m.topo_cliente) * mm

    c.setFillColor(COR_PRETO)
    c.setFont("Helvetica-Bold", m.fonte_rotulo)
    c.drawString(x, y, "CLIENTE:")
    y -= m.passo_cliente * mm

    c.setFont("Helvetica-Bold", m.fonte_cliente)
    for linha in _quebrar_texto(c, cliente or "—", "Helvetica-Bold", m.fonte_cliente, largura_max)[:2]:
        c.drawString(x, y, linha)
        y -= m.passo_cliente * mm

    y -= m.passo_cliente * 0.3 * mm
    c.setFont("Helvetica-Bold", m.fonte_rotulo)
    c.drawString(x, y, "ENDEREÇO:")
    y -= m.passo_endereco * 1.1 * mm

    c.setFont("Helvetica", m.fonte_endereco)
    sublinhas = [
        sub for linha in (endereco_linhas or ["—"])
        for sub in _quebrar_texto(c, linha, "Helvetica", m.fonte_endereco, largura_max)
    ]
    for sub in sublinhas[:m.linhas_endereco]:
        c.drawString(x, y, sub)
        y -= m.passo_endereco * mm


def _desenhar_volume(c: canvas.Canvas, m: Modelo, y0: float, volume_atual: int, volume_total: int):
    c.setFillColor(COR_PRETO)
    c.setFont("Helvetica-Bold", m.fonte_vol)
    c.drawCentredString(m.largura * mm / 2, y0 + (m.h_vol - m.vol_topo) * mm, f"VOL: {volume_atual:02d}/{volume_total:02d}")

    largura_box = (m.largura - 2 * m.margem) * mm / 3
    lado = m.lado_pictograma * mm
    for i, nome in enumerate(PICTOGRAMAS):
        caminho = os.path.join(_PASTA_PICTOGRAMAS, nome)
        if not os.path.isfile(caminho):
            continue
        cx = m.margem * mm + largura_box * i + largura_box / 2
        c.drawImage(ImageReader(caminho), cx - lado / 2, y0 + m.base_pictograma * mm, lado, lado,
                    mask="auto", preserveAspectRatio=True)


def gerar_etiqueta_pdf(
    modelo: Modelo,
    numero_nf: str,
    orcamento: str,
    os_texto: str,
    cliente: str,
    endereco_linhas: list[str],
    qtde_volumes: int = 1,
) -> bytes:
    """Uma pagina por volume (VOL 01/N, 02/N, ...), cada pagina no tamanho
    exato do rolo do modelo, pronta pra imprimir direto na Zebra termica."""
    m = modelo
    largura, altura = m.largura * mm, m.altura * mm
    qtde_volumes = max(1, int(qtde_volumes or 1))
    buf = BytesIO()
    # pageCompression=0: etiqueta e' um arquivo pequeno (poucas paginas, so
    # texto/vetor) - sem compressao, o texto fica legivel no PDF cru, o que
    # ajuda a depurar e permite testar o conteudo sem precisar de uma lib
    # de leitura de PDF (pypdf/pymupdf) so pra isso.
    c = canvas.Canvas(buf, pagesize=portrait((largura, altura)), pageCompression=0)
    c.setTitle("Etiqueta de Expedição")
    # Pede ao visualizador (Chrome, Edge, Acrobat) pra imprimir em tamanho
    # real: sem isso o dialogo "ajusta a pagina" ao papel padrao do driver da
    # Zebra e a etiqueta sai reduzida.
    c.setViewerPreference("PrintScaling", "None")

    for volume_atual in range(1, qtde_volumes + 1):
        # Faixa do logo em branco: o logo vem pre-impresso no rolo.
        y = altura - m.h_logo * mm
        y -= m.h_titulo * mm
        _desenhar_titulo(c, m, y)
        y -= m.h_nf * mm
        _desenhar_nf(c, m, y, numero_nf, orcamento, os_texto)
        y -= m.h_cliente * mm
        _desenhar_cliente(c, m, y, cliente, endereco_linhas)
        y -= m.h_vol * mm
        _desenhar_volume(c, m, y, volume_atual, qtde_volumes)

        # Moldura so' da parte impressa (abaixo do logo) + divisorias.
        c.setStrokeColor(COR_PRETO)
        c.setLineWidth(1.4)
        c.rect(0, 0, largura, altura - m.h_logo * mm, stroke=1, fill=0)
        c.setLineWidth(1.1)
        y_linha = altura - m.h_logo * mm
        for altura_faixa in (m.h_titulo, m.h_nf, m.h_cliente):
            y_linha -= altura_faixa * mm
            c.line(0, y_linha, largura, y_linha)

        c.showPage()

    c.save()
    buf.seek(0)
    return buf.getvalue()

