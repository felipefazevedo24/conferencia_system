"""Previsao de envios e coletas de Servico de Terceiro (ST) para a Logistica.

A cadeia real e: processo anterior ao ST termina -> PCP pede o ST -> Compras
emite a OC -> sai a NF e o material -> fornecedor devolve -> PCP finaliza o ST.
O ERP so mostra o roteiro (tpro_pro); a OC e a NF ja estao no Sync (aba ST da
Expedicao). Juntando os dois da pra ver o que vem antes da OC existir.

Nada aqui e gravado: a fase e calculada a cada consulta, entao nunca sai de
sincronia com o ERP.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from ..compras import queries
from ..compras.db import fetch_all
from ..models import ExpedicaoOrdemST
from ..tempo import agora_br

_logger = logging.getLogger(__name__)

# Ordem da tela: do mais urgente para a Logistica ao mais distante.
FASES = {
    "coleta": "Aguardando retorno",
    "oc_emitida": "OC emitida",
    "aguardando_oc": "Aguardando OC",
    "maquina": "Na máquina",
    "fila": "Na fila",
    "depois": "Mais pra frente",
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


def _fase(row: dict, ordem: ExpedicaoOrdemST | None) -> str:
    pendentes = int(row.get("pendentes_antes") or 0)
    if pendentes == 0:
        # A OC so nasce depois que o processo anterior acaba, entao ela so
        # e considerada quando o ST e o proximo da fila.
        if ordem is not None:
            saiu = bool(ordem.numero_nf or ordem.faturado_at or ordem.expedido_at)
            return "coleta" if saiu else "oc_emitida"
        return "aguardando_oc"
    # Segundo ST seguido do mesmo item: espera o primeiro voltar, nao a fabrica.
    if "TERCEIRO" in str(row.get("processo_atual") or "").upper():
        return "depois"
    if pendentes == 1:
        return "maquina" if row.get("atual_iniciado") else "fila"
    return "depois"


def montar_previsao() -> dict:
    hoje = agora_br().date()
    inicio_semana = hoje - timedelta(days=hoje.weekday())
    fim_semana = inicio_semana + timedelta(days=6)

    try:
        rows = fetch_all(queries.SQL_PRODUCAO_ST_PREVISAO, {"cod_empresa": 1})
    except Exception as exc:
        # Integracao com o ERP nunca derruba a tela.
        _logger.warning("st_previsao_erp_indisponivel error=%s", exc)
        return {"itens": [], "erp_indisponivel": True, "hoje": hoje.isoformat()}

    ocs = _ocs_por_item()
    itens = []
    for row in rows:
        ordem = ocs.get(_chave_item(row.get("cod_os_completo")))
        fase = _fase(row, ordem)
        if fase not in ("coleta", "oc_emitida"):
            ordem = None
        prevista = _data(ordem.dt_prevista_entrega) if ordem else None
        pronto_desde = _data(row.get("pronto_desde")) if fase == "aguardando_oc" else None
        itens.append({
            "fase": fase,
            "fase_label": FASES[fase],
            "n_os": row.get("n_os"),
            "cod_os_completo": row.get("cod_os_completo"),
            "descricao": row.get("subtitulo"),
            "cliente": row.get("cliente"),
            "servico_st": row.get("servico_st"),
            "pendentes_antes": int(row.get("pendentes_antes") or 0),
            "processo_atual": row.get("processo_atual"),
            "oc": ordem.cod_ordem_compra if ordem else None,
            "fornecedor": ordem.fornecedor if ordem else None,
            "numero_nf": ordem.numero_nf if ordem else None,
            "dt_prevista_retorno": prevista.isoformat() if prevista else None,
            "retorno_atrasado": bool(fase == "coleta" and prevista and prevista < hoje),
            "pronto_desde": pronto_desde.isoformat() if pronto_desde else None,
        })

    ordem_fase = list(FASES)
    # Dentro de "Aguardando OC", o mais recente primeiro: o antigo tende a
    # ser ST que ja foi e voltou mas nunca foi finalizado no ERP.
    itens.sort(key=lambda i: i["pronto_desde"] or "", reverse=True)
    itens.sort(key=lambda i: (ordem_fase.index(i["fase"]), i["dt_prevista_retorno"] or "9999"))

    coletas = [i for i in itens if i["fase"] == "coleta"]
    resumo = {
        "coletas_atrasadas": sum(1 for i in coletas if i["retorno_atrasado"]),
        "coletas_semana": sum(
            1 for i in coletas
            if i["dt_prevista_retorno"] and inicio_semana.isoformat() <= i["dt_prevista_retorno"] <= fim_semana.isoformat()
        ),
        "a_enviar": sum(1 for i in itens if i["fase"] in ("oc_emitida", "aguardando_oc")),
        "na_maquina": sum(1 for i in itens if i["fase"] == "maquina"),
    }
    return {
        "itens": itens,
        "resumo": resumo,
        "fases": FASES,
        "erp_indisponivel": False,
        "hoje": hoje.isoformat(),
    }
