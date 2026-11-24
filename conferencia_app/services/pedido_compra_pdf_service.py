"""Ordem de compra em PDF no mesmo layout do relatorio do GRV.

O GRV nao grava o PDF da OC no banco, entao o Sync redesenha: os dados vem da
bridge (/api/erp/pedido-compra-impressao) e os textos de cabecalho/rodape sao
o RTF que o comprador escreveu na propria OC - por isso o RTF e interpretado
aqui (negrito, marca-texto, cor, sublinhado, marcadores) em vez de virar texto
fixo no codigo. Layout conferido contra a OC 13074 impressa pelo GRV.
"""
from __future__ import annotations

import base64
import re
from datetime import datetime
from io import BytesIO
from typing import Any
from xml.sax.saxutils import escape

import requests
from flask import current_app

from .pedidos_service import _carregar_config_postgres_pedidos

_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
          "agosto", "setembro", "outubro", "novembro", "dezembro")

# modFrete da NF-e, que e o codigo que o GRV grava em frete_por_conta.
_FRETE = {
    0: "0-Contratação do Frete por conta do Remetente (CIF)",
    1: "1-Contratação do Frete por conta do Destinatário (FOB)",
    2: "2-Contratação do Frete por conta de Terceiros",
    3: "3-Transporte Próprio por conta do Remetente",
    4: "4-Transporte Próprio por conta do Destinatário",
    9: "9-Sem Ocorrência de Transporte",
}

# tfornece.regime_federal: 0 nos fornecedores do Simples (conferido por porte
# Microempresa/EPP e pela AGD na OC 13074), 1 nos demais (Gerdau, Arcelor...).
_REGIME = {0: "Simples Nacional", 1: "Regime Normal"}


# ---------- Bridge ----------


def buscar_pedido_impressao(numero_pedido: str) -> dict | None:
    cfg = _carregar_config_postgres_pedidos()
    if not cfg.get("api_url"):
        return None
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/ERP-PedidoImpressao",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    resp = requests.post(
        f"{cfg['api_url']}/api/erp/pedido-compra-impressao",
        headers=headers,
        json={"pedido": str(numero_pedido).strip()},
        timeout=cfg.get("api_timeout") or 30,
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data.get("sucesso"):
        raise RuntimeError(str((data or {}).get("erro") or "Resposta invalida da bridge"))
    return data


# ---------- RTF -> paragrafos do reportlab ----------

_RTF_TOKEN = re.compile(
    r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([^\\{}\r\n]+)|[\r\n]+"
)
_RTF_GRUPOS_IGNORADOS = {
    "fonttbl", "colortbl", "stylesheet", "info", "generator", "pict", "object",
    "themedata", "colorschememapping", "latentstyles", "datastore", "listtable",
    "listoverridetable", "rsidtbl", "xmlnstbl", "header", "footer", "fname",
}


def _rtf_tabela_cores(rtf: str) -> list[str | None]:
    m = re.search(r"\{\\colortbl\s*;?(.*?)\}", rtf, re.S)
    cores: list[str | None] = [None]
    if not m:
        return cores
    for entrada in m.group(1).split(";"):
        r = re.search(r"\\red(\d+)", entrada)
        g = re.search(r"\\green(\d+)", entrada)
        b = re.search(r"\\blue(\d+)", entrada)
        if r and g and b:
            cores.append("#%02x%02x%02x" % (int(r.group(1)), int(g.group(1)), int(b.group(1))))
    return cores


def _rtf_tabela_fontes(rtf: str) -> dict[int, str]:
    fontes: dict[int, str] = {}
    for m in re.finditer(r"\{\\f(\d+)[^{}]*?(?:\{\\\*\\fname ([^;}]*);\})?([^\\{};]*);\}", rtf):
        nome = (m.group(2) or m.group(3) or "").strip().lower()
        fontes[int(m.group(1))] = "Times-Roman" if "times" in nome else "Helvetica"
    return fontes


