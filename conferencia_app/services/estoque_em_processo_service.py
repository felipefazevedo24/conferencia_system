"""Estoque > Produtos em processo (pedido da Logística em 21/11/2026).

É o material mandado para terceiro industrializar: só controle de saldo,
sem mínimo, lote, cobertura nem slow mover. Não deveria ter nada no
depósito 1, só nos outros. A tela sinaliza:
  - item com saldo no depósito 1;
  - item há mais de DIAS_NO_TERCEIRO dias fora do depósito 1.

O saldo da tela de Estoque vem só do depósito 1 (/api/erp/estoque); aqui
vem o de todos os depósitos, pelo catálogo de SQL da bridge
(compras/queries.py).

Há quanto tempo está no terceiro: as entradas de cada depósito (variação
do saldo no kardex) casadas com o saldo de hoje do mais novo para o mais
velho (FIFO: o que voltou é o que foi primeiro). A data da entrada que
fecha o saldo é a da remessa mais antiga ainda lá. Mandou 100 em 01/10 e
voltaram 50: os 50 seguem contando desde 01/10.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..compras import db as grv
from ..compras import queries
from ..compras.config import get_settings
from ..tempo import agora_br

FAMILIA = "PRODUTOS EM PROCESSO"
DEPOSITO_PRINCIPAL = "1"
DIAS_NO_TERCEIRO = 30
# Quanto o kardex volta para achar a remessa. Saldo que as entradas da
# janela não fecham é mais velho que ela.
JANELA_DIAS = 365
EPS = 1e-6


def _nomes_depositos(linhas: list[dict]) -> dict[str, str]:
    """codigo -> nome. Os nomes das colunas da tdeposito não foram
    conferidos: lê o JSON e tenta os candidatos, como o Cardex."""
    nomes = {}
    empresa = get_settings().PG_COD_EMPRESA
    for linha in linhas:
        dep = linha.get("deposito") or {}
        if dep.get("cod_empresa") not in (None, empresa):
            continue
        codigo = str(dep.get("codigo") if dep.get("codigo") is not None else "").strip()
        if not codigo:
            continue
        nomes[codigo] = next(
            (str(dep[k]).strip() for k in ("descricao", "nome", "deposito", "nome_deposito") if str(dep.get(k) or "").strip()),
            "",
        )
    return nomes


def remessa_mais_antiga(saldo: float, entradas: list[tuple[date, float]], inicio_janela: date) -> tuple[date, bool]:
    """(data, exata) da remessa mais antiga que ainda compõe o saldo.
    `entradas` do mais novo para o mais velho. Se as entradas da janela não
    fecham o saldo, parte dele é anterior à janela: devolve o início dela e
    exata=False."""
    acumulado = 0.0
    for data, qtde in entradas:
        acumulado += qtde
        if acumulado >= saldo - EPS:
            return data, True
    return inicio_janela, False


def coletar() -> list[dict[str, Any]]:
    """Linhas da foto da visão, no formato das outras visões, com
    `depositos` (saldo e remessa mais antiga de cada um)."""
    empresa = get_settings().PG_COD_EMPRESA
    produtos = grv.fetch_all(queries.SQL_ESTOQUE_SALDO_DEPOSITOS_FAMILIA, {"cod_empresa": empresa, "familia": FAMILIA})
    try:
        nomes = _nomes_depositos(grv.fetch_all(queries.SQL_ESTOQUE_DEPOSITOS, {}))
    except Exception:  # noqa: BLE001 - sem o nome, mostra só o número
        nomes = {}
    hoje = agora_br().date()
    inicio = hoje - timedelta(days=JANELA_DIAS)
    fora_do_1 = [
        p["cod_produto"] for p in produtos
        if any(str(dep) != DEPOSITO_PRINCIPAL and float(q or 0) > EPS for dep, q in (p.get("saldos") or {}).items())
    ]
    entradas: dict[tuple[str, str], list[tuple[date, float]]] = {}
    if fora_do_1:
        for e in grv.fetch_all(queries.SQL_ESTOQUE_ENTRADAS_DEPOSITOS, {
            "cod_empresa": empresa, "produtos": fora_do_1, "desde": inicio.isoformat(),
        }):
            data = e["data"] if isinstance(e["data"], date) else date.fromisoformat(str(e["data"])[:10])
            entradas.setdefault((str(e["cod_produto"]), str(e["cod_deposito"])), []).append((data, float(e["variacao"] or 0)))

    linhas = []
    for p in produtos:
        depositos = []
        for dep, qtde in sorted((p.get("saldos") or {}).items(), key=lambda kv: (kv[0] != DEPOSITO_PRINCIPAL, kv[0])):
            qtde = float(qtde or 0)
            item = {"codigo": str(dep), "nome": nomes.get(str(dep), ""), "qtde": qtde, "desde": None, "desde_exato": True}
            if str(dep) != DEPOSITO_PRINCIPAL and qtde > EPS:
                desde, exata = remessa_mais_antiga(qtde, entradas.get((str(p["cod_produto"]), str(dep)), []), inicio)
                item["desde"], item["desde_exato"] = desde.isoformat(), exata
            depositos.append(item)
        total = sum(d["qtde"] for d in depositos)
        linhas.append({
            "codigo": str(p.get("codigo_interno") or "").strip(),
            "descricao": str(p.get("item") or "").strip(),
            "familia": str(p.get("familia") or "").strip(),
            "grupo": str(p.get("grupo") or "").strip(),
            "unidade": str(p.get("unidade") or "UN").strip() or "UN",
            "saldo_disponivel": total,
            "saldo_reservado": 0.0,
            "saldo_total": total,
            "estoque_minimo": 0.0,
            "lote_economico": 0.0,
            "localizacoes": [],
            "sem_controle_estoque": False,
            "custo_medio": p.get("custo_medio"),
            "depositos": depositos,
        })
    return linhas


def situacao(linha: dict, hoje: date) -> dict[str, Any]:
    """Campos da tela calculados na leitura (os dias mudam sem nova foto):
    saldo no depósito 1, fora dele e a remessa mais antiga fora dele."""
    no_1 = sum(d["qtde"] for d in linha.get("depositos") or [] if d["codigo"] == DEPOSITO_PRINCIPAL)
    fora = [d for d in linha.get("depositos") or [] if d["codigo"] != DEPOSITO_PRINCIPAL and d["qtde"] > EPS]
    mais_antiga = min(fora, key=lambda d: d["desde"] or "9999", default=None)
    dias = (hoje - date.fromisoformat(mais_antiga["desde"])).days if mais_antiga and mais_antiga["desde"] else None
    if no_1 > EPS:
        nivel, rotulo = "critico", "No depósito 1"
    elif dias is not None and dias > DIAS_NO_TERCEIRO:
        nivel, rotulo = "atencao", f"Mais de {DIAS_NO_TERCEIRO} dias no terceiro"
    elif fora:
        nivel, rotulo = "normal", "No terceiro"
    else:
        nivel, rotulo = "sem_estoque", "Sem saldo"
    return {
        "saldo_deposito1": no_1,
        "saldo_fora": sum(d["qtde"] for d in fora),
        "terceiro_desde": mais_antiga["desde"] if mais_antiga else None,
        "terceiro_desde_exato": bool(mais_antiga and mais_antiga["desde_exato"]),
        "terceiro_dias": dias,
        "nivel": nivel,
        "nivel_label": rotulo,
    }
