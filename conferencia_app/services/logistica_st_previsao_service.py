"""Previsao de envios de Servico de Terceiro (ST) para a Logistica.

A cadeia real e: processo anterior ao ST termina -> PCP pede o ST -> Compras
emite a OC -> sai a NF e o material -> fornecedor devolve -> PCP finaliza o ST.
O ERP so mostra o roteiro (tpro_pro); a OC e a NF ja estao no Sync (aba ST da
Expedicao). Juntando os dois da pra ver o que vai sair antes da OC existir.

A data de envio e estimada pelo tempo historico de cada processo que falta
antes do ST, nao pela data prevista do PCP (que nao acompanha o atraso real
da producao). Nada aqui e gravado: tudo e recalculado a cada consulta.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from datetime import date, datetime, timedelta

from ..compras import queries
from ..compras.db import fetch_all
from ..models import ExpedicaoOrdemST
from ..tempo import agora_br

_logger = logging.getLogger(__name__)

_RE_OS_ITEM = re.compile(r"^\s*0*(\d+)\s*/\s*0*(\d+)")

# Processo sem historico suficiente (menos de 5 passagens em 180 dias).
_DIAS_PADRAO = 3.0
# O tempo historico muda devagar; a consulta varre a tpro_pro inteira.
_LEAD_TIME_TTL = 6 * 3600
_lead_time_cache: dict = {"em": 0.0, "dias": {}}
_lead_time_lock = threading.Lock()


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


def _lead_times() -> dict[str, float]:
    with _lead_time_lock:
        if _lead_time_cache["dias"] and time.time() - _lead_time_cache["em"] < _LEAD_TIME_TTL:
            return _lead_time_cache["dias"]
    try:
        rows = fetch_all(queries.SQL_PRODUCAO_ST_LEAD_TIME, {"cod_empresa": 1})
    except Exception as exc:
        # Sem historico a tela ainda funciona, so com o tempo padrao.
        _logger.warning("st_previsao_lead_time_indisponivel error=%s", exc)
        return _lead_time_cache["dias"]
    dias = {str(r["servico"]): float(r["mediana_dias"] or 0) for r in rows if r.get("servico")}
    with _lead_time_lock:
        _lead_time_cache.update(em=time.time(), dias=dias)
    return dias


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


def _prever_envio(pendentes: list[str], pronto_desde: date | None, hoje: date, lead: dict[str, float]) -> date:
    """Soma o tempo historico dos processos que faltam antes do ST."""
    if not pendentes:
        return hoje
    dias = [lead.get(s, _DIAS_PADRAO) for s in pendentes]
    # O processo atual conta a partir de quando o anterior acabou; se ja
    # estourou o tempo normal, assume que termina a qualquer momento.
    fim = (pronto_desde or hoje) + timedelta(days=math.ceil(dias[0]))
    if fim <= hoje:
        fim = hoje + timedelta(days=1)
    fim += timedelta(days=math.ceil(sum(dias[1:])))
    # A mediana e em dias corridos; a fabrica nao trabalha sabado/domingo.
    if fim.weekday() >= 5:
        fim += timedelta(days=7 - fim.weekday())
    return fim


def montar_previsao() -> dict:
    hoje = agora_br().date()
    try:
        rows = fetch_all(queries.SQL_PRODUCAO_ST_PREVISAO, {"cod_empresa": 1})
    except Exception as exc:
        # Integracao com o ERP nunca derruba a tela.
        _logger.warning("st_previsao_erp_indisponivel error=%s", exc)
        return {"itens": [], "hoje": hoje.isoformat(), "erp_indisponivel": True}

    lead = _lead_times()
    ocs = _ocs_por_item()
    itens = []
    for row in rows:
        pendentes = [str(s) for s in (row.get("pendentes_lista") or []) if s]
        ordem = None
        if not pendentes:
            # A OC so nasce depois que o processo anterior acaba, entao ela so
            # e considerada quando o ST e o proximo da fila.
            ordem = ocs.get(_chave_item(row.get("cod_os_completo")))
            if ordem is not None and (ordem.numero_nf or ordem.faturado_at or ordem.expedido_at):
                continue  # ja saiu
        pronto_desde = _data(row.get("pronto_desde"))
        envio = _prever_envio(pendentes, pronto_desde, hoje, lead)
        itens.append({
            "cod_os_completo": row.get("cod_os_completo"),
            "descricao": row.get("subtitulo"),
            "cliente": row.get("cliente"),
            "pronto": not pendentes,
            "pronto_desde": pronto_desde.isoformat() if pronto_desde and not pendentes else None,
            "pendentes": pendentes,
            "atual_iniciado": bool(row.get("atual_iniciado")),
            "dt_envio": envio.isoformat(),
            "oc": ordem.cod_ordem_compra if ordem else None,
            "fornecedor": ordem.fornecedor if ordem else None,
        })

    # Prontos: mais recente primeiro - o antigo tende a ser ST que ja foi e
    # voltou mas nunca foi finalizado no ERP.
    itens.sort(key=lambda i: i["pronto_desde"] or "", reverse=True)
    itens.sort(key=lambda i: i["dt_envio"])
    return {
        "itens": itens,
        "hoje": hoje.isoformat(),
        "erp_indisponivel": False,
        "sem_historico": not lead,
    }
