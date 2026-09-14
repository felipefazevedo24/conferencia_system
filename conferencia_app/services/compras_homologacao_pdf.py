"""Gera o F-COM-001-01 preenchido em PDF (para arquivo e auditoria).

Reproduz o formulario que Compras usava no Excel: cabecalho com codigo/
revisao, dados do fornecedor, escopo, as quatro secoes pontuadas com as
respostas e comentarios, a nota final com a classificacao, as fotos da
visita e a trilha de quem preencheu/aprovou.
"""
from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import compras_homologacao_form as form
from . import compras_homologacao_service as svc

_CINZA = colors.HexColor("#f1f5f9")
_BORDA = colors.HexColor("#cbd5e1")
_TITULO = colors.HexColor("#1e293b")
_VERDE = colors.HexColor("#15803d")
_AMBAR = colors.HexColor("#b45309")
_VERMELHO = colors.HexColor("#b91c1c")

_COR_CLASSIFICACAO = {
    form.CLASSIFICACAO_APROVADO: _VERDE,
    form.CLASSIFICACAO_RESSALVAS: _AMBAR,
    form.CLASSIFICACAO_REPROVADO: _VERMELHO,
}


def _estilos():
    base = getSampleStyleSheet()
    return {
        "titulo": ParagraphStyle("t", parent=base["Title"], fontSize=14, spaceAfter=2, textColor=_TITULO),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontSize=8, alignment=TA_CENTER, textColor=colors.grey),
        "secao": ParagraphStyle("sec", parent=base["Heading2"], fontSize=11, spaceBefore=10, spaceAfter=4, textColor=_TITULO),
        "campo": ParagraphStyle("c", parent=base["Normal"], fontSize=8.5, leading=11),
        "celula": ParagraphStyle("cel", parent=base["Normal"], fontSize=8, leading=10),
        "rodape": ParagraphStyle("r", parent=base["Normal"], fontSize=7.5, textColor=colors.grey),
    }


def _p(texto, estilo):
    return Paragraph(str(texto if texto is not None else "—"), estilo)