def rtf_para_paragrafos(rtf: str) -> list[dict]:
    """Converte RTF em [{"markup", "tamanho", "recuo", "recuo_1a", "espaco_depois"}].

    Cobre o que os textos da OC usam; o que nao reconhece e ignorado (nunca
    derruba o envio por causa de formatacao).
    """
    # O GRV grava um NUL depois do RTF; fora do grupo principal nada e texto.
    texto = str(rtf or "").replace("\x00", "")
    if not texto.lstrip().startswith("{\\rtf"):
        linhas = [linha for linha in texto.replace("\r", "").split("\n")]
        return [{"markup": escape(linha) or "&nbsp;", "tamanho": 8, "recuo": 0, "recuo_1a": 0, "espaco_depois": 0}
                for linha in linhas if linha.strip()]

    cores = _rtf_tabela_cores(texto)
    fontes = _rtf_tabela_fontes(texto)
    estado = {"b": False, "u": False, "cf": 0, "hl": 0, "fs": 24, "f": 0, "uc": 1}
    para = {"li": 0, "fi": 0, "sa": 0}
    pilha: list[tuple[dict, int]] = []
    ignorar_ate: int | None = None
    profundidade = 0
    pular = 0
    inicio_grupo = False
    paragrafos: list[dict] = []
    runs: list[tuple[str, dict]] = []

    def fechar_paragrafo():
        markup = ""
        maior = 0
        # Marcador do Word: simbolo \'b7 (fonte Symbol) seguido de tab.
        visiveis = [i for i, (txt, _) in enumerate(runs) if txt]
        if len(visiveis) >= 2 and runs[visiveis[0]][0] == "·" and runs[visiveis[1]][0] == "\t":
            runs[visiveis[0]] = ("•", runs[visiveis[0]][1])
            runs[visiveis[1]] = ("  ", runs[visiveis[1]][1])
        for txt, st in runs:
            if not txt:
                continue
            maior = max(maior, st["fs"])
            pedaco = escape(txt).replace("\t", "&nbsp;&nbsp;&nbsp;").replace("  ", " &nbsp;")
            if st["u"]:
                pedaco = f"<u>{pedaco}</u>"
            if st["b"]:
                pedaco = f"<b>{pedaco}</b>"
            attrs = [f'name="{fontes.get(st["f"], "Helvetica")}"', f'size="{st["fs"] / 2:g}"']
            cor = cores[st["cf"]] if 0 < st["cf"] < len(cores) else None
            if cor:
                attrs.append(f'color="{cor}"')
            fundo = cores[st["hl"]] if 0 < st["hl"] < len(cores) else None
            if fundo:
                attrs.append(f'backColor="{fundo}"')
            markup += f"<font {' '.join(attrs)}>{pedaco}</font>"
        paragrafos.append({
            "markup": markup,
            "tamanho": (maior or estado["fs"]) / 2,
            "recuo": para["li"] / 20,
            "recuo_1a": para["fi"] / 20,
            "espaco_depois": para["sa"] / 20,
        })
        runs.clear()

    for m in _RTF_TOKEN.finditer(texto):
        palavra, num, hexa, simbolo, chave, literal = m.groups()
        if chave == "{":
            profundidade += 1
            pilha.append((dict(estado), profundidade))
            inicio_grupo = True
            continue
        if chave == "}":
            if ignorar_ate is not None and profundidade == ignorar_ate:
                ignorar_ate = None
            if pilha:
                estado = pilha.pop()[0]
            profundidade -= 1
            inicio_grupo = False
            continue
        if ignorar_ate is not None:
            continue
        if inicio_grupo:
            inicio_grupo = False
            if simbolo == "*" or (palavra and palavra in _RTF_GRUPOS_IGNORADOS):
                ignorar_ate = profundidade
                continue
        if palavra:
            n = int(num) if num is not None else None
            if palavra == "par":
                fechar_paragrafo()
            elif palavra == "pard":
                para = {"li": 0, "fi": 0, "sa": 0}
            elif palavra == "plain":
                estado.update(b=False, u=False, cf=0, hl=0, fs=24, f=0)
            elif palavra == "b":
                estado["b"] = n != 0
            elif palavra == "ul":
                estado["u"] = n != 0
            elif palavra in ("ulnone", "ul0"):
                estado["u"] = False
            elif palavra == "cf":
                estado["cf"] = n or 0
            elif palavra in ("highlight", "cb"):
                estado["hl"] = n or 0
            elif palavra == "fs":
                estado["fs"] = n or 24
            elif palavra == "f":
                estado["f"] = n or 0
            elif palavra == "uc":
                estado["uc"] = n or 0
            elif palavra in ("li", "fi", "sa"):
                para[palavra] = n or 0
            elif palavra == "tab":
                runs.append(("\t", dict(estado)))
            elif palavra == "line":
                runs.append(("\n", dict(estado)))
            elif palavra == "u" and n is not None:
                runs.append((chr(n % 65536), dict(estado)))
                pular = estado["uc"]
            continue
        if hexa:
            if pular:
                pular -= 1
                continue
            runs.append((bytes([int(hexa, 16)]).decode("cp1252", "replace"), dict(estado)))
            continue
        if simbolo:
            if simbolo in "\\{}":
                runs.append((simbolo, dict(estado)))
            elif simbolo == "~":
                runs.append(("\u00a0", dict(estado)))
            continue
        if literal:
            if profundidade == 0:
                continue
            if pular:
                corte = min(pular, len(literal))
                literal = literal[corte:]
                pular -= corte
            if literal:
                runs.append((literal, dict(estado)))
    if any(txt.strip() for txt, _ in runs):
        fechar_paragrafo()

    # Linhas vazias do fim (o GRV costuma deixar \par sobrando) nao vao pro PDF.
    while paragrafos and not re.sub(r"<[^>]+>|&nbsp;|\s", "", paragrafos[-1]["markup"]):
        paragrafos.pop()
    return paragrafos


