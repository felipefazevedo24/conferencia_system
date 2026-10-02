"""Etiquetas de expedicao em ZPL (linguagem nativa da Zebra), pra imprimir
direto na impressora pelo Zebra Browser Print - sem PDF nem dialogo de
impressao.

Cada etiqueta leva o proprio tamanho (^PW largura / ^LL comprimento), entao
a mesma Zebra imprime Red Molds (105 x 200) e Columbia (80 x 120) sem
configurar papel no Windows - igual o ERP faz.

As medidas e fontes vem dos mesmos modelos do PDF (expedicao_etiqueta_pdf),
convertidas de mm/pt pra pontos da impressora conforme a resolucao (203 ou
300 dpi - a tela pergunta pra propria Zebra).
"""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image
from reportlab.pdfbase.pdfmetrics import stringWidth

from .expedicao_etiqueta_pdf import PICTOGRAMAS, Modelo, _PASTA_PICTOGRAMAS

DPI_PADRAO = 203
FATOR_FONTE_0 = 0.80
DPIS_SUPORTADOS = (152, 203, 300, 600)


class _Escala:
    def __init__(self, dpi: int):
        self.dpmm = dpi / 25.4
        self.dpi = dpi

    def mm(self, valor_mm: float) -> int:
        return round(valor_mm * self.dpmm)

    def pt(self, valor_pt: float) -> int:
        return round(valor_pt / 72 * self.dpi)


def _limpar(texto) -> str:
    """^ e ~ sao comandos ZPL - dentro de um campo quebrariam a etiqueta."""
    return str(texto or "").replace("^", " ").replace("~", " ").strip()


def _texto(e: _Escala, x_mm: float, y_mm: float, tamanho_pt: float, texto: str, *,
           largura_mm: float | None = None, linhas: int = 1, alinhamento: str = "L") -> str:
    """Campo de texto com a fonte escalavel 0. ^FO e' o canto de cima da
    linha; com ^FB o texto quebra sozinho em ate `linhas`."""
    altura = e.pt(tamanho_pt)
    bloco = f"^FB{e.mm(largura_mm)},{linhas},0,{alinhamento},0" if largura_mm else ""
    return f"^FO{e.mm(x_mm)},{e.mm(y_mm)}^A0N,{altura},{altura}{bloco}^FD{_limpar(texto)}^FS"


def _linha(e: _Escala, y_mm: float, largura_mm: float, espessura_pt: float) -> str:
    t = max(1, e.pt(espessura_pt))
    return f"^FO0,{e.mm(y_mm)}^GB{e.mm(largura_mm)},{t},{t}^FS"


