"""Consulta o saldo de estoque do GRV via API bridge do ERP.

Usado para comparar o inventario fisico feito pela Logistica (Modulo de
Inventario) com o saldo que o GRV tem registrado - APENAS na tela de
consulta/revisao. A contagem em si (Novo Inventario) nunca deve enxergar
esse valor, para nao vesar a contagem (mesma logica da conferencia cega
usada no resto do sistema)."""
from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import requests
from flask import current_app

_CACHE: dict[str, Any] = {"dados": None, "expira_em": 0.0}
_CACHE_TTL_SEGUNDOS = 300


def _bridge_config() -> dict[str, Any]:
    app = current_app._get_current_object()
    api_url = (
        os.environ.get("ERP_LANCAMENTO_API_URL")
        or app.config.get("ERP_LANCAMENTO_API_URL")
        or ""
    )
    api_token = (
        os.environ.get("ERP_LANCAMENTO_API_TOKEN")
        or app.config.get("ERP_LANCAMENTO_API_TOKEN")
        or ""
    )
    try:
        timeout = int(
            os.environ.get("ERP_LANCAMENTO_API_TIMEOUT")
            or app.config.get("ERP_LANCAMENTO_API_TIMEOUT")
            or 30
        )
    except (TypeError, ValueError):
        timeout = 30
    return {
        "api_url": str(api_url or "").strip().rstrip("/"),
        "api_token": str(api_token or ""),
        "timeout": timeout,
    }


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/Inventario-Estoque",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    return headers


def buscar_estoque_grv(empresa: int = 1, forcar_atualizacao: bool = False) -> dict[str, Any]:
    """Retorna o saldo do GRV, cacheado por alguns minutos (a consulta traz
    o estoque inteiro de uma vez, entao nao vale a pena bater a cada request).

    Formato do retorno:
        {
            "por_local": {"CODIGO|LOCAL": item_bruto, ...},
            "por_codigo": {"CODIGO": {"qtde_total", "qtde_disponivel",
                "qtde_reservada", "unidade", "item", "localizacoes": [...]}},
        }
    """
    agora = time.monotonic()
    if not forcar_atualizacao and _CACHE["dados"] is not None and agora < _CACHE["expira_em"]:
        return _CACHE["dados"]

    cfg = _bridge_config()
    if not cfg["api_url"]:
        raise ValueError("ERP_LANCAMENTO_API_URL nao configurada para consultar estoque no GRV.")

    resp = requests.post(
        f"{cfg['api_url']}/api/erp/estoque",
        headers=_headers(cfg),
        json={"empresa": empresa},
        timeout=cfg["timeout"],
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data.get("sucesso"):
        raise RuntimeError(str((data or {}).get("erro") or "Resposta invalida da API de estoque."))

    por_local: dict[str, Any] = {}
    por_codigo: dict[str, Any] = {}
    for item in data.get("itens") or []:
        codigo = str(item.get("codigo_interno") or "").strip().upper()
        if not codigo:
            continue
        local = str(item.get("localizacao_estoque") or "").strip().upper()
        if local:
            por_local[f"{codigo}|{local}"] = item

        agregado = por_codigo.setdefault(codigo, {
            "qtde_total": 0.0,
            "qtde_disponivel": 0.0,
            "qtde_reservada": 0.0,
            "item": item.get("item") or "",
            "unidade": item.get("unidade") or "",
            "localizacoes": [],
        })
        agregado["qtde_total"] += float(item.get("qtde_total") or 0)
        agregado["qtde_disponivel"] += float(item.get("qtde_disponivel") or 0)
        agregado["qtde_reservada"] += float(item.get("qtde_reservada") or 0)
        if local:
            agregado["localizacoes"].append(local)

    resultado = {"por_local": por_local, "por_codigo": por_codigo}
    _CACHE["dados"] = resultado
    _CACHE["expira_em"] = agora + _CACHE_TTL_SEGUNDOS
    return resultado


class LocalizacaoEstoqueNaoEncontrada(Exception):
    """codigo_interno nao encontrado na tabela tproduto do ERP (HTTP 404)."""


def atualizar_localizacao_estoque(codigo_interno: str, localizacao_estoque: str) -> dict:
    """Atualiza a localizacao de estoque (rua/prateleira) de um produto
    direto no ERP: PATCH .../producao/estoque/{codigo_interno}/localizacao,
    body {"localizacao_estoque": "..."}.

    Chamada sempre que o Modulo de Inventario registra ou recontа um item
    com uma localizacao - sem isso, o campo no ERP fica desatualizado e
    `qtde_grv_para` pode nao conseguir casar item+local (cai no fallback:
    total agregado do codigo em todas as localizacoes, que aparenta
    "quantidade de GRV errada" pra quem espera o saldo so daquele local)."""
    codigo_interno = str(codigo_interno or "").strip()
    localizacao_estoque = str(localizacao_estoque or "").strip()
    if not codigo_interno:
        raise ValueError("codigo_interno é obrigatório.")
    if not localizacao_estoque:
        raise ValueError("localizacao_estoque é obrigatório.")

    app = current_app._get_current_object()
    base_url = str(
        os.environ.get("INVENTARIO_LOCALIZACAO_API_URL")
        or app.config.get("INVENTARIO_LOCALIZACAO_API_URL")
        or ""
    ).strip().rstrip("/")
    if not base_url:
        raise ValueError("INVENTARIO_LOCALIZACAO_API_URL nao configurada.")
    try:
        timeout = int(
            os.environ.get("INVENTARIO_LOCALIZACAO_API_TIMEOUT")
            or app.config.get("INVENTARIO_LOCALIZACAO_API_TIMEOUT")
            or 30
        )
    except (TypeError, ValueError):
        timeout = 30

    url = f"{base_url}/{quote(codigo_interno, safe='')}/localizacao"
    resp = requests.patch(
        url,
        headers={"Content-Type": "application/json"},
        json={"localizacao_estoque": localizacao_estoque},
        timeout=timeout,
    )
    if resp.status_code == 404:
        raise LocalizacaoEstoqueNaoEncontrada(f"Código interno '{codigo_interno}' não encontrado no ERP (tproduto).")
    if resp.status_code == 422:
        detalhe = ""
        try:
            detalhe = str(resp.json())
        except ValueError:
            pass
        raise ValueError(f"Localização de estoque inválida ou não informada.{(' ' + detalhe) if detalhe else ''}")
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def qtde_grv_para(codigo_produto: str, local_codigo: str, estoque: dict[str, Any]) -> float | None:
    """Resolve a quantidade do GRV para um item do inventario: tenta casar
    por codigo+local primeiro (mais preciso); se o local nao bater com o
    formato do GRV, cai no total agregado do codigo em todas as localizacoes.
    Retorna None se o codigo simplesmente nao existir no GRV."""
    codigo = str(codigo_produto or "").strip().upper()
    local = str(local_codigo or "").strip().upper()
    if not codigo:
        return None

    chave_local = f"{codigo}|{local}"
    if local and chave_local in estoque.get("por_local", {}):
        return float(estoque["por_local"][chave_local].get("qtde_total") or 0)

    agregado = estoque.get("por_codigo", {}).get(codigo)
    if agregado is None:
        return None
    return float(agregado.get("qtde_total") or 0)