# ---------- Formatacao ----------


def _num(valor: Any, casas: int = 2) -> str:
    try:
        numero = float(valor or 0)
    except (TypeError, ValueError):
        numero = 0.0
    return f"{numero:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _data(valor: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(valor or "")[:19])
    except ValueError:
        return None


def _txt(valor: Any) -> str:
    return escape(str(valor or "").strip())


def _texto_rtf_simples(valor: Any) -> str:
    """Descricao de servico vem em RTF de uma linha: junta o texto puro."""
    return " ".join(
        re.sub(r"<[^>]+>", "", p["markup"]).replace("&nbsp;", " ").strip()
        for p in rtf_para_paragrafos(valor)
    ).strip()


# ---------- PDF ----------


def gerar_pdf_pedido_compra_grv(numero_pedido: str) -> bytes | None:
    """PDF da OC no layout do GRV, ou None se a bridge nao achar o pedido."""
    dados = buscar_pedido_impressao(numero_pedido)
    if not dados or not dados.get("pedido"):
        return None
    return montar_pdf_pedido_compra(dados)


def montar_pdf_pedido_compra(dados: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas
    from reportlab.platypus import (
        BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    oc = dados["pedido"]
    servicos = dados.get("servicos") or []
    materiais = dados.get("materiais") or []

    pagina = landscape(A4)
    largura = pagina[0] - 20 * mm
    preto = colors.black
    cinza_linha = colors.HexColor("#9a9a9a")
    cinza_fundo = colors.HexColor("#e6e6e6")

    def estilo(nome, tamanho=7.5, negrito=False, alinhamento=0, fonte="Helvetica", leading=None):
        return ParagraphStyle(
            nome, fontName=f"{fonte}-Bold" if negrito else fonte, fontSize=tamanho,
            leading=leading or tamanho * 1.2, alignment=alinhamento,
        )

    s_txt = estilo("txt")
    s_neg = estilo("neg", negrito=True)
    s_dir = estilo("dir", alinhamento=TA_RIGHT)
    s_dir_neg = estilo("dirneg", negrito=True, alinhamento=TA_RIGHT)
    s_cen_neg = estilo("cenneg", negrito=True, alinhamento=TA_CENTER)
    s_peq = estilo("peq", 6.5)
    s_peq_neg = estilo("peqneg", 6.5, negrito=True)
    s_titulo = estilo("titulo", 17, negrito=True, alinhamento=TA_CENTER)
    s_cidade = estilo("cidade", 11, negrito=True, alinhamento=TA_RIGHT)
    s_secao = estilo("secao", 10, negrito=True)
    P = Paragraph

    numero = _txt(oc.get("numero"))
    emissao = _data(oc.get("data"))
    entrega = _data(oc.get("dt_prevista_entrega"))
    cidade_data = _txt(oc.get("empresa_cidade"))
    if emissao:
        cidade_data += f", {emissao.day:02d} de {_MESES[emissao.month - 1]} de {emissao.year}"

    logo = None
    logo_bytes = b""
    if oc.get("empresa_logo"):
        try:
            logo_bytes = base64.b64decode(oc["empresa_logo"])
            logo = ImageReader(BytesIO(logo_bytes))
        except Exception:
            logo = None
            current_app.logger.warning("Logo da empresa no GRV ilegivel; OC %s sai sem logo.", numero)

    # ----- cabecalho (todas as paginas) -----
    def bloco_cabecalho():
        endereco_empresa = ", ".join(x for x in (_txt(oc.get("empresa_endereco")), _txt(oc.get("empresa_numero"))) if x)
        endereco_empresa = " - ".join(x for x in (
            endereco_empresa, _txt(oc.get("empresa_bairro")), _txt(oc.get("empresa_cidade")),
            _txt(oc.get("empresa_uf")),
        ) if x)
        if oc.get("empresa_cep"):
            endereco_empresa += f" - CEP {_txt(oc.get('empresa_cep'))}"
        direita = [
            P(cidade_data, s_cidade),
            P(_txt(oc.get("empresa_nome")), s_dir_neg),
            P(f"CNPJ: {_txt(oc.get('empresa_cnpj'))} - IE: {_txt(oc.get('empresa_ie'))}", s_dir_neg),
            P(f"Endereço: {endereco_empresa}", s_dir_neg),
            P(f"Fone/Fax: {_txt(oc.get('empresa_telefone'))}", s_dir_neg),
        ]
        enderecos = P(
            f"Endereço de entrega: {_txt(_texto_rtf_simples(oc.get('endereco_entrega')))}<br/>"
            f"Endereço de cobrança: {_txt(_texto_rtf_simples(oc.get('endereco_cobranca')))}", s_peq_neg)
        meio = Table([[P(f"Ordem de Compra nº: {numero}", s_titulo)], [enderecos]],
                     colWidths=[120 * mm], rowHeights=[11 * mm, 13 * mm])
        meio.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (0, 0), 0.8, preto),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ]))
        celula_logo = ""
        if logo:
            from reportlab.platypus import Image as RLImage
            lw, lh = logo.getSize()
            esc = min(38 * mm / lw, 20 * mm / lh)
            celula_logo = RLImage(BytesIO(logo_bytes), width=lw * esc, height=lh * esc)
        topo = Table([[celula_logo, meio, direita]],
                     colWidths=[42 * mm, 120 * mm, largura - 162 * mm], rowHeights=[24 * mm])
        topo.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.8, preto),
            ("LINEAFTER", (0, 0), (1, 0), 0.8, preto),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, 0), "CENTER"),
            ("LEFTPADDING", (1, 0), (1, 0), 0),
            ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (1, 0), (1, 0), 0),
            ("BOTTOMPADDING", (1, 0), (1, 0), 0),
        ]))

        def rot(rotulo, valor):
            return P(f"<b>{rotulo}</b> {valor}", s_txt)

        endereco_forn = ", ".join(x for x in (_txt(oc.get("fornecedor_endereco")), _txt(oc.get("fornecedor_numero"))) if x)
        email = _txt(oc.get("fornecedor_email"))
        regime = _REGIME.get(oc.get("fornecedor_regime_federal"), "")
        forn = Table([
            [rot("Fornecedor:", _txt(oc.get("fornecedor"))), rot("Contato:", _txt(oc.get("contato"))), "",
             rot("E-mail:", f'<font color="#1f4e9c"><u>{email}</u></font>' if email else "")],
            [rot("Endereço:", endereco_forn), rot("Bairro:", _txt(oc.get("fornecedor_bairro"))), "", ""],
            [rot("Cidade:", _txt(oc.get("fornecedor_cidade"))), rot("UF:", _txt(oc.get("fornecedor_uf"))),
             rot("CEP:", _txt(oc.get("fornecedor_cep"))), ""],
            [Table([[rot("CNPJ:", _txt(oc.get("fornecedor_cnpj"))), rot("IE:", _txt(oc.get("fornecedor_ie")))]],
                   colWidths=[50 * mm, 50 * mm], style=[("LEFTPADDING", (0, 0), (-1, -1), 0),
                                                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                                                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]),
             rot("Tel.:", _txt(oc.get("fornecedor_telefone"))), rot("Fax.:", _txt(oc.get("fornecedor_fax"))),
             rot("Enquadramento fiscal:", regime)],
        ], colWidths=[112 * mm, 50 * mm, 40 * mm, largura - 202 * mm])
        forn.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.8, preto),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 0.6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6),
        ]))
        return [topo, forn]

    # ----- rodape de totais (todas as paginas) -----
    def bloco_rodape():
        frete = _FRETE.get(oc.get("frete_por_conta"), _txt(oc.get("frete_por_conta")))
        c = largura / 7
        tab = Table([
            [P(f"<b>Transportadora:</b> {_txt(oc.get('transportadora'))}", s_txt), "", "", "",
             P(f"<b>Frete:</b> {frete}", s_txt), "", P("Data de entrega", s_cen_neg)],
            [P(f"<b>Forma de pagamento:</b> {_txt(oc.get('forma_pgto') or oc.get('cond_pagamento'))}", s_txt), "", "", "",
             "", "", P(entrega.strftime("%d/%m/%y") if entrega else "", estilo("ent", alinhamento=TA_CENTER))],
            [P(t, estilo(f"rt{i}", 7, negrito=True, alinhamento=TA_CENTER)) for i, t in enumerate((
                "Total dos itens (R$)", "Valor do frete (R$)", "Valor do seguro (R$)",
                "Valor de outras despesas (R$)", "Valor do ICMS (R$)", "Valor do IPI (R$)",
                "Valor total do pedido (R$)"))],
            [P(_num(oc.get(k)), s_dir) for k in ("subtotal", "vl_frete", "vl_seguro", "vl_outras_despesas",
                                                 "vl_icms", "vl_ipi", "totalgeral")],
        ], colWidths=[c] * 7)
        tab.setStyle(TableStyle([
            ("SPAN", (0, 0), (3, 0)), ("SPAN", (4, 0), (5, 0)),
            ("SPAN", (0, 1), (3, 1)), ("SPAN", (4, 1), (5, 1)),
            ("BOX", (0, 0), (-1, -1), 0.8, preto),
            ("INNERGRID", (0, 2), (-1, -1), 0.5, cinza_linha),
            ("LINEBELOW", (0, 0), (-1, 1), 0.5, cinza_linha),
            ("LINEBEFORE", (4, 0), (4, 1), 0.5, cinza_linha),
            ("LINEBEFORE", (6, 0), (6, 1), 0.5, cinza_linha),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        return tab

    cab = bloco_cabecalho()
    rod = bloco_rodape()
    altura_cab = sum(t.wrap(largura, pagina[1])[1] for t in cab) + 2 * mm
    altura_rod = rod.wrap(largura, pagina[1])[1] + 9 * mm
    margem = 10 * mm

    def desenhar_fixos(canv, doc):
        canv.saveState()
        y = pagina[1] - margem
        for bloco in cab:
            _, h = bloco.wrap(largura, pagina[1])
            y -= h
            bloco.drawOn(canv, margem, y)
        _, h = rod.wrap(largura, pagina[1])
        rod.drawOn(canv, margem, margem + 6 * mm)
        canv.setLineWidth(1.2)
        canv.line(margem, margem + 5 * mm, pagina[0] - margem, margem + 5 * mm)
        canv.setFont("Helvetica", 6.5)
        rodape_txt = str(oc.get("empresa_nome") or "")
        if oc.get("empresa_site"):
            rodape_txt += f" - {oc['empresa_site']}"
        canv.drawString(margem, margem + 2 * mm, rodape_txt)
        canv.restoreState()

    class CanvasNumerado(rl_canvas.Canvas):
        """"Página x/y" precisa do total: guarda as paginas e escreve no save."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._paginas = []

        def showPage(self):
            self._paginas.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._paginas)
            for estado_pagina in self._paginas:
                self.__dict__.update(estado_pagina)
                self.setFont("Helvetica", 6.5)
                self.drawRightString(pagina[0] - margem, margem + 2 * mm, f"Página {self._pageNumber}/{total}")
                super().showPage()
            super().save()

    # ----- corpo -----
    story: list = []

    def paragrafos_rtf(rtf):
        saida = []
        for p in rtf_para_paragrafos(rtf):
            st = ParagraphStyle(
                "rtf", fontName="Helvetica", fontSize=p["tamanho"], leading=p["tamanho"] * 1.25,
                leftIndent=p["recuo"], firstLineIndent=p["recuo_1a"], spaceAfter=min(p["espaco_depois"], 6),
            )
            saida.append(P(p["markup"] or "&nbsp;", st))
        return saida

    story += paragrafos_rtf(oc.get("cabecalho"))
    story.append(Spacer(1, 2 * mm))

    if servicos:
        cw = [10 * mm, 52 * mm, 0, 16 * mm, 10 * mm, 13 * mm, 13 * mm, 13 * mm, 26 * mm, 22 * mm, 24 * mm]
        cw[2] = largura - sum(cw)
        linhas = [
            [P("Serviços de Terceiros", s_secao), "", "", "", "", P("Impostos", estilo("imp", alinhamento=TA_CENTER)),
             "", "", P("Preço unitário", estilo("pu", alinhamento=TA_CENTER)), "", ""],
            [P(t, s_txt) for t in ("Item", "Grupo / Sub-grupo")]
            + [P("Descrição", estilo("dh", alinhamento=TA_CENTER))]
            + [P(t, estilo(f"h{i}", alinhamento=TA_CENTER)) for i, t in enumerate(
                ("Qtde", "UN", "% ISS", "% ICMS", "% IPI", "c/ ICMS s/IPI (R$)", "Valor ISS (R$)", "Valor total (R$)"))],
        ]
        estilos = [
            ("SPAN", (0, 0), (4, 0)), ("SPAN", (5, 0), (7, 0)), ("SPAN", (8, 0), (10, 0)),
            ("BOX", (0, 0), (-1, -1), 0.8, preto),
            ("LINEBELOW", (0, 1), (-1, 1), 0.8, preto),
            ("LINEBEFORE", (3, 0), (-1, 1), 0.5, cinza_linha),
            ("LINEBELOW", (5, 0), (7, 0), 0.5, cinza_linha),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 1.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ]
        total_iss = 0.0
        total = 0.0
        for i, s in enumerate(servicos, 1):
            grupo = " / ".join(x for x in (_txt(s.get("grupo_serv_ter")), _txt(s.get("sub_grupo_serv_ter"))) if x)
            linha_principal = len(linhas)
            linhas.append([
                P(str(i), s_txt), P(grupo, s_txt),
                P(_txt(_texto_rtf_simples(s.get("descricao"))), estilo(f"d{i}", 8.5, alinhamento=TA_CENTER)),
                P(_num(s.get("qtde"), 4), s_dir), P(_txt(s.get("unidade")), s_txt),
                P(_num(s.get("iss")), s_dir), P(_num(s.get("icms")), s_dir), P(_num(s.get("ipi")), s_dir),
                P(_num(s.get("vl_unitario")), s_dir), P(_num(s.get("vl_iss")), s_dir), P(_num(s.get("vl_total")), s_dir),
            ])
            os_txt = " - ".join(x for x in (_txt(s.get("cod_os_completo")), _txt(s.get("descricao_os"))) if x)
            if s.get("posicao_os"):
                os_txt += f" / {_txt(s.get('posicao_os'))}"
            sub = Table([
                [P("Qtde", s_peq_neg), P("Unidade", s_peq_neg), P("OS / Descrição / Posição", s_peq_neg)],
                [P(_num(s.get("qtde"), 4), s_peq), P(_txt(s.get("unidade")), s_peq), P(os_txt, s_peq)],
            ], colWidths=[18 * mm, 18 * mm, cw[1] + cw[2] - 40 * mm])
            sub.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
            linhas.append(["", sub, "", "", "", "", "", "", "", "", ""])
            estilos.append(("SPAN", (1, len(linhas) - 1), (2, len(linhas) - 1)))
            estilos.append(("LINEBEFORE", (3, linha_principal), (-1, len(linhas) - 1), 0.5, cinza_linha))
            if i % 2 == 0:
                estilos.append(("BACKGROUND", (0, linha_principal), (-1, len(linhas) - 1), cinza_fundo))
            total_iss += float(s.get("vl_iss") or 0)
            total += float(s.get("vl_total") or 0)
        linhas.append(["", "", "", "", "", "", "", "", P("Total", s_dir), P(_num(total_iss), s_dir), P(_num(total), s_dir)])
        estilos += [
            ("LINEABOVE", (8, -1), (-1, -1), 0.8, preto),
            ("LINEBEFORE", (9, -1), (-1, -1), 0.5, cinza_linha),
            ("BOX", (9, -1), (-1, -1), 0.5, cinza_linha),
        ]
        tabela = Table(linhas, colWidths=cw, repeatRows=2)
        tabela.setStyle(TableStyle(estilos))
        story.append(tabela)
        story.append(Spacer(1, 3 * mm))

    if materiais:
        cw = [10 * mm, 30 * mm, 0, 18 * mm, 10 * mm, 14 * mm, 14 * mm, 26 * mm, 26 * mm]
        cw[2] = largura - sum(cw)
        linhas = [
            [P("Materiais", s_secao)] + [""] * 8,
            [P(t, s_neg) for t in ("Item", "Código", "Descrição")]
            + [P(t, estilo(f"mh{i}", negrito=True, alinhamento=TA_CENTER)) for i, t in enumerate(
                ("Qtde", "UN", "% ICMS", "% IPI", "Preço unitário (R$)", "Valor total (R$)"))],
        ]
        for i, m in enumerate(materiais, 1):
            linhas.append([
                P(str(i), s_txt), P(_txt(m.get("cod_interno")), s_txt), P(_txt(_texto_rtf_simples(m.get("descricao"))), s_txt),
                P(_num(m.get("qtde"), 4), s_dir), P(_txt(m.get("unidade")), s_txt), P(_num(m.get("icms")), s_dir),
                P(_num(m.get("ipi")), s_dir), P(_num(m.get("vl_unitario"), 4), s_dir), P(_num(m.get("vl_total")), s_dir),
            ])
        tabela = Table(linhas, colWidths=cw, repeatRows=2)
        tabela.setStyle(TableStyle([
            ("SPAN", (0, 0), (-1, 0)),
            ("BOX", (0, 0), (-1, -1), 0.8, preto),
            ("LINEBELOW", (0, 1), (-1, 1), 0.8, preto),
            ("INNERGRID", (0, 2), (-1, -1), 0.3, cinza_linha),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tabela)
        story.append(Spacer(1, 3 * mm))

    story.append(P("Obs.:", s_neg))
    story += paragrafos_rtf(oc.get("obs"))
    story.append(Spacer(1, 2 * mm))
    story += paragrafos_rtf(oc.get("rodape"))
    story.append(Spacer(1, 4 * mm))

    assinatura = Table(
        [[P("Aprovado eletronicamente por:", s_neg), P("Solicitante:", s_neg)],
         [P(_txt(oc.get("aprovado_por")), s_txt), P(_txt(oc.get("solicitante")), s_txt)]],
        colWidths=[largura - 55 * mm, 55 * mm],
    )
    assinatura.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.8, preto),
        ("LINEBEFORE", (1, 0), (1, -1), 0.8, preto),
    ]))
    story.append(assinatura)

    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=pagina, leftMargin=margem, rightMargin=margem,
        topMargin=margem, bottomMargin=margem,
        title=f"Ordem de Compra {numero}", author=str(oc.get("empresa_nome") or ""),
    )
    frame = Frame(
        margem, margem + altura_rod, largura, pagina[1] - 2 * margem - altura_cab - altura_rod,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    doc.addPageTemplates([PageTemplate(id="oc", frames=[frame], onPage=desenhar_fixos)])
    doc.build(story, canvasmaker=CanvasNumerado)
    return buffer.getvalue()