@lru_cache(maxsize=32)
def _grafico(nome: str, lado_dots: int) -> str:
    """PNG do pictograma -> ^GFA (bitmap 1 bit, hexadecimal). Cacheado por
    tamanho: a conversao e' a parte cara e as imagens nao mudam."""
    caminho = os.path.join(_PASTA_PICTOGRAMAS, nome)
    if not os.path.isfile(caminho):
        return ""
    with Image.open(caminho) as origem:
        img = origem.convert("RGBA")
    fundo = Image.new("RGBA", img.size, (255, 255, 255, 255))
    fundo.alpha_composite(img)
    img = fundo.convert("L").resize((lado_dots, lado_dots), Image.LANCZOS)
    bytes_linha = (lado_dots + 7) // 8
    hexa = []
    pixels = img.load()
    for y in range(lado_dots):
        linha = bytearray(bytes_linha)
        for x in range(lado_dots):
            if pixels[x, y] < 128:  # escuro = ponto impresso
                linha[x // 8] |= 0x80 >> (x % 8)
        hexa.append(linha.hex().upper())
    total = bytes_linha * lado_dots
    return f"^GFA,{total},{total},{bytes_linha},{''.join(hexa)}"


def _volume(m: Modelo, e: _Escala, numero_nf, orcamento, os_texto, cliente, endereco_linhas, atual, total) -> str:
    largura = m.largura
    util = m.largura - 2 * m.margem
    y = m.h_logo  # topo da parte impressa (o logo e' pre-impresso no rolo)
    partes = [
        "^XA", "^CI28",  # UTF-8: acentos (Ç, Ã...) saem certos
        f"^PW{e.mm(largura)}", f"^LL{e.mm(m.altura)}", "^LH0,0", "^PON",
    ]

    # Moldura da parte impressa + divisorias entre as faixas.
    borda = max(2, e.pt(1.4))
    partes.append(f"^FO0,{e.mm(y)}^GB{e.mm(largura)},{e.mm(m.altura - y)},{borda}^FS")
    y_div = y
    for faixa in (m.h_titulo, m.h_nf, m.h_cliente):
        y_div += faixa
        partes.append(_linha(e, y_div, largura, 1.1))

    # Titulo centralizado na faixa.
    topo_titulo = y + (m.h_titulo - m.fonte_titulo * 25.4 / 72 * 0.72) / 2
    partes.append(_texto(e, 0, topo_titulo, m.fonte_titulo, "IDENTIFICAÇÃO DE VOLUME",
                         largura_mm=largura, alinhamento="C"))

    # NF / Orcamento (/ OS): rotulo pequeno + valor grande na mesma linha.
    def campo(y_base_mm: float, rotulo: str, valor: str):
        topo = y_base_mm - m.fonte_valor * 25.4 / 72 * 0.72
        topo_rotulo = y_base_mm - m.fonte_rotulo * 25.4 / 72 * 0.72
        # A fonte 0 da Zebra e' mais estreita que a Helvetica usada pra medir.
        largura_rotulo_mm = stringWidth(rotulo + " ", "Helvetica-Bold", m.fonte_rotulo) * 25.4 / 72 * FATOR_FONTE_0
        partes.append(_texto(e, m.margem, topo_rotulo, m.fonte_rotulo, rotulo))
        partes.append(_texto(e, m.margem + largura_rotulo_mm, topo, m.fonte_valor, valor or "—",
                             largura_mm=util - largura_rotulo_mm))

    y_nf = y + m.h_titulo
    base = y_nf + m.topo_nf
    campo(base, "NOTA FISCAL:", numero_nf)
    campo(base + m.passo_nf, "ORÇAMENTO:", orcamento)
    if m.com_os:
        campo(base + 2 * m.passo_nf, "OS:", os_texto)

    # Cliente / endereco (quebra automatica com ^FB).
    y_cli = y_nf + m.h_nf
    altura_rotulo = m.fonte_rotulo * 25.4 / 72 * 0.72
    base = y_cli + m.topo_cliente
    partes.append(_texto(e, m.margem, base - altura_rotulo, m.fonte_rotulo, "CLIENTE:"))
    base += m.passo_cliente
    altura_cliente = m.fonte_cliente * 25.4 / 72 * 0.72
    partes.append(_texto(e, m.margem, base - altura_cliente, m.fonte_cliente, cliente or "—",
                         largura_mm=util, linhas=2))
    base += 2 * m.passo_cliente + m.passo_cliente * 0.3
    partes.append(_texto(e, m.margem, base - altura_rotulo, m.fonte_rotulo, "ENDEREÇO:"))
    base += m.passo_endereco * 1.1
    altura_end = m.fonte_endereco * 25.4 / 72 * 0.72
    endereco = "\\&".join(_limpar(linha) for linha in (endereco_linhas or ["—"]))
    partes.append(
        f"^FO{e.mm(m.margem)},{e.mm(base - altura_end)}^A0N,{e.pt(m.fonte_endereco)},{e.pt(m.fonte_endereco)}"
        f"^FB{e.mm(util)},{m.linhas_endereco},{e.pt(m.passo_endereco * 72 / 25.4 - m.fonte_endereco)},L,0"
        f"^FD{endereco}^FS"
    )

    # Volume centralizado + pictogramas embaixo.
    y_vol = y_cli + m.h_cliente
    base_vol = y_vol + m.vol_topo
    partes.append(_texto(e, 0, base_vol - m.fonte_vol * 25.4 / 72 * 0.72, m.fonte_vol,
                         f"VOL: {atual:02d}/{total:02d}", largura_mm=largura, alinhamento="C"))
    lado = e.mm(m.lado_pictograma)
    caixa = util / 3
    topo_pic = m.altura - m.base_pictograma - m.lado_pictograma
    for i, nome in enumerate(PICTOGRAMAS):
        grafico = _grafico(nome, lado)
        if not grafico:
            continue
        cx = m.margem + caixa * i + caixa / 2
        partes.append(f"^FO{e.mm(cx - m.lado_pictograma / 2)},{e.mm(topo_pic)}{grafico}^FS")

    partes.append("^XZ")
    return "\n".join(partes)


def gerar_etiqueta_zpl(modelo: Modelo, numero_nf: str, orcamento: str, os_texto: str, cliente: str,
                       endereco_linhas: list[str], qtde_volumes: int = 1, dpi: int = DPI_PADRAO) -> str:
    """Um ^XA...^XZ por volume (VOL 01/N, 02/N, ...)."""
    dpi = dpi if dpi in DPIS_SUPORTADOS else DPI_PADRAO
    escala = _Escala(dpi)
    total = max(1, int(qtde_volumes or 1))
    return "\n".join(
        _volume(modelo, escala, numero_nf, orcamento, os_texto, cliente, endereco_linhas, atual, total)
        for atual in range(1, total + 1)
    ) + "\n"
