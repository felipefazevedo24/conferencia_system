"""Trilha de uma ordem de faturamento no GRV, pra Conferência de Expedição.

O GRV não grava quem criou a OF: `tordem_fat` não tem coluna de usuário e o
histórico de alterações dele não cobre essa tabela (conferido com a OF 1724
em 13/10/2026). O que dá pra mostrar é quem mexeu em volta dela, e é isso que
o bridge devolve (/api/erp/expedicao/trilha-ordem-fat): os estágios do
orçamento e os movimentos de kardex dos itens que citam o orçamento ou a NF
(solicitação de material, entrada de produção, emissão da nota).

Nada é gravado no Sync: a trilha é estado do GRV, lida na hora. E nunca
derruba a tela: se o bridge não responder, volta vazio com o motivo.
"""
from __future__ import annotations

import re
from typing import Any

import requests
from flask import current_app

from .erp_estoque_service import _bridge_config, _headers

# tabela_link do kardex -> como o evento aparece na tela.
_TIPOS_MOVIMENTO = {
    "TSOL_MAT": ("reserva", "Solicitação de material"),
    "TOS": ("producao", "Entrada de produção"),
    "TNOTA_FISCAL": ("nf", "Nota fiscal emitida"),
    "TORCAMENTO": ("orcamento", "Movimento do orçamento"),
}


def _vazio(motivo: str) -> dict[str, Any]:
    return {"disponivel": False, "motivo": motivo, "eventos": []}


def montar_eventos(trilha: dict[str, Any]) -> list[dict[str, Any]]:
    """Eventos em ordem cronológica: {tipo, titulo, detalhe, usuario, data, sem_hora}."""
    eventos = []
    for estagio in trilha.get("estagios") or []:
        eventos.append({
            "tipo": "orcamento",
            "titulo": f"Orçamento {estagio.get('orcamento') or ''}: {str(estagio.get('estagio') or '').strip().capitalize()}".strip(),
            "detalhe": "",
            "usuario": str(estagio.get("usuario") or "").strip(),
            "data": estagio.get("data"),
            "sem_hora": False,
        })
    for mov in trilha.get("movimentos") or []:
        tabela = str(mov.get("tabela") or "").strip().upper()
        tipo, titulo = _TIPOS_MOVIMENTO.get(tabela, ("outro", tabela or "Movimento de estoque"))
        obs = str(mov.get("obs") or "").strip()
        if tipo == "nf" and trilha.get("numero_nf"):
            titulo = f"Nota fiscal {trilha['numero_nf']} emitida"
            obs = ""
        elif tipo == "producao":
            # "1;17857" = empresa;código da OS.
            cod_os = str(mov.get("chave") or "").split(";")[-1]
            titulo = f"Entrada de produção (OS {cod_os})" if cod_os else titulo
        itens = int(mov.get("itens") or 0)
        if itens > 1:
            obs = f"{obs} · {itens} itens da ordem".strip(" ·")
        eventos.append({
            "tipo": tipo,
            "titulo": titulo,
            # O kardex grava a observação toda em maiúsculas.
            "detalhe": re.sub(r"\s+", " ", obs).capitalize(),
            "usuario": str(mov.get("usuario") or "").strip(),
            "data": mov.get("data"),
            "sem_hora": False,
        })
    if trilha.get("dt_solicitacao_fat"):
        eventos.append({
            "tipo": "solicitacao",
            "titulo": "Solicitação de faturamento",
            "detalhe": "O GRV guarda só a data da ordem de faturamento, sem hora e sem usuário.",
            "usuario": "",
            "data": trilha["dt_solicitacao_fat"],
            "sem_hora": True,
        })
    # Evento sem hora vai pro fim do dia dele: a solicitação de faturamento vem
    # depois da produção do mesmo dia, não antes.
    eventos.sort(key=lambda e: (str(e["data"] or "")[:10], e["sem_hora"], str(e["data"] or "")))
    return eventos


def buscar_trilha_grv(cod_ordem_fat: int) -> dict[str, Any]:
    cfg = _bridge_config()
    if not cfg["api_url"]:
        return _vazio("nao_configurado")
    try:
        empresa = int(current_app.config.get("ERP_ESTOQUE_PG_COMPANY") or 1)
    except (TypeError, ValueError):
        empresa = 1
    try:
        resp = requests.post(
            f"{cfg['api_url']}/api/erp/expedicao/trilha-ordem-fat",
            headers=_headers(cfg),
            json={"empresa": empresa, "cod_ordem_fat": int(cod_ordem_fat)},
            timeout=cfg["timeout"],
        )
        # 404 do próprio bridge = VM sem o endpoint novo; do túnel = bridge fora.
        if resp.status_code == 404 and "ERR_NGROK" not in (resp.text or "")[:2000]:
            return _vazio("bridge_desatualizado")
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict) or not data.get("sucesso"):
            raise RuntimeError(str((data or {}).get("erro") or "Resposta invalida da API de trilha."))
    except Exception:
        current_app.logger.warning("Nao foi possivel consultar a trilha da OF %s no GRV.", cod_ordem_fat, exc_info=True)
        return _vazio("bridge_fora")
    if not data.get("encontrada"):
        return _vazio("nao_encontrada")
    trilha = data.get("trilha") or {}
    return {"disponivel": True, "motivo": "", "numero_nf": trilha.get("numero_nf") or "", "eventos": montar_eventos(trilha)}