def _tabela_campos(pares, estilos, larguras=(35 * mm, 60 * mm, 35 * mm, 55 * mm)):
    """Grade de 2 colunas de rotulo/valor (dados do fornecedor)."""
    linhas = []
    for i in range(0, len(pares), 2):
        esq = pares[i]
        dir_ = pares[i + 1] if i + 1 < len(pares) else ("", "")
        linhas.append([
            _p(f"<b>{esq[0]}</b>", estilos["celula"]), _p(esq[1] or "—", estilos["celula"]),
            _p(f"<b>{dir_[0]}</b>" if dir_[0] else "", estilos["celula"]), _p(dir_[1] or ("" if not dir_[0] else "—"), estilos["celula"]),
        ])
    tabela = Table(linhas, colWidths=list(larguras))
    tabela.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDA),
        ("BACKGROUND", (0, 0), (0, -1), _CINZA),
        ("BACKGROUND", (2, 0), (2, -1), _CINZA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return tabela


def _tabela_secao(secao, respostas, detalhe, estilos):
    cabecalho = [
        _p("<b>#</b>", estilos["celula"]),
        _p("<b>Item</b>", estilos["celula"]),
        _p("<b>Resposta</b>", estilos["celula"]),
        _p("<b>Comentário</b>", estilos["celula"]),
    ]
    linhas = [cabecalho]
    for i, texto in enumerate(secao["itens"], start=1):
        registro = respostas.get((secao["chave"], i))
        linhas.append([
            _p(i, estilos["celula"]),
            _p(texto, estilos["celula"]),
            _p((registro.resposta if registro else None) or "—", estilos["celula"]),
            _p((registro.comentario if registro else None) or "", estilos["celula"]),
        ])

    tabela = Table(linhas, colWidths=[8 * mm, 96 * mm, 26 * mm, 55 * mm], repeatRows=1)
    tabela.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDA),
        ("BACKGROUND", (0, 0), (-1, 0), _CINZA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return tabela


def _cabecalho_secao(secao, detalhe, estilos):
    info = detalhe.get(secao["chave"], {})
    aproveitamento = info.get("aproveitamento", 0.0)
    return _p(
        f"{secao['titulo']} "
        f"<font size=8 color='#64748b'>(peso {secao['peso']:.0%} · aproveitamento {aproveitamento:.0%})</font>",
        estilos["secao"],
    )


def _fotos(homologacao, estilos):
    """Fotos da visita, 2 por linha, redimensionadas pra caber na pagina."""
    if not homologacao.fotos:
        return []
    blocos = [_p("<b>8. Fotos</b>", estilos["secao"])]
    linha = []
    for foto in homologacao.fotos:
        if not foto.dados:
            continue
        try:
            leitor = ImageReader(BytesIO(foto.dados))
            largura_orig, altura_orig = leitor.getSize()
        except Exception:
            continue
        largura = 85 * mm
        altura = largura * (altura_orig / largura_orig) if largura_orig else 60 * mm
        altura = min(altura, 70 * mm)
        largura = altura * (largura_orig / altura_orig) if altura_orig else largura
        imagem = Image(BytesIO(foto.dados), width=largura, height=altura)
        legenda = _p(foto.legenda or foto.nome_arquivo or "", estilos["rodape"])
        linha.append(Table([[imagem], [legenda]], colWidths=[largura]))
        if len(linha) == 2:
            blocos.append(Table([linha], colWidths=[92 * mm, 92 * mm]))
            blocos.append(Spacer(1, 4))
            linha = []
    if linha:
        blocos.append(Table([linha], colWidths=[92 * mm]))
    return blocos


def gerar_pdf_homologacao(homologacao) -> bytes:
    estilos = _estilos()
    respostas = {(r.secao, r.item): r for r in homologacao.respostas}
    nota, classificacao, detalhe = svc.calcular_nota(
        {chave: r.resposta for chave, r in respostas.items()}
    )

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"{form.CODIGO_FORMULARIO} - {homologacao.razao_social or ''}",
        # sem compressao: mantem o texto legivel nos bytes crus (facilita teste)
        pageCompression=0,
    )

    fluxo = [
        _p("Homologação de Fornecedores", estilos["titulo"]),
        _p(
            f"{form.CODIGO_FORMULARIO} · Emissão 03/06/2024 · Revisão 01 · "
            f"Registro #{homologacao.id}",
            estilos["sub"],
        ),
        Spacer(1, 8),
        _p("1. Dados do Fornecedor", estilos["secao"]),
        _tabela_campos([
            ("Razão Social", homologacao.razao_social),
            ("CNPJ", homologacao.cnpj),
            ("Nome Fantasia", homologacao.nome_fantasia),
            ("Inscrição Estadual", homologacao.inscricao_estadual),
            ("Endereço", homologacao.endereco),
            ("Cidade/Estado", homologacao.cidade_estado),
            ("Contato Principal", homologacao.contato_principal),
            ("Telefone", homologacao.telefone),
            ("E-mail", homologacao.email),
            ("Website", homologacao.website),
        ], estilos),
        _p("2. Escopo de Fornecimento", estilos["secao"]),
        _tabela_campos([
            ("Categoria de Compra", homologacao.categoria_compra),
            ("Descrição", homologacao.descricao_produto_servico),
        ], estilos),
    ]

    if homologacao.resultado_auditoria:
        fluxo += [
            _p("3. Resultado da Auditoria", estilos["secao"]),
            _p(homologacao.resultado_auditoria, estilos["campo"]),
        ]

    for secao in form.SECOES:
        fluxo.append(_cabecalho_secao(secao, detalhe, estilos))
        fluxo.append(_tabela_secao(secao, respostas, detalhe, estilos))
        if secao["chave"] == form.SECAO_LEGAL and homologacao.obs_conformidade_legal:
            fluxo.append(Spacer(1, 3))
            fluxo.append(_p(f"<i>Obs.: {homologacao.obs_conformidade_legal}</i>", estilos["rodape"]))

    cor = _COR_CLASSIFICACAO.get(classificacao, _TITULO)
    resultado = Table([[
        _p("<b>Nota final</b>", estilos["celula"]),
        _p(f"<b>{nota:.1%}</b>", estilos["celula"]),
        _p("<b>Classificação</b>", estilos["celula"]),
        # hexval() devolve "0xRRGGBB"; o markup do ReportLab quer "#RRGGBB".
        _p(f"<b><font color='#{cor.hexval()[2:]}'>{classificacao}</font></b>", estilos["celula"]),
    ]], colWidths=[30 * mm, 30 * mm, 35 * mm, 90 * mm])
    resultado.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, _BORDA),
        ("BACKGROUND", (0, 0), (0, -1), _CINZA),
        ("BACKGROUND", (2, 0), (2, -1), _CINZA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    fluxo += [Spacer(1, 10), resultado]

    if homologacao.comentario:
        fluxo += [_p("9. Comentário", estilos["secao"]), _p(homologacao.comentario, estilos["campo"])]

    fotos = _fotos(homologacao, estilos)
    if fotos:
        fluxo.append(PageBreak())
        fluxo += fotos

    def _dt(valor):
        return valor.strftime("%d/%m/%Y %H:%M") if valor else "—"

    trilha = [
        ("Status", homologacao.status),
        ("Preenchido por", f"{homologacao.criado_por or '—'} em {_dt(homologacao.criado_em)}"),
        ("Enviado por", f"{homologacao.enviado_por or '—'} em {_dt(homologacao.enviado_em)}"),
        ("Decidido por", f"{homologacao.decidido_por or '—'} em {_dt(homologacao.decidido_em)}"),
        ("Validade", homologacao.valido_ate.strftime("%d/%m/%Y") if homologacao.valido_ate else "—"),
    ]
    if homologacao.justificativa_decisao:
        trilha.append(("Justificativa", homologacao.justificativa_decisao))

    fluxo += [
        Spacer(1, 12),
        _p("Registro da aprovação", estilos["secao"]),
        _tabela_campos(trilha, estilos),
    ]

    doc.build(fluxo)
    return buffer.getvalue()
