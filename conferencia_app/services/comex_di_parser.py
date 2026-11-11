"""Leitura do extrato da DUIMP (despacho formal) e da DIR (courier).

Validado contra 3 documentos reais (11/11/2026): 2 extratos de DUIMP e 1
DIR da FedEx. O que NAO vem nesses documentos (ICMS - guia estadual -,
fatura do frete, despesas do despachante) e' digitado na gestao financeira.
"""
from __future__ import annotations

import re


def texto_do_pdf(conteudo: bytes) -> str:
    import pymupdf
    try:
        with pymupdf.open(stream=conteudo, filetype="pdf") as doc:
            return "\n".join(p.get_text() for p in doc)
    except Exception:
        raise ValueError("Não consegui abrir o PDF - envie o extrato da DUIMP ou a DIR original.")


def _valor(texto: str | None) -> float | None:
    if not texto:
        return None
    limpo = re.sub(r"[^\d,.]", "", texto)
    if not limpo:
        return None
    return float(limpo.replace(".", "").replace(",", "."))


def _campo(texto: str, rotulo: str) -> str | None:
    """Linha "ROTULO      : valor" do bloco de informacoes da DUIMP."""
    m = re.search(rf"^{rotulo}\s*:\s*(.*)$", texto, re.MULTILINE)
    return m.group(1).strip() if m else None


def ler_duimp(texto: str) -> dict:
    numero = re.search(r"Extrato da Duimp\s+(\S+)", texto, re.IGNORECASE)
    # So' o bloco "TOTAL DOS IMPOSTOS" (ate' o proximo titulo) - o mesmo
    # rotulo aparece em outras partes do extrato.
    bloco = re.search(r"TOTAL DOS IMPOSTOS\n(.*?)\n[A-Z][A-Z ]+\n", texto, re.DOTALL)
    impostos = {}
    for linha in (bloco.group(1).splitlines() if bloco else []):
        m = re.match(r"\s*([A-Z][A-Z ./]+?)\s*:\s*([\d.,]+)\s*$", linha)
        if m:
            impostos[m.group(1).strip()] = _valor(m.group(2))
    cambio = _campo(texto, "COTACAO DO DOLAR \\(EUA\\)") or _campo(texto, "COTACAO DA MOEDA NEGOCIADA")
    return {
        "documento_tipo": "DUIMP",
        "documento_numero": numero.group(1) if numero else None,
        "referencia_importador": _campo(texto, "REFERENCIA DO IMPORTADOR"),
        "cambio": _valor(cambio.split("-")[0]) if cambio else None,
        "ii": impostos.get("II"),
        "ipi": impostos.get("IPI"),
        "pis": impostos.get("PIS"),
        "cofins": impostos.get("COFINS"),
        "taxa_siscomex": impostos.get("TAXA DE UTILIZACAO"),
        "afrmm": _valor(_campo(texto, "AFRMM")),
        "cbs_ibs": None,
        "multas": None,
    }


def ler_dir(texto: str) -> dict:
    linhas = [l.strip() for l in texto.splitlines()]

    def depois(rotulo: str, salto: int = 1) -> str | None:
        try:
            return linhas[linhas.index(rotulo) + salto]
        except (ValueError, IndexError):
            return None

    # Tabela de impostos: "Tipo / Valor Devido / Valor Recolhido / ..." e
    # depois trios (tributo, devido, recolhido). Vale o devido.
    devidos = {}
    if "Autenticação Bancária" in linhas:
        resto = [l for l in linhas[linhas.index("Autenticação Bancária") + 1:] if l]
        for i in range(0, len(resto) - 1, 3):
            devidos[resto[i]] = _valor(resto[i + 1])
    numero = depois("Declaração (DIR)/Versão")
    frete = depois("Valor Frete") or ""
    frete_brl = frete.split("R$")[-1] if "R$" in frete else None
    cbs_ibs = sum(v or 0 for k, v in devidos.items() if k.startswith(("CBS", "IBS")))
    return {
        "documento_tipo": "DIR",
        "documento_numero": numero.split("/")[0].strip() if numero else None,
        "referencia_importador": depois("Remessa", -1),
        "cambio": _valor((depois("Taxa de Câmbio") or "").replace(" ", "")),
        "ii": devidos.get("II"),
        "ipi": devidos.get("IPI"),
        "pis": devidos.get("PIS"),
        "cofins": devidos.get("COFINS"),
        "taxa_siscomex": None,
        "afrmm": None,
        "cbs_ibs": round(cbs_ibs, 2) if any(k.startswith(("CBS", "IBS")) for k in devidos) else None,
        "multas": _valor((depois("Multas") or "").replace("R$", "")),
        "frete_declarado_brl": _valor(frete_brl),
    }


def ler_documento(conteudo: bytes) -> dict:
    texto = texto_do_pdf(conteudo)
    if "DECLARAÇÃO DE IMPORTAÇÃO DE REMESSA" in texto:
        return ler_dir(texto)
    if re.search(r"Extrato da Duimp", texto, re.IGNORECASE):
        return ler_duimp(texto)
    raise ValueError("Não reconheci o documento: envie o extrato da DUIMP ou a DIR (courier) em PDF.")
