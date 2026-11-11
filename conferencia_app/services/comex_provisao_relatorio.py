"""PDF e Excel da provisao de custo de importacao (download e e-mail a
Controladoria)."""
from __future__ import annotations

import json
from io import BytesIO

from ..models import ComexProvisao

_ROTULOS = (("fob", "FOB"), ("frete", "Frete (base)"), ("seguro", "Seguro"), ("cif", "CIF"), ("ii", "II"), ("ipi", "IPI"),
            ("pis", "PIS"), ("cofins", "COFINS"), ("icms", "ICMS"), ("taxas", "Taxas (Siscomex/AFRMM/multas)"),
            ("despesas", "Despesas"))


def _brl(v) -> str:
    return "R$ " + f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _dados(p: ComexProvisao) -> tuple[dict, dict]:
    resultado = json.loads(p.resultado)
    return resultado, resultado[p.modelo]


def gerar_pdf(p: ComexProvisao) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    resultado, escolhido = _dados(p)
    estilos = getSampleStyleSheet()
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=12 * mm)
    proc = p.processo
    markup = f" (markup {resultado['markup_pct']}%)" if resultado.get("markup_pct") else ""
    modelo = "Despachante (DUIMP)" if p.modelo == "despachante" else "Courier"
    pos = ", ".join(resultado.get("pos") or []) or "—"
    cabecalho = (
        f"<b>Provisão de custo de importação — {proc.id_op}</b> (versão {p.versao})<br/>"
        f"Fornecedor: {proc.fornecedor or '—'} · POs: {pos}<br/>"
        f"Modelo: {modelo} · Incoterm: {p.incoterm} · PTAX: {p.ptax:.4f} · "
        f"Forwarder: {resultado.get('forwarder') or '—'}{markup}<br/>"
        f"Gerada em {p.criado_em:%d/%m/%Y %H:%M} por {p.criado_por or '—'}"
    )
    resumo = Table([
        ["Impostos e taxas", "Frete internacional (cotação)", "Despesas de desembaraço", "Total provisionado"],
        [_brl(p.impostos_brl), _brl(p.frete_brl), _brl(p.despesas_brl), _brl((p.impostos_brl or 0) + (p.frete_brl or 0) + (p.despesas_brl or 0))],
    ], hAlign="LEFT")
    resumo.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                ("GRID", (0, 0), (-1, -1), .4, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 9)]))
    cab = ["PO", "Código", "NCM", "Qtde"] + [r for _, r in _ROTULOS] + ["Total"]
    linhas = [cab] + [
        [i.get("po") or "", i.get("codigo") or "", i.get("ncm") or "", f"{i.get('quantidade') or 0:g}"]
        + [_brl(i.get(c)) for c, _ in _ROTULOS] + [_brl(i.get("total"))]
        for i in escolhido["itens"]
    ] + [["Total", "", "", ""] + [_brl(escolhido["totais"].get(c)) for c, _ in _ROTULOS] + [_brl(escolhido["totais"]["total"])]]
    tabela = Table(linhas, repeatRows=1)
    tabela.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                ("GRID", (0, 0), (-1, -1), .3, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 6.5),
                                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"), ("ALIGN", (3, 1), (-1, -1), "RIGHT")]))
    avisos = escolhido.get("avisos") or []
    corpo = [Paragraph(cabecalho, estilos["Normal"]), Spacer(1, 6), resumo, Spacer(1, 8), tabela]
    corpo += [Spacer(1, 6), Paragraph("<br/>".join(f"Obs.: {a}" for a in avisos), estilos["Normal"])] if avisos else []
    corpo += [Spacer(1, 6), Paragraph(
        f"Base do frete pelo Incoterm {p.incoterm}: {', '.join(resultado.get('etapas_na_base') or [])}. "
        "Impostos na PTAX (taxa fiscal); frete provisionado = cotação escolhida em R$ com o markup do forwarder.", estilos["Normal"])]
    doc.build(corpo)
    return buf.getvalue()


def gerar_xlsx(p: ComexProvisao) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    resultado, escolhido = _dados(p)
    wb = Workbook()
    ws = wb.active
    ws.title = "Provisão"
    proc = p.processo
    for linha in (
        ["Processo", proc.id_op], ["Fornecedor", proc.fornecedor], ["POs", ", ".join(resultado.get("pos") or [])],
        ["Versão", p.versao], ["Modelo", p.modelo], ["Incoterm", p.incoterm], ["PTAX", p.ptax],
        ["Gerada em", p.criado_em.strftime("%d/%m/%Y %H:%M")], ["Gerada por", p.criado_por], [],
        ["Impostos e taxas", p.impostos_brl], ["Frete internacional (cotação)", p.frete_brl],
        ["Despesas de desembaraço", p.despesas_brl], [],
    ):
        ws.append(linha)
    cab = ["PO", "Código", "Descrição", "NCM", "Qtde"] + [r for _, r in _ROTULOS] + ["Total"]
    ws.append(cab)
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for i in escolhido["itens"]:
        ws.append([i.get("po"), i.get("codigo"), i.get("descricao"), i.get("ncm"), i.get("quantidade")]
                  + [i.get(c) for c, _ in _ROTULOS] + [i.get("total")])
    ws.append(["Total", "", "", "", ""] + [escolhido["totais"].get(c) for c, _ in _ROTULOS] + [escolhido["totais"]["total"]])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
