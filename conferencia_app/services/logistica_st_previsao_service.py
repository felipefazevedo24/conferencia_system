"""Previsao de envios de Servico de Terceiro (ST) para a Logistica.

A cadeia real e: processo anterior ao ST termina -> PCP pede o ST -> Compras
emite a OC -> sai a NF e o material -> fornecedor devolve -> PCP finaliza o ST.
O ERP so mostra o roteiro (tpro_pro); a OC e a NF ja estao no Sync (aba ST da
Expedicao). Juntando os dois da pra ver o que vai sair antes da OC existir.

Nada aqui e gravado: o grupo e calculado a cada consulta, entao nunca sai de
sincronia com o ERP.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime

from ..compras import queries
from ..compras.db import fetch_all
from ..models import ExpedicaoOrdemST

_logger = logging.getLogger(__name__)

# Ordem da tela: do mais urgente ao mais distante.
GRUPOS = {
    "enviar": "Enviar",
    "maquina": "Na máquina",
    "programado": "Programado",
}

_RE_OS_ITEM = re.compile(r"^\s*0*(\d+)\s*/\s*0*(\d+)")


def _chave_item(texto) -> tuple[str, int] | None:
    """'10089/002-1' e '10089/002' viram ('10089', 2) - o sufixo e o zero a
    esquerda variam entre a API de ST e o tos_aux."""
    m = _RE_OS_ITEM.match(str(texto or ""))
    return (m.group(1), int(m.group(2))) if m else None


def _data(valor) -> date | None:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    # Pela bridge a data chega como texto ISO.
    try:
        return date.fromisoformat(str(valor or "")[:10])
    except ValueError:
        return None


def _ocs_por_item() -> dict[tuple[str, int], ExpedicaoOrdemST]:
    """OC ST mais recente de cada item de OS. Item que ja passou por outro ST
    antes pode ter OC antiga; a mais nova e a que vale para o ST pendente."""
    ordens = (
        ExpedicaoOrdemST.query.filter(ExpedicaoOrdemST.excluido.is_(False))
        .order_by(ExpedicaoOrdemST.created_at)
        .all()
    )
    mapa: dict[tuple[str, int], ExpedicaoOrdemST] = {}
    for ordem in ordens:
        for item in ordem.itens:
            chave = _chave_item(item.cod_interno)
            if chave:
                mapa[chave] = ordem
    return mapa


def _grupo(row: dict, ordem: ExpedicaoOrdemST | None) -> str | None:
    """None = ja saiu (tem NF), fica fora da tela."""
    pendentes = int(row.get("pendentes_antes") or 0)
    if pendentes == 0:
        # A OC so nasce depois que o processo anterior acaba, entao ela so
        # e considerada quando o ST e o proximo da fila.
        if ordem is not None and (ordem.numero_nf or ordem.faturado_at or ordem.expedido_at):
            return None
        return "enviar"
    # Segundo ST seguido do mesmo item: espera o primeiro voltar, nao a fabrica.
    if "TERCEIRO" in str(row.get("processo_atual") or "").upper():
        return "programado"
    if pendentes == 1 and row.get("atual_iniciado"):
        return "maquina"
    return "programado"


def montar_previsao() -> dict:
    try:
        rows = fetch_all(queries.SQL_PRODUCAO_ST_PREVISAO, {"cod_empresa": 1})
    except Exception as exc:
        # Integracao com o ERP nunca derruba a tela.
        _logger.warning("st_previsao_erp_indisponivel error=%s", exc)
        return {"itens": [], "grupos": GRUPOS, "erp_indisponivel": True}

    ocs = _ocs_por_item()
    itens = []
    for row in rows:
        pendentes = int(row.get("pendentes_antes") or 0)
        ordem = ocs.get(_chave_item(row.get("cod_os_completo"))) if pendentes == 0 else None
        grupo = _grupo(row, ordem)
        if grupo is None:
            continue
        pronto_desde = _data(row.get("pronto_desde")) if grupo == "enviar" else None
        itens.append({
            "grupo": grupo,
            "cod_os_completo": row.get("cod_os_completo"),
            "descricao": row.get("subtitulo"),
            "cliente": row.get("cliente"),
            "pendentes_antes": pendentes,
            "processo_atual": row.get("processo_atual"),
            "pronto_desde": pronto_desde.isoformat() if pronto_desde else None,
            "oc": ordem.cod_ordem_compra if ordem else None,
            "fornecedor": ordem.fornecedor if ordem else None,
        })

    # Mais recente primeiro: em "Enviar", o antigo tende a ser ST que ja foi
    # e voltou mas nunca foi finalizado no ERP.
    ordem_grupo = list(GRUPOS)
    itens.sort(key=lambda i: i["pronto_desde"] or "", reverse=True)
    itens.sort(key=lambda i: (ordem_grupo.index(i["grupo"]), i["pendentes_antes"]))
    return {"itens": itens, "grupos": GRUPOS, "erp_indisponivel": False}
