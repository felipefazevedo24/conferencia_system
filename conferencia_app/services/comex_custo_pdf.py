"""PDF do Simulador de custo de importacao (Comex): o que foi digitado
(cambio, itens, parametros do despachante e do courier) e o resultado da
comparacao (resumo, composicao do desembolso e custo por item).

Recebe o mesmo dict que `comex_custo_service.comparar` devolve - o PDF e'
recalculado no servidor, nunca copiado da tela, pra sair igual a simulacao.
"""
from __future__ import annotations

from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .comex_custo_service import ICMS_COURIER_PADRAO, II_COURIER_PADRAO, formatar_ncm
from ..tempo import agora_br

_styles = getSampleStyleSheet()
COR_PRIMARIA = colors.HexColor("#1B3B6F")
COR_TEXTO = colors.HexColor("#1C1C1C")
COR_SUAVE = colors.HexColor("#64748B")
COR_LINHA = colors.HexColor("#D1D5DB")
COR_SUB = colors.HexColor("#F1F5F9")
COR_VENCEDOR = colors.HexColor("#DCFCE7")
COR_AVISO = colors.HexColor("#FFFBEB")

# Mesmos rotulos da tela (templates/comex_simulador_custo.html).
CAMPOS_DESPACHANTE = [
    ("frete_usd", "Frete (US$)"), ("seguro_usd", "Seguro (US$)"),
    ("acrescimos_brl", "Outros acréscimos (R$)"), ("taxa_siscomex_brl", "Taxa Siscomex (R$)"),
    ("afrmm_brl", "AFRMM (R$)"), ("despesas_desembaraco_brl", "Despesas de desembaraço (R$)"),
]
CAMPOS_COURIER = [
    ("frete_usd", "Frete (US$)"), ("seguro_usd", "Seguro (US$)"),
    ("servico_courier_brl", "Serviço courier (R$)"),
    ("tarifa_aeroportuaria_brl", "Tarifa aeroportuária / armazenagem (R$)"),
    ("multas_brl", "Multas (R$)"), ("ii_courier_pct", "II do courier (%)"),
    ("icms_courier_pct", "ICMS do courier (%)"),
]
LINHAS_COMPOSICAO = [
    ("fob", "Valor FOB", None), ("frete", "Frete", None), ("seguro", "Seguro", None),
    ("acrescimos", "Outros acréscimos", None), ("cif", "Valor CIF (aduaneiro)", "sub"),
    ("ii", "II", None), ("ipi", "IPI", None), ("pis", "PIS", None), ("cofins", "COFINS", None),
    ("icms", "ICMS", None), ("impostos", "Total de impostos", "sub"),
    ("taxas", "Taxas (Siscomex + AFRMM | multas courier)", None),
    ("despesas", "Despesas (desembaraço | serviço courier + tarifa)", None),
    ("total", "Desembolso total", "total"),
]


def _p(texto, *, size=8.5, bold=False, color=COR_TEXTO, align=0):
    style = ParagraphStyle(
        name=f"sc{size}{bold}{align}", parent=_styles["Normal"],
        fontName="Helvetica-Bold" if bold else "Helvetica",
        fontSize=size, textColor=color, leading=size + 2.5, alignment=align,
    )
    texto = str(texto if texto not in (None, "") else "—")
    return Paragraph(texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)


def _num_br(valor, casas=2) -> str:
    s = f"{float(valor or 0):,.{casas}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def _brl(valor) -> str:
    return "R$ " + _num_br(valor)


def _digitado(valor) -> str:
    """Valor como a pessoa digitou (o PDF mostra a entrada, nao reinterpreta)."""
    return str(valor).strip() if str(valor or "").strip() else "—"


def _titulo(texto):
    return _p(texto, size=11, bold=True, color=COR_PRIMARIA)


def _tabela(dados, larguras, *, estilos=(), num_cols=()):
    t = Table(dados, colWidths=larguras, repeatRows=1)
    base = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, COR_PRIMARIA),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, COR_LINHA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    t.setStyle(TableStyle(base + list(estilos)))
    return t


