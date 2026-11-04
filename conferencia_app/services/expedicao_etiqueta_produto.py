"""Etiqueta de Identificacao do Produto - aba Servico de Terceiro (ST) da
Conferencia de Expedicao. Uma etiqueta por PECA conferida: item com 4 pecas
contadas sai com 4 etiquetas (PC 1/4 ... 4/4), pra cada peca ir identificada
ao terceiro.

Rolo 70 x 50 mm (deitado, sem logo pre-impresso), tres faixas:
    OS (grande) + contador da peca / descricao (ate 4 linhas) /
    codigo do item, ordem de compra e fornecedor.

ZPL e' o caminho padrao (ver .claude/skills/etiqueta-zebra); o PDF desenha a
mesma etiqueta como alternativa.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .expedicao_etiqueta_zpl import DPI_PADRAO, DPIS_SUPORTADOS, FATOR_FONTE_0, _Escala, _limpar, _texto

LARGURA, ALTURA, MARGEM = 70.0, 50.0, 2.5
UTIL = LARGURA - 2 * MARGEM
H_OS, H_DESCRICAO = 11.0, 20.0  # a faixa de baixo fica com o resto (19 mm)
FONTE_OS, FONTE_PECA, FONTE_ROTULO = 20, 13, 6.5
FONTE_DESCRICAO, LINHAS_DESCRICAO, PASSO_DESCRICAO = 8.5, 4, 3.6
FONTE_CAMPO, PASSO_CAMPO = 8, 5.4
ESPESSURA_BORDA, ESPESSURA_LINHA = 1.4, 1.1
PAPEL = "70 x 50 mm"


@dataclass(frozen=True)
class Etiqueta:
    os: str
    descricao: str
    codigo: str
    ordem_compra: str
    fornecedor: str
    peca: int
    total: int


def _altura_mm(pt: float) -> float:
    """Altura das maiusculas (~72% do corpo), pra centrar/alinhar pela base."""
    return pt * 25.4 / 72 * 0.72


def _campos(et: Etiqueta) -> list[tuple[str, str]]:
    return [("CÓDIGO:", et.codigo), ("OC:", et.ordem_compra), ("FORNECEDOR:", et.fornecedor)]


def _largura_rotulo_mm(rotulo: str, pt: float) -> float:
    return stringWidth(rotulo + " ", "Helvetica-Bold", pt) * 25.4 / 72


def _linhas_zpl(texto: str, pt: float, largura_mm: float, linhas: int) -> str:
    """Quebra/corta aqui em vez de deixar pro ^FB: o que passa das linhas do
    ^FB a Zebra imprime POR CIMA da ultima linha (vira borrao). Mede com a
    Helvetica-Bold cheia (sem o FATOR_FONTE_0): com o fator, texto com muito
    numero/pontuacao estourou a linha no Labelary."""
    largura_pt = largura_mm * 0.97 * 72 / 25.4
    partes = simpleSplit(_limpar(texto) or "—", "Helvetica-Bold", pt, largura_pt)[:linhas]
    return "\\&".join(partes)


# ── ZPL ──────────────────────────────────────────────────────────────────
def _zpl_etiqueta(e: _Escala, et: Etiqueta) -> str:
    partes = ["^XA", "^CI28", f"^PW{e.mm(LARGURA)}", f"^LL{e.mm(ALTURA)}", "^LH0,0", "^PON"]
    borda, linha = max(2, e.pt(ESPESSURA_BORDA)), max(1, e.pt(ESPESSURA_LINHA))
    partes.append(f"^FO0,0^GB{e.mm(LARGURA)},{e.mm(ALTURA)},{borda}^FS")
    for y in (H_OS, H_OS + H_DESCRICAO):
        partes.append(f"^FO0,{e.mm(y)}^GB{e.mm(LARGURA)},{linha},{linha}^FS")

    # Faixa 1: "OS" pequeno + numero grande; contador da peca a direita.
    base = (H_OS + _altura_mm(FONTE_OS)) / 2
    rotulo_os = _largura_rotulo_mm("OS", FONTE_ROTULO + 3) * FATOR_FONTE_0
    partes.append(_texto(e, MARGEM, base - _altura_mm(FONTE_ROTULO + 3), FONTE_ROTULO + 3, "OS"))
    partes.append(_texto(e, MARGEM + rotulo_os, base - _altura_mm(FONTE_OS), FONTE_OS, et.os or "—",
                         largura_mm=UTIL * 0.62 - rotulo_os))
    partes.append(_texto(e, MARGEM, base - _altura_mm(FONTE_PECA), FONTE_PECA, f"PÇ {et.peca}/{et.total}",
                         largura_mm=UTIL, alinhamento="R"))

    # Faixa 2: descricao com quebra automatica (^FB) em ate 4 linhas.
    y = H_OS + 1.2
    partes.append(_texto(e, MARGEM, y, FONTE_ROTULO, "DESCRIÇÃO:"))
    y += _altura_mm(FONTE_ROTULO) + 1.3
    espaco = e.mm(PASSO_DESCRICAO) - e.pt(FONTE_DESCRICAO)
    partes.append(
        f"^FO{e.mm(MARGEM)},{e.mm(y)}^A0N,{e.pt(FONTE_DESCRICAO)},{e.pt(FONTE_DESCRICAO)}"
        f"^FB{e.mm(UTIL)},{LINHAS_DESCRICAO},{espaco},L,0"
        f"^FD{_linhas_zpl(et.descricao, FONTE_DESCRICAO, UTIL, LINHAS_DESCRICAO)}^FS"
    )

    # Faixa 3: rotulo + valor, um por linha (valor longo e' cortado em 1 linha).
    base = H_OS + H_DESCRICAO + 1.6 + _altura_mm(FONTE_CAMPO)
    for rotulo, valor in _campos(et):
        largura_rotulo = _largura_rotulo_mm(rotulo, FONTE_ROTULO) * FATOR_FONTE_0
        partes.append(_texto(e, MARGEM, base - _altura_mm(FONTE_ROTULO), FONTE_ROTULO, rotulo))
        partes.append(_texto(e, MARGEM + largura_rotulo, base - _altura_mm(FONTE_CAMPO), FONTE_CAMPO,
                             _linhas_zpl(valor, FONTE_CAMPO, UTIL - largura_rotulo, 1)))
        base += PASSO_CAMPO

    partes.append("^XZ")
    return "\n".join(partes)


def gerar_zpl(etiquetas: list[Etiqueta], dpi: int = DPI_PADRAO) -> str:
    """Um ^XA...^XZ por peca."""
    escala = _Escala(dpi if dpi in DPIS_SUPORTADOS else DPI_PADRAO)
    return "\n".join(_zpl_etiqueta(escala, et) for et in etiquetas) + "\n"


# ── PDF (alternativa sem o Browser Print) ────────────────────────────────
def _pdf_etiqueta(c: canvas.Canvas, et: Etiqueta):
    topo = ALTURA * mm
    c.setLineWidth(ESPESSURA_BORDA)
    c.rect(0, 0, LARGURA * mm, ALTURA * mm, stroke=1, fill=0)
    c.setLineWidth(ESPESSURA_LINHA)
    for y in (H_OS, H_OS + H_DESCRICAO):
        c.line(0, topo - y * mm, LARGURA * mm, topo - y * mm)

    base = topo - (H_OS + _altura_mm(FONTE_OS)) / 2 * mm
    c.setFont("Helvetica-Bold", FONTE_ROTULO + 3)
    c.drawString(MARGEM * mm, base, "OS")
    c.setFont("Helvetica-Bold", FONTE_OS)
    c.drawString((MARGEM + _largura_rotulo_mm("OS", FONTE_ROTULO + 3)) * mm, base, et.os or "—")
    c.setFont("Helvetica-Bold", FONTE_PECA)
    c.drawRightString((LARGURA - MARGEM) * mm, base, f"PÇ {et.peca}/{et.total}")

    y = H_OS + 1.2 + _altura_mm(FONTE_ROTULO)
    c.setFont("Helvetica-Bold", FONTE_ROTULO)
    c.drawString(MARGEM * mm, topo - y * mm, "DESCRIÇÃO:")
    y += 1.3 + _altura_mm(FONTE_DESCRICAO)
    c.setFont("Helvetica", FONTE_DESCRICAO)
    for linha in simpleSplit(et.descricao or "—", "Helvetica", FONTE_DESCRICAO, UTIL * mm)[:LINHAS_DESCRICAO]:
        c.drawString(MARGEM * mm, topo - y * mm, linha)
        y += PASSO_DESCRICAO

    base = H_OS + H_DESCRICAO + 1.6 + _altura_mm(FONTE_CAMPO)
    for rotulo, valor in _campos(et):
        largura_rotulo = _largura_rotulo_mm(rotulo, FONTE_ROTULO)
        c.setFont("Helvetica-Bold", FONTE_ROTULO)
        c.drawString(MARGEM * mm, topo - base * mm, rotulo)
        c.setFont("Helvetica-Bold", FONTE_CAMPO)
        texto = (simpleSplit(valor or "—", "Helvetica-Bold", FONTE_CAMPO, (UTIL - largura_rotulo) * mm) or ["—"])[0]
        c.drawString((MARGEM + largura_rotulo) * mm, topo - base * mm, texto)
        base += PASSO_CAMPO


def gerar_pdf(etiquetas: list[Etiqueta]) -> bytes:
    """Uma pagina de 70 x 50 mm por peca."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(LARGURA * mm, ALTURA * mm), pageCompression=0)
    c.setTitle("Etiqueta de Identificação do Produto")
    c.setViewerPreference("PrintScaling", "None")  # sem isso o driver reduz a etiqueta
    for et in etiquetas:
        _pdf_etiqueta(c, et)
        c.showPage()
    c.save()
    return buf.getvalue()
