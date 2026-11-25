"""Etiqueta de numero de serie - aba Faturamento da Conferencia de Expedicao.

Rolo 50 x 150 mm em pe. Os 8,5 cm de cima ficam em branco (layout do
usuario); abaixo, seis campos de 3,5 cm escritos de cima pra baixo (texto
girado 90 graus no sentido horario), lado a lado da direita pra esquerda:
Cliente, Nome do Monte, Produto, OS, Data e N. de serie. OS e data saem da
conferencia; os outros quatro sao digitados na hora de imprimir.

Uma etiqueta por OS da ordem: cliente e data valem pra todas; monte, produto
e serie sao digitados por OS (a OS marcada "nao aplicavel" nao imprime).

ZPL e' o caminho padrao (ver .claude/skills/etiqueta-zebra); o PDF desenha a
mesma etiqueta como alternativa.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .expedicao_etiqueta_zpl import DPI_PADRAO, DPIS_SUPORTADOS, _Escala, _limpar

LARGURA, ALTURA = 50.0, 150.0
TOPO_CAMPOS, COMPRIMENTO_CAMPO = 85.0, 35.0
MARGEM_ESQUERDA, MARGEM_DIREITA = 11.0, 4.0
QTDE_CAMPOS = 6
LARGURA_CAMPO = (LARGURA - MARGEM_ESQUERDA - MARGEM_DIREITA) / QTDE_CAMPOS  # ~5,8 mm
# Fonte encolhe ate caber nos 3,5 cm; abaixo do minimo o texto e' cortado.
FONTE_MAX, FONTE_MIN = 10.0, 6.0
PAPEL = "50 x 150 mm"


@dataclass(frozen=True)
class Etiqueta:
    cliente: str
    monte: str
    produto: str
    os: str
    data: str
    serie: str


def _campos(et: Etiqueta) -> list[str]:
    """Na ordem do desenho: o primeiro fica na coluna da direita. So' o
    valor, sem rotulo (pedido do usuario em 25/11/2026)."""
    return [et.cliente, et.monte, et.produto, et.os, et.data, et.serie]


def _ajustar(texto: str) -> tuple[str, float]:
    """(texto, fonte em pt) que cabe no comprimento do campo. Mede com a
    Helvetica-Bold cheia - mais larga que a fonte 0 da Zebra, sobra folga."""
    texto = _limpar(texto)
    limite = COMPRIMENTO_CAMPO * 0.97 * 72 / 25.4
    pt = FONTE_MAX
    while pt > FONTE_MIN and stringWidth(texto, "Helvetica-Bold", pt) > limite:
        pt -= 0.5
    while texto and stringWidth(texto, "Helvetica-Bold", pt) > limite:
        texto = texto[:-1]
    return texto.rstrip(), pt


def _coluna_x(indice: int) -> float:
    """Borda esquerda da coluna do campo `indice` (0 = Cliente, a direita)."""
    return LARGURA - MARGEM_DIREITA - (indice + 1) * LARGURA_CAMPO


def _altura_mm(pt: float) -> float:
    return pt * 25.4 / 72


# ── ZPL ──────────────────────────────────────────────────────────────────
def _zpl_etiqueta(e: _Escala, et: Etiqueta) -> str:
    partes = ["^XA", "^CI28", f"^PW{e.mm(LARGURA)}", f"^LL{e.mm(ALTURA)}", "^LH0,0", "^PON"]
    for i, campo in enumerate(_campos(et)):
        texto, pt = _ajustar(campo)
        # ^A0R gira 90 graus no horario: le-se de cima pra baixo, e o ^FO e'
        # o canto de cima/esquerda da caixa girada - centra na coluna.
        x = _coluna_x(i) + (LARGURA_CAMPO - _altura_mm(pt)) / 2
        altura = e.pt(pt)
        partes.append(f"^FO{e.mm(x)},{e.mm(TOPO_CAMPOS)}^A0R,{altura},{altura}^FD{texto}^FS")
    partes.append("^XZ")
    return "\n".join(partes)


def gerar_zpl(etiquetas: list[Etiqueta], dpi: int = DPI_PADRAO) -> str:
    """Um ^XA...^XZ por OS."""
    escala = _Escala(dpi if dpi in DPIS_SUPORTADOS else DPI_PADRAO)
    return "\n".join(_zpl_etiqueta(escala, et) for et in etiquetas) + "\n"


# ── PDF (alternativa sem o Browser Print) ────────────────────────────────
def gerar_pdf(etiquetas: list[Etiqueta]) -> bytes:
    """Uma pagina de 50 x 150 mm por OS."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(LARGURA * mm, ALTURA * mm), pageCompression=0)
    c.setTitle("Etiqueta de número de série")
    c.setViewerPreference("PrintScaling", "None")  # sem isso o driver reduz a etiqueta
    for et in etiquetas:
        _pdf_etiqueta(c, et)
        c.showPage()
    c.save()
    return buf.getvalue()


def _pdf_etiqueta(c: canvas.Canvas, et: Etiqueta):
    topo = (ALTURA - TOPO_CAMPOS) * mm
    for i, campo in enumerate(_campos(et)):
        texto, pt = _ajustar(campo)
        # Base da linha a ~22% da coluna (a esquerda); as letras crescem pra
        # direita depois do giro de -90 graus.
        x = _coluna_x(i) + (LARGURA_CAMPO - _altura_mm(pt) * 0.72) / 2
        c.saveState()
        c.translate(x * mm, topo)
        c.rotate(-90)
        c.setFont("Helvetica-Bold", pt)
        c.drawString(0, 0, texto)
        c.restoreState()