def gerar_pdf(entrada: dict, resultado: dict, usuario: str) -> bytes:
    """`entrada`: o payload da tela (cambio, itens, despachante, courier).
    `resultado`: o retorno de `comparar`."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm,
                            title="Simulação de custo de importação")
    largura = doc.width
    d_tot, c_tot = resultado["despachante"]["totais"], resultado["courier"]["totais"]
    mais_barato = resultado.get("mais_barato")
    R = 2  # alinhamento a direita do Paragraph

    hist = [
        _p("Simulação de custo de importação — Despachante (DUIMP) x Courier", size=15, bold=True, color=COR_PRIMARIA),
        _p(f"Gerado em {agora_br().strftime('%d/%m/%Y %H:%M')} por {usuario} · Câmbio R$ "
           f"{_num_br(resultado.get('cambio'), 4)} por US$ · Sem crédito de impostos.", size=9, color=COR_SUAVE),
        Spacer(1, 6 * mm),
    ]

    # ── Resumo ──
    def caixa(rotulo, t, chave):
        selo = "  (mais barato)" if mais_barato == chave else ""
        sobre_cif = f" · {_num_br((t['total'] / t['cif'] - 1) * 100, 1)}% sobre o CIF" if t.get("cif") else ""
        return [_p(rotulo + selo, size=8.5, bold=True, color=COR_SUAVE),
                _p(_brl(t["total"]), size=15, bold=True),
                _p(f"Impostos {_brl(t['impostos'])}{sobre_cif}", size=8.5, color=COR_SUAVE)]
    if mais_barato:
        conclusao = f"{'Courier' if mais_barato == 'courier' else 'Despachante'} sai mais barato"
    else:
        conclusao = "Mesmo custo"
    resumo = Table([[caixa("DESPACHANTE (DUIMP)", d_tot, "despachante"), caixa("COURIER", c_tot, "courier"),
                     [_p("DIFERENÇA", size=8.5, bold=True, color=COR_SUAVE),
                      _p(_brl(resultado.get("diferenca")), size=15, bold=True),
                      _p(conclusao, size=8.5, color=COR_SUAVE)]]],
                   colWidths=[largura / 3] * 3)
    estilo_resumo = [("BOX", (i, 0), (i, 0), 0.6, COR_LINHA) for i in range(3)]
    estilo_resumo += [("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 6),
                      ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 8)]
    if mais_barato:
        col = 0 if mais_barato == "despachante" else 1
        estilo_resumo += [("BACKGROUND", (col, 0), (col, 0), COR_VENCEDOR),
                          ("BOX", (col, 0), (col, 0), 1.2, colors.HexColor("#16A34A"))]
    resumo.setStyle(TableStyle(estilo_resumo))
    hist += [resumo, Spacer(1, 3 * mm)]

    avisos = list(resultado["despachante"].get("avisos") or []) + list(resultado["courier"].get("avisos") or [])
    for aviso in avisos:
        t = Table([[_p("Atenção: " + aviso, size=8.5, color=colors.HexColor("#92400E"))]], colWidths=[largura])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), COR_AVISO),
                               ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#FDE68A"))]))
        hist += [t, Spacer(1, 2 * mm)]

    # ── Itens (entrada) ──
    itens = resultado["despachante"]["itens"]
    dados = [[_p(h, bold=True, align=a) for h, a in (("Código", 0), ("Descrição", 0), ("NCM", 0), ("Qtd", R),
                                                     ("Valor total US$", R), ("Peso líq. (kg)", R))]]
    for it in itens:
        dados.append([_p(it.get("codigo")), _p(it.get("descricao")), _p(formatar_ncm(it.get("ncm")) or it.get("ncm")),
                      _p(_num_br(it.get("quantidade"), 2).rstrip("0").rstrip(","), align=R),
                      _p(_num_br(it.get("valor_usd")), align=R),
                      _p(_num_br(it.get("peso"), 3) if it.get("peso") else "—", align=R)])
    hist += [Spacer(1, 3 * mm), _titulo("Itens (conforme invoice)"), Spacer(1, 2 * mm),
             _tabela(dados, [x * largura for x in (0.14, 0.42, 0.12, 0.08, 0.13, 0.11)]), Spacer(1, 5 * mm)]

    # ── Parametros (entrada) ──
    def bloco(titulo, campos, valores):
        linhas = [[_p(titulo, bold=True, color=COR_PRIMARIA), ""]]
        linhas += [[_p(rot), _p(_digitado(valores.get(chave)), align=R)] for chave, rot in campos]
        return linhas
    meia = (largura - 6 * mm) / 2
    t_desp = _tabela(bloco("Despachante (DUIMP)", CAMPOS_DESPACHANTE, entrada.get("despachante") or {}),
                     [meia * 0.68, meia * 0.32])
    # II/ICMS do courier em branco: o calculo usa o padrao - o PDF mostra o aplicado.
    courier = {"ii_courier_pct": f"{II_COURIER_PADRAO * 100:g}", "icms_courier_pct": f"{ICMS_COURIER_PADRAO * 100:g}",
               **{k: v for k, v in (entrada.get("courier") or {}).items() if str(v or "").strip()}}
    t_cour = _tabela(bloco("Courier", CAMPOS_COURIER, courier),
                     [meia * 0.68, meia * 0.32])
    params = Table([[t_desp, "", t_cour]], colWidths=[meia, 6 * mm, meia])
    params.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    hist += [KeepTogether([_titulo("Parâmetros"), Spacer(1, 2 * mm), params]), Spacer(1, 5 * mm)]

    # ── Composicao do desembolso ──
    dados = [[_p(" "), _p("Despachante", bold=True, align=R), _p("Courier", bold=True, align=R),
              _p("Courier − Despachante", bold=True, align=R)]]
    estilos = []
    for n, (chave, rot, classe) in enumerate(LINHAS_COMPOSICAO, start=1):
        dif = (c_tot.get(chave) or 0) - (d_tot.get(chave) or 0)
        cor = colors.HexColor("#991B1B") if dif > 0.004 else colors.HexColor("#166534") if dif < -0.004 else COR_TEXTO
        txt_dif = "—" if abs(dif) < 0.005 else ("+" if dif > 0 else "−") + _brl(abs(dif))
        negrito = classe is not None
        dados.append([_p(rot, bold=negrito), _p(_brl(d_tot.get(chave)), bold=negrito, align=R),
                      _p(_brl(c_tot.get(chave)), bold=negrito, align=R), _p(txt_dif, bold=negrito, color=cor, align=R)])
        if classe == "sub":
            estilos.append(("BACKGROUND", (0, n), (-1, n), COR_SUB))
        elif classe == "total":
            estilos.append(("LINEABOVE", (0, n), (-1, n), 1.2, COR_PRIMARIA))
    hist += [KeepTogether([_titulo("Composição do desembolso"), Spacer(1, 2 * mm),
                           _tabela(dados, [x * largura for x in (0.40, 0.20, 0.20, 0.20)], estilos=estilos)]),
             Spacer(1, 5 * mm)]

    # ── Custo por item ──
    dados = [[_p(h, bold=True, align=a) for h, a in (
        ("Código", 0), ("Descrição", 0), ("NCM", 0), ("Qtd", R), ("Unitário despachante", R),
        ("Unitário courier", R), ("Total despachante", R), ("Total courier", R))]]
    for di, ci in zip(itens, resultado["courier"]["itens"]):
        dados.append([_p(di.get("codigo")), _p(di.get("descricao")), _p(formatar_ncm(di.get("ncm")) or di.get("ncm")),
                      _p(_num_br(di.get("quantidade"), 2).rstrip("0").rstrip(","), align=R),
                      _p(_brl(di.get("unitario")), align=R), _p(_brl(ci.get("unitario")), align=R),
                      _p(_brl(di.get("total")), align=R), _p(_brl(ci.get("total")), align=R)])
    hist += [_titulo("Custo por item"), Spacer(1, 2 * mm),
             _tabela(dados, [x * largura for x in (0.11, 0.27, 0.10, 0.06, 0.115, 0.115, 0.115, 0.115)])]

    def rodape(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(COR_SUAVE)
        canvas.drawRightString(doc_.pagesize[0] - 12 * mm, 7 * mm, f"Sync · Comex · página {doc_.page}")
        canvas.restoreState()

    doc.build(hist, onFirstPage=rodape, onLaterPages=rodape)
    return buf.getvalue()
