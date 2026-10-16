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
# ST ja faturado continua na tela por esses dias, para a NF aparecer.
_DIAS_FATURADO_VISIVEL = 7
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


def _ajustar_dia_util(dia: date) -> date:
    # A mediana e em dias corridos; a fabrica nao trabalha sabado/domingo.
    if dia.weekday() >= 5:
        dia += timedelta(days=7 - dia.weekday())
    return dia


def _prever_envio(
    pendentes: list[str],
    pronto_desde: date | None,
    hoje: date,
    lead: dict[str, float],
    retorno: date | None = None,
) -> tuple[date, list[dict]]:
    """Soma o tempo historico dos processos que faltam antes do ST.
    Devolve a data de envio e a previsao de termino de cada etapa.

    `retorno`: o item esta em outro fornecedor e volta nessa data. Ai o
    primeiro passo (o ST em andamento) termina no retorno, nao na mediana."""
    if retorno is not None and (not pendentes or "TERCEIRO" in pendentes[0]):
        fim = max(retorno, hoje + timedelta(days=1))
        etapas = [("VOLTA DO FORNECEDOR", fim)]
    elif not pendentes:
        return hoje, []
    else:
        # O processo atual conta a partir de quando o anterior acabou; se ja
        # estourou o tempo normal, assume que termina a qualquer momento.
        fim = (pronto_desde or hoje) + timedelta(days=math.ceil(lead.get(pendentes[0], _DIAS_PADRAO)))
        if fim <= hoje:
            fim = hoje + timedelta(days=1)
        etapas = [(pendentes[0], fim)]
    for servico in pendentes[1:]:
        fim += timedelta(days=math.ceil(lead.get(servico, _DIAS_PADRAO)))
        etapas.append((servico, fim))
    return _ajustar_dia_util(fim), [
        {"processo": p, "fim": _ajustar_dia_util(f).isoformat()} for p, f in etapas
    ]


def _dias_st(lead: dict[str, float]) -> float:
    return next((d for s, d in lead.items() if "TERCEIRO" in s), _DIAS_PADRAO)


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
        # Sem linha na tserv_te (None) a situacao e desconhecida; 0/1 vem do ERP.
        tem_linha = row.get("solicitado") is not None or row.get("enviado") is not None
        solicitado = int(row.get("solicitado") or 0) == 1
        enviado = int(row.get("enviado") or 0) == 1
        dt_envio_nf = _data(row.get("dt_envio"))
        fornecedor = row.get("fornecedor")

        fora = bool(row.get("fora_fornecedor") or row.get("fora_dt_envio")) and not enviado
        if fora and not tem_linha and not pendentes:
            # ST sem linha propria na tserv_te (cod_relaciona solto, ex.: '2'):
            # a peca no fornecedor e este mesmo ST, que ja foi enviado.
            enviado = solicitado = True
            dt_envio_nf = _data(row.get("fora_dt_envio"))
            fornecedor = row.get("fora_fornecedor")
            fora = False

        if enviado:
            # Ja faturado no ERP: fica visivel alguns dias para a Logistica
            # ver a NF; depois disso o material ja saiu.
            if dt_envio_nf is None or (hoje - dt_envio_nf).days > _DIAS_FATURADO_VISIVEL:
                continue

        retorno = None
        if fora:
            retorno = _data(row.get("fora_retorno_previsto"))
            if retorno is None:
                saiu = _data(row.get("fora_dt_envio")) or hoje
                retorno = saiu + timedelta(days=math.ceil(_dias_st(lead)))

        pronto = (not pendentes and not fora) or enviado
        ordem = None
        if not pendentes and not fora:
            # A OC so nasce depois que o processo anterior acaba. A OC do
            # Sync pode ser de um ST anterior do mesmo item; a tserv_te diz
            # se este ST ja foi solicitado.
            if solicitado or not tem_linha:
                ordem = ocs.get(_chave_item(row.get("cod_os_completo")))
            if ordem is not None and (ordem.status == "Expedido" or ordem.expedido_at):
                continue

        pronto_desde = _data(row.get("pronto_desde"))
        envio, etapas = (hoje, []) if pronto else _prever_envio(pendentes, pronto_desde, hoje, lead, retorno)
        nf_sync = (ordem.numero_nf or None) if ordem else None
        dt_faturado = dt_envio_nf or (_data(ordem.faturado_at) if ordem else None)
        itens.append({
            "cod_os_completo": row.get("cod_os_completo"),
            "descricao": row.get("subtitulo"),
            "cliente": row.get("cliente"),
            "pronto": pronto,
            "pronto_desde": pronto_desde.isoformat() if pronto_desde and pronto else None,
            "pendentes": pendentes,
            "atual_iniciado": bool(row.get("atual_iniciado")),
            "dt_envio": envio.isoformat(),
            "etapas": etapas,
            "solicitado": solicitado or ordem is not None,
            "oc": ordem.cod_ordem_compra if ordem else None,
            "fornecedor": fornecedor or (ordem.fornecedor if ordem else None),
            "faturado": enviado or bool(nf_sync),
            "numero_nf": nf_sync,
            "dt_faturado": dt_faturado.isoformat() if dt_faturado else None,
            "no_fornecedor": row.get("fora_fornecedor") if fora else None,
            "retorno_previsto": retorno.isoformat() if retorno else None,
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
