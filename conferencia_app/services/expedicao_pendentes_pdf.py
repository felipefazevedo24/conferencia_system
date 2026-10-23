"""PDF dos itens que ainda faltam conferir numa ordem da Conferencia de
Expedicao (abas FAT e ST), com o desenho de cada OS anexado depois da lista.

Conferencia CEGA: a lista nunca traz a quantidade esperada - a coluna de
contagem sai em branco pra anotar a mao.

O desenho vem do ERP (ver expedicao_desenho_service). ERP fora nao impede a
impressao: a lista sai do mesmo jeito, marcando que o desenho nao veio.
"""

from __future__ import annotations

import logging
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import expedicao_desenho_service as desenho_svc
from ..tempo import agora_br

logger = logging.getLogger(__name__)

_styles = getSampleStyleSheet()
COR_PRIMARIA = colors.HexColor("#1B3B6F")
COR_TEXTO = colors.HexColor("#1C1C1C")
COR_LINHA = colors.HexColor("#D1D5DB")
COR_ZEBRA = colors.HexColor("#F8FAFC")

EM_ANEXO = "Em anexo"
SEM_DESENHO = "Sem desenho"
ERP_FORA = "ERP indisponível"
NAO_IMPRIMIVEL = "Formato não imprimível"


def _p(text, *, size=9, bold=False, color=COR_TEXTO):
    style = ParagraphStyle(
        name=f"pend{size}{bold}",
        parent=_styles["Normal"],
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size,
        textColor=color,
        leading=size + 3,
    )
    texto = str(text if text not in (None, "") else "-")
    return Paragraph(texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)


def filtrar_itens(itens, ids_txt: str | None) -> list:
    """Itens da ordem cujos ids vieram da tela (os que estao sem contagem
    nela). Id de outra ordem e' ignorado."""
    ids = {int(x) for x in str(ids_txt or "").split(",") if x.strip().isdigit()}
    return [it for it in itens if it.id in ids]


def _abrir_como_pdf(conteudo: bytes, nome: str):
    import pymupdf

    ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else "pdf"
    doc = pymupdf.open(stream=conteudo, filetype=ext)
    if doc.is_pdf:
        return doc
    # Desenho em imagem (PNG/JPG): vira uma pagina de PDF pra entrar no mesmo arquivo.
    convertido = pymupdf.open("pdf", doc.convert_to_pdf())
    doc.close()
    return convertido


def _buscar_desenhos(itens) -> tuple[dict[str, str], list]:
    """Situacao do desenho por OS + os desenhos abertos, na ordem da lista.
    Cada OS e' buscada uma vez. Na primeira falha do ERP para de tentar: as
    proximas iam esperar o mesmo timeout, uma por uma."""
    situacao: dict[str, str] = {}
    anexos = []
    erp_fora = False
    for it in itens:
        n_os = str(it.n_os or "").strip()
        if not n_os or n_os in situacao:
            continue
        if erp_fora:
            situacao[n_os] = ERP_FORA
            continue
        try:
            conteudo, nome = desenho_svc.obter_arquivo(n_os)
        except LookupError:
            situacao[n_os] = SEM_DESENHO
            continue
        except Exception:  # noqa: BLE001 - ERP fora nao derruba a impressao
            logger.warning("Desenho da OS %s indisponivel para impressao (ERP fora)", n_os, exc_info=True)
            situacao[n_os] = ERP_FORA
            erp_fora = True
            continue
        try:
            anexos.append(_abrir_como_pdf(conteudo, nome))
            situacao[n_os] = EM_ANEXO
        except Exception:  # noqa: BLE001 - DWG, arquivo corrompido etc.
            logger.warning("Desenho da OS %s (%s) nao pode ser anexado ao PDF", n_os, nome, exc_info=True)
            situacao[n_os] = NAO_IMPRIMIVEL
    return situacao, anexos


def _lista_pdf(cabecalho: list[tuple[str, str]], itens, situacao: dict[str, str], total_itens: int, usuario: str) -> bytes:
    buf = BytesIO()
    largura = 182 * mm
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title="Itens pendentes de conferência",
    )
    story = [
        _p("Itens pendentes de conferência", size=15, bold=True, color=COR_PRIMARIA),
        Spacer(1, 2 * mm),
        _p(" · ".join(f"{rotulo}: {valor}" for rotulo, valor in cabecalho if valor), size=9),
        _p(
            f"{len(itens)} de {total_itens} itens · gerado em {agora_br().strftime('%d/%m/%Y %H:%M')} por {usuario}",
            size=8, color=colors.HexColor("#6B7280"),
        ),
        Spacer(1, 5 * mm),
    ]

    linhas = [[_p(t, bold=True, size=8, color=colors.white) for t in
               ("Cód. interno", "Descrição do material", "Nº OS", "Desenho", "Contagem")]]
    for it in itens:
        n_os = str(it.n_os or "").strip()
        linhas.append([
            _p(it.cod_interno),
            _p(it.item),
            _p(n_os),
            _p(situacao.get(n_os, SEM_DESENHO) if n_os else "-", size=8),
            "",
        ])
    tabela = Table(
        linhas,
        colWidths=[28 * mm, largura - 28 * mm - 24 * mm - 30 * mm - 26 * mm, 24 * mm, 30 * mm, 26 * mm],
        repeatRows=1,
    )
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, COR_LINHA),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
    ]
    for i in range(2, len(linhas), 2):
        estilo.append(("BACKGROUND", (0, i), (-2, i), COR_ZEBRA))
    tabela.setStyle(TableStyle(estilo))
    story += [
        tabela,
        Spacer(1, 4 * mm),
        _p("Conferência cega: anote na coluna Contagem a quantidade contada de cada item.", size=8,
           color=colors.HexColor("#6B7280")),
    ]
    doc.build(story)
    return buf.getvalue()


def gerar_pdf(cabecalho: list[tuple[str, str]], itens, total_itens: int, usuario: str) -> bytes:
    """Lista dos itens + desenho de cada OS (uma vez por OS), num PDF so'."""
    import pymupdf

    situacao, anexos = _buscar_desenhos(itens)
    lista = _lista_pdf(cabecalho, itens, situacao, total_itens, usuario)
    if not anexos:
        return lista
    saida = pymupdf.open("pdf", lista)
    try:
        for anexo in anexos:
            saida.insert_pdf(anexo)
        return saida.tobytes(garbage=3, deflate=True)
    finally:
        saida.close()
        for anexo in anexos:
            anexo.close()
