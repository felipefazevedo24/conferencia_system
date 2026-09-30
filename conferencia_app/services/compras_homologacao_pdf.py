"""Gera a homologacao preenchida em PDF (para arquivo e auditoria), no
formulario em que ela foi feita: F-COM-001-01 (antigas) ou F 066 rev. 04.

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
from . import compras_homologacao_form_rev04 as form_r04
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
    form_r04.CLASSIFICACAO_QUALIFICADO: _VERDE,
    form_r04.CLASSIFICACAO_PLANO_ACAO: _AMBAR,
    form_r04.CLASSIFICACAO_DESQUALIFICADO: _VERMELHO,
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


def _resposta_pdf(secao, registro, com_evidencia) -> str:
    """Resposta como gravada; no rev. 04 avisa quando o "Atende" sem anexo
    valeu como Parcial na nota."""
    resposta = (registro.resposta if registro else None) or "—"
    if registro and secao.get("evidencia") and registro.resposta:
        efetiva = form_r04.resposta_efetiva(secao, registro.resposta, (secao["chave"], registro.item) in com_evidencia)
        if efetiva != registro.resposta:
            resposta += f" <font size=6.5 color='#b45309'>(vale {efetiva}: sem evidência)</font>"
    return resposta


def _tabela_secao(secao, respostas, detalhe, estilos, com_evidencia=frozenset()):
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
            _p(_resposta_pdf(secao, registro, com_evidencia), estilos["celula"]),
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
    peso = f"peso {secao['peso']:.0%} · " if secao.get("peso") is not None else ""
    return _p(
        f"{secao['titulo']} "
        f"<font size=8 color='#64748b'>({peso}aproveitamento {aproveitamento:.0%})</font>",
        estilos["secao"],
    )


def _blocos_rev04(homologacao, estilos) -> list:
    """Quadros do F 066 rev. 04 que o formulario antigo nao tinha."""
    por_setor = {r.setor: r for r in homologacao.responsaveis}
    linhas = [[_p(f"<b>{t}</b>", estilos["celula"]) for t in ("Setor/Processo", "Nome", "Cargo", "Telefone", "E-mail")]]
    for setor in form_r04.SETORES_RESPONSAVEIS:
        r = por_setor.get(setor)
        linhas.append([_p(setor, estilos["celula"])] + [
            _p(getattr(r, campo, None) or "", estilos["celula"]) for campo in ("nome", "cargo", "telefone", "email")
        ])
    responsaveis = Table(linhas, colWidths=[35 * mm, 45 * mm, 30 * mm, 30 * mm, 45 * mm])
    responsaveis.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, _BORDA),
        ("BACKGROUND", (0, 0), (-1, 0), _CINZA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    iso = homologacao.iso9001_certificado
    validade = homologacao.iso9001_validade.strftime("%d/%m/%Y") if homologacao.iso9001_validade else "—"
    return [
        _p("Responsáveis do Fornecedor", estilos["secao"]),
        responsaveis,
        _p("Sistema de Gestão da Qualidade Certificado ISO 9001", estilos["secao"]),
        _tabela_campos([
            ("ISO 9001 vigente", "Sim" if iso else ("Não" if iso is False else "—")),
            ("Validade do certificado", validade if iso else "—"),
            ("Certificado anexado", "Sim" if svc.iso_valido(homologacao) else "Não"),
            ("Questionário", "Dispensado (ISO 9001)" if svc.iso_valido(homologacao) else "Respondido"),
        ], estilos),
    ]


def _blocos_uso_columbia(homologacao, estilos) -> list:
    return [
        _p("Para uso exclusivo da Columbia", estilos["secao"]),
        _tabela_campos([
            ("Aprovação por amostra?", homologacao.amostra_necessaria),
            ("Obs.", homologacao.amostra_obs),
            ("Visita técnica?", homologacao.visita_necessaria),
            ("Obs.", homologacao.visita_obs),
        ], estilos),
        _p("<b>Resultado da visita técnica</b>", estilos["campo"]),
        _p(homologacao.resultado_auditoria or "—", estilos["campo"]),
        Spacer(1, 4),
        _p(
            "Metodologia: Pontos obtidos / Pontos possíveis × 100 (Atende = 1 · Atende Parcial = 0,5 · "
            "Não Atende = 0; Não Aplicável fora da conta). " + form_r04.TEXTO_FAIXAS + ".",
            estilos["rodape"],
        ),
    ]


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
    nota, classificacao, detalhe = svc.calcular_nota_da(homologacao)
    formulario = svc.formulario_da(homologacao)
    rev04 = formulario is form_r04
    com_evidencia = {(e.secao, e.item) for e in homologacao.evidencias}

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
        title=f"{formulario.CODIGO_FORMULARIO} - {homologacao.razao_social or ''}",
        # sem compressao: mantem o texto legivel nos bytes crus (facilita teste)
        pageCompression=0,
    )

    fluxo = [
        _p("Avaliação de Fornecedor" if rev04 else "Homologação de Fornecedores", estilos["titulo"]),
        _p(
            (f"{form_r04.CODIGO_FORMULARIO} · Revisão {form_r04.REVISAO} · " if rev04
             else f"{form.CODIGO_FORMULARIO} · Emissão 03/06/2024 · Revisão 01 · ")
            + f"Registro #{homologacao.id}",
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

    if rev04:
        fluxo += _blocos_rev04(homologacao, estilos)
    elif homologacao.resultado_auditoria:
        fluxo += [
            _p("3. Resultado da Auditoria", estilos["secao"]),
            _p(homologacao.resultado_auditoria, estilos["campo"]),
        ]

    for secao in formulario.SECOES:
        fluxo.append(_cabecalho_secao(secao, detalhe, estilos))
        if rev04 and secao["pula_com_iso"] and svc.iso_valido(homologacao):
            fluxo.append(_p("Dispensado: fornecedor com ISO 9001 vigente (certificado anexado).", estilos["campo"]))
            continue
        fluxo.append(_tabela_secao(secao, respostas, detalhe, estilos, com_evidencia))
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
    if rev04:
        fluxo += _blocos_uso_columbia(homologacao, estilos)

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
