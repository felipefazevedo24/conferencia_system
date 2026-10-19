"""Sugestão de máscara de imposto no Documento de Entrada, por item.

O GRV não grava qual máscara foi usada num lançamento, só os impostos que ela
aplicou (CFOP, CST de ICMS/IPI/PIS/COFINS, alíquota de ICMS e conta). A
máscara se descobre comparando esses impostos com o cadastro
(public.tmascara_impostos).

Para cada item, do mais confiável para o menos:
1. alta  - o que foi lançado deste produto vindo deste fornecedor (4 meses);
2. media - o que foi lançado deste produto vindo de outros fornecedores;
3. baixa - regra pelo XML: a máscara que a família do produto mais recebe
           para a operação do fornecedor (1 ano, pesando o recente).
Só conta lançamento compatível com a operação do XML: um retorno de conserto
(5916) não herda a máscara de uma compra, nem compra sem ST a de uma com ST.

Teste em 19/10/2026 contra 663 itens lançados no GRV (sugestão feita só com o
que existia antes de cada lançamento): 71% de acerto na sugestão principal,
82% com a certa entre as "outras". O que erra é quase sempre 1101 x 1556 ou
uso e consumo com/sem crédito - nem o XML nem o histórico decidem isso.

Nada é gravado: o histórico é lido do GRV na hora. Se o bridge não responder,
volta sem sugestão e a tela segue.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any

import requests
from flask import current_app

from .erp_estoque_service import _bridge_config, _headers

# Sufixos de CFOP em que a entrada espelha a saída do fornecedor (5916 -> 1916).
# Nos demais (venda: 5101, 5102, 5405...) o CFOP de entrada depende do uso que
# a Columbia dá ao material, e quem decide é o histórico da família.
_SUFIXOS_ESPELHO = {
    "124", "125", "151", "152", "201", "202", "352", "353", "551", "552", "553", "554", "555", "557",
    "901", "902", "903", "904", "905", "906", "907", "908", "909", "910", "911", "912", "913", "914",
    "915", "916", "917", "918", "919", "920", "921", "922", "923", "924", "925", "926", "933", "934", "949",
}
_CST_ST = {"10", "30", "60", "70", "201", "202", "203", "500"}
_MAX_OUTRAS = 4
# O critério do fiscal muda com o tempo: lançamento de 60 dias atrás vale
# metade do de hoje.
_MEIA_VIDA_DIAS = 60


def _digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _cst(valor: Any, tamanho: int) -> str:
    d = _digitos(valor)
    return d.zfill(tamanho) if d else ""


def _num(valor: Any) -> float:
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def normalizar_produto(valor: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(valor or "").upper())


def _impostos(linha: dict[str, Any]) -> dict[str, Any]:
    return {
        "cfop": _digitos(linha.get("cfop"))[:4],
        "cst": _cst(linha.get("cst"), 3),
        "icms": _num(linha.get("icms")),
        "cst_ipi": _cst(linha.get("cst_ipi"), 2),
        "cst_pis": _cst(linha.get("cst_pis"), 2),
        "cst_cofins": _cst(linha.get("cst_cofins"), 2),
        "conta": str(linha.get("conta") or "").strip(),
    }


def _preparar_mascaras(mascaras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prontas = []
    for m in mascaras:
        # O CFOP da máscara só existe na descrição ("1556 - COMPRA DE ...").
        cfop = re.search(r"(\d{4})", str(m.get("descricao") or ""))
        prontas.append({
            **_impostos({**m, "cfop": cfop.group(1) if cfop else ""}),
            "codigo": m.get("codigo"),
            "descricao": str(m.get("descricao") or "").strip(),
        })
    return prontas


def _perfil(f: dict[str, Any]) -> tuple:
    """Impostos menos a alíquota de ICMS: o que o histórico ensina. A alíquota
    vem da NF - o cadastro tem uma máscara por alíquota (Simples 3,37%, 2,39%...)."""
    return (f["cfop"], f["cst"], f["cst_ipi"], f["cst_pis"], f["cst_cofins"], f["conta"])


def _candidatas_do_perfil(perfil: tuple, mascaras: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cfop, cst, cst_ipi, cst_pis, cst_cofins, conta = perfil
    cand = [m for m in mascaras if m["cfop"] == cfop and m["cst"] == cst and m["cst_ipi"] == cst_ipi]
    if cst_pis:
        cand = [m for m in cand if m["cst_pis"] == cst_pis and m["cst_cofins"] == cst_cofins] or cand
    return [m for m in cand if m["conta"] == conta] or cand


def _mascaras_do_perfil(perfil: tuple, aliquota: float, mascaras: list[dict[str, Any]]) -> tuple:
    """Códigos das máscaras do perfil com esta alíquota (mais de uma = cadastro duplicado)."""
    cand = _candidatas_do_perfil(perfil, mascaras)
    # Máscara sem alíquota usa a da nota.
    escolha = [m for m in cand if m["icms"] == aliquota] or [m for m in cand if not m["icms"]] or cand
    return tuple(sorted(m["codigo"] for m in escolha))


def _operacao_xml(cfop_xml: Any) -> tuple[str, str]:
    """(1º dígito do CFOP de entrada, CFOP de entrada exato quando a operação espelha)."""
    cfop = _digitos(cfop_xml)[:4]
    if len(cfop) != 4:
        return "", ""
    if cfop[0] in "123":
        # Documento já de entrada (importação, NF própria): CFOP é o da entrada.
        return cfop[0], cfop
    prefixo = {"5": "1", "6": "2", "7": "3"}.get(cfop[0], "")
    if not prefixo:
        return "", ""
    return prefixo, (prefixo + cfop[1:]) if cfop[1:] in _SUFIXOS_ESPELHO else ""


def _tem_st(cst_icms: Any, cfop_xml: Any) -> bool | None:
    cst, cfop = _digitos(cst_icms), _digitos(cfop_xml)[:4]
    if not cst and len(cfop) != 4:
        return None
    return cst in _CST_ST or cfop[1:2] == "4"


def _compativel(cfop_entrada: str, prefixo: str, espelho: str, st: bool | None) -> bool:
    if not prefixo:
        return True
    if espelho:
        return cfop_entrada == espelho
    if cfop_entrada[:1] != prefixo or cfop_entrada[1:] in _SUFIXOS_ESPELHO:
        return False
    # Compra com ST entra como 1401/1403/1407; sem ST, não.
    return st is None or (cfop_entrada[1:2] == "4") == st


def _data(valor: Any) -> date | None:
    try:
        return date.fromisoformat(str(valor or "")[:10])
    except ValueError:
        return None


def _nome_familia(nome: str) -> str:
    return re.sub(r"^[A-Z]+\s*-\s*\d+\s*-\s*", "", str(nome or "").strip())


class _Contexto:
    def __init__(self, dados: dict[str, Any], hoje: date | None = None):
        hoje = hoje or date.today()

        def peso(valor) -> float:
            dia = _data(valor)
            return 0.5 ** (max((hoje - dia).days, 0) / _MEIA_VIDA_DIAS) if dia else 1.0

        self.mascaras = _preparar_mascaras(dados.get("mascaras") or [])
        self.por_codigo = {m["codigo"]: m for m in self.mascaras}
        self.familias = {normalizar_produto(k): v for k, v in (dados.get("familias") or {}).items()}
        self._cache: dict[tuple, tuple] = {}
        # Histórico vem do mais recente para o mais antigo.
        self.historico = []
        for linha in dados.get("historico") or []:
            f = _impostos(linha)
            self.historico.append({"produto": normalizar_produto(linha.get("produto")), "cnpj": _digitos(linha.get("cnpj")),
                                   "cfop": f["cfop"], "perfil": _perfil(f), "icms": f["icms"], "n": 1,
                                   "peso": peso(linha.get("dt_lancamento"))})
        self.uso_familia = []
        for linha in dados.get("uso_familia") or []:
            f = _impostos(linha)
            n = int(linha.get("n") or 0)
            # "mes" é o 1º dia do mês; o meio do mês representa melhor a idade.
            mes = _data(linha.get("mes"))
            meio = mes.replace(day=15).isoformat() if mes else None
            self.uso_familia.append({"familia": linha.get("familia") or "", "cfop": f["cfop"], "perfil": _perfil(f),
                                     "icms": f["icms"], "n": n, "peso": n * peso(meio)})
        # Desempate entre máscaras de impostos idênticos: a que mais aparece
        # sozinha no que já foi lançado.
        self.uso_mascara = Counter()
        for linha in self.historico + self.uso_familia:
            grupo = self.resolver(linha["perfil"], linha["icms"])
            if len(grupo) == 1:
                self.uso_mascara[grupo[0]] += linha["peso"]

    def resolver(self, perfil: tuple, aliquota: float) -> tuple:
        chave = (perfil, aliquota)
        if chave not in self._cache:
            self._cache[chave] = _mascaras_do_perfil(perfil, aliquota, self.mascaras)
        return self._cache[chave]

    def ordenar(self, codigos) -> list:
        return sorted(codigos, key=lambda c: (-self.uso_mascara[c], c))

    def mascara(self, codigo) -> dict[str, Any]:
        m = self.por_codigo.get(codigo) or {}
        return {"codigo": codigo, "descricao": m.get("descricao", "")}


def _montar(ctx: _Contexto, linhas: list[dict[str, Any]], aliquota_nf: float, nivel: str, motivo) -> dict[str, Any] | None:
    """linhas: lançamentos. Principal = máscara mais usada, pesando o recente."""
    peso, vezes = Counter(), Counter()
    for linha in linhas:
        perfil, aliquota = linha["perfil"], linha["icms"]
        # Vale a alíquota da NF quando o cadastro tem máscara exata pra ela
        # (Simples muda de alíquota todo mês); senão, a que o lançamento usou
        # (há produto que entra sempre a 7% com NF a 18%).
        if aliquota_nf and any(m["icms"] == aliquota_nf for m in _candidatas_do_perfil(perfil, ctx.mascaras)):
            aliquota = aliquota_nf
        grupo = ctx.resolver(perfil, aliquota)
        if grupo:
            peso[grupo] += linha["peso"]
            vezes[grupo] += linha["n"]
    if not peso:
        return None
    grupos = sorted(peso, key=lambda g: -peso[g])
    ordem = ctx.ordenar(grupos[0])
    principal = ordem[0]
    outras = []
    for grupo in grupos[1:]:
        codigo = ctx.ordenar(grupo)[0]
        if codigo != principal and codigo not in outras:
            outras.append(codigo)
    vezes_principal, total = vezes[grupos[0]], sum(vezes.values())
    return {
        "nivel": nivel,
        "mascara": ctx.mascara(principal),
        "equivalentes": [ctx.mascara(c) for c in ordem[1:_MAX_OUTRAS + 1]],
        "outras": [ctx.mascara(c) for c in outras[:_MAX_OUTRAS]],
        "vezes": vezes_principal,
        "total": total,
        "motivo": motivo(vezes_principal, total),
    }


def sugerir_item(ctx: _Contexto, item: dict[str, Any]) -> dict[str, Any] | None:
    produto = normalizar_produto(item.get("produto"))
    cnpj = _digitos(item.get("cnpj"))
    prefixo, espelho = _operacao_xml(item.get("cfop_xml"))
    st = _tem_st(item.get("cst_icms"), item.get("cfop_xml"))
    aliquota = _num(item.get("icms_aliquota"))

    def compativeis(linhas):
        return [h for h in linhas if _compativel(h["cfop"], prefixo, espelho, st)]

    do_produto = compativeis([h for h in ctx.historico if produto and h["produto"] == produto])
    deste_fornecedor = [h for h in do_produto if cnpj and h["cnpj"] == cnpj]
    niveis = [
        (deste_fornecedor, "alta", lambda v, t: f"Usada em {v} de {t} entradas deste fornecedor"),
        (do_produto, "media", lambda v, t: f"Usada em {v} de {t} entradas do produto, de outros fornecedores"),
    ]
    familia = ctx.familias.get(produto, "")
    if familia:
        nome = _nome_familia(familia)
        da_familia = compativeis([u for u in ctx.uso_familia if u["familia"] == familia])
        niveis.append((da_familia, "baixa", lambda v, t: f"Regra: a mais usada na família {nome}"))
    if espelho:
        do_cfop = [{"perfil": _perfil(m), "icms": m["icms"], "n": 1, "peso": 1.0}
                   for m in ctx.mascaras if m["cfop"] == espelho]
        niveis.append((do_cfop, "baixa", lambda v, t: f"Regra: CFOP {espelho}, pela operação do XML"))
    for linhas, nivel, motivo in niveis:
        sugestao = _montar(ctx, linhas, aliquota, nivel, motivo) if linhas else None
        if sugestao:
            return sugestao
    return None


def sugerir(itens: list[dict[str, Any]], dados: dict[str, Any]) -> dict[str, Any]:
    """itens: [{id, produto, cnpj, cfop_xml, cst_icms, icms_aliquota}] -> {id: sugestão ou None}."""
    ctx = _Contexto(dados)
    return {str(item["id"]): sugerir_item(ctx, item) for item in itens}


def _consultar_bridge(produtos: list[str]) -> tuple[dict[str, Any] | None, str]:
    cfg = _bridge_config()
    if not cfg["api_url"]:
        return None, "nao_configurado"
    try:
        empresa = int(current_app.config.get("ERP_ESTOQUE_PG_COMPANY") or 1)
    except (TypeError, ValueError):
        empresa = 1
    try:
        resp = requests.post(
            f"{cfg['api_url']}/api/erp/fiscal/mascaras-sugestao",
            headers=_headers(cfg),
            json={"empresa": empresa, "produtos": produtos},
            timeout=cfg["timeout"],
        )
        # 404 do próprio bridge = VM sem o endpoint novo; do túnel = bridge fora.
        if resp.status_code == 404 and "ERR_NGROK" not in (resp.text or "")[:2000]:
            return None, "bridge_desatualizado"
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not data.get("sucesso"):
            raise RuntimeError(str((data or {}).get("erro") or "Resposta invalida da API de mascaras."))
    except Exception:
        current_app.logger.warning("Nao foi possivel consultar as mascaras de imposto no GRV.", exc_info=True)
        return None, "bridge_fora"
    return data, ""


def sugerir_para_itens_nota(itens_nota) -> dict[str, Any]:
    """Sugestão para os itens (ItemNota) de uma NF. Nunca levanta erro."""
    itens = [
        {
            "id": i.id,
            "produto": i.codigo_grv or i.codigo,
            "cnpj": i.cnpj_emitente,
            "cfop_xml": i.cfop,
            "cst_icms": i.cst_icms,
            "icms_aliquota": i.icms_aliquota,
        }
        for i in itens_nota
    ]
    produtos = sorted({normalizar_produto(i["produto"]) for i in itens} - {""})
    dados, motivo = _consultar_bridge(produtos)
    if dados is None:
        return {"disponivel": False, "motivo": motivo, "itens": {}}
    return {"disponivel": True, "motivo": "", "itens": sugerir(itens, dados)}
