"""Busca o endereco do cliente no ERP (tcliente) via bridge, pra montar a
etiqueta de expedicao (Identificacao de Volume) - o Sync so guarda o NOME
do cliente na ordem de faturamento, nao o endereco, entao a consulta e'
feita ao vivo por nome (melhor match) na hora de gerar a etiqueta. Se o
bridge estiver indisponivel ou nao achar o cliente, a etiqueta ainda e'
gerada normalmente - so sai sem endereco preenchido (nao trava a impressao)."""
from __future__ import annotations

import os
from typing import Any

import requests
from flask import current_app


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
            or 15
        )
    except (TypeError, ValueError):
        timeout = 15
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
        "User-Agent": "ColumbiaSync/Expedicao-Etiqueta",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    return headers


def montar_endereco_formatado(cliente: dict[str, Any] | None) -> list[str]:
    """Formata o endereco em ate 2 linhas prontas pra etiqueta:
    'Rua X, 123 - Bairro' e 'Cidade UF - CEP 12345-678'. Pula partes vazias
    sem deixar traco/virgula sobrando."""
    if not cliente:
        return []

    rua = str(cliente.get("endereco") or "").strip()
    numero = str(cliente.get("numero") or "").strip()
    complemento = str(cliente.get("complemento") or "").strip()
    bairro = str(cliente.get("bairro") or "").strip()
    cidade = str(cliente.get("cidade") or "").strip()
    uf = str(cliente.get("uf") or "").strip()
    cep = str(cliente.get("cep") or "").strip()
    if len(cep) == 8:
        cep = f"{cep[:5]}-{cep[5:]}"

    linha1_partes = []
    if rua:
        linha1_partes.append(f"{rua}, {numero}" if numero else rua)
    if complemento:
        linha1_partes.append(complemento)
    linha1 = " - ".join(linha1_partes)
    if bairro:
        linha1 = f"{linha1} - {bairro}" if linha1 else bairro

    linha2_partes = []
    if cidade or uf:
        linha2_partes.append(" ".join(p for p in (cidade, uf) if p))
    if cep:
        linha2_partes.append(f"CEP: {cep}")
    linha2 = " - ".join(linha2_partes)

    return [linha for linha in (linha1, linha2) if linha]


def buscar_endereco_cliente(nome_cliente: str) -> dict[str, Any] | None:
    """Busca o cliente no ERP por nome (melhor match). Retorna o dict cru
    do bridge (codigo/nome/endereco/numero/complemento/bairro/cidade/uf/cep)
    ou None se nao encontrar/bridge indisponivel."""
    nome = (nome_cliente or "").strip()
    if not nome:
        return None

    cfg = _bridge_config()
    if not cfg["api_url"]:
        return None

    try:
        resp = requests.post(
            f"{cfg['api_url']}/api/erp/cliente-endereco",
            headers=_headers(cfg),
            json={"nome": nome},
            timeout=cfg["timeout"],
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:  # noqa: BLE001
        current_app.logger.warning(
            "Falha ao consultar endereco do cliente no ERP (nome=%s)", nome, exc_info=True
        )
        return None

    if not isinstance(data, dict) or not data.get("sucesso") or not data.get("encontrado"):
        return None
    cliente = data.get("cliente")
    return cliente if isinstance(cliente, dict) else None
