"""Cliente da API de inventario do CPS (fornecedor do ERP) - columbia.consultoriarf.net.

Contrato (documentacao do fornecedor):
    GET  /inventario/deposito/1?cod_empresa=1&limit=500&offset=N  -> saldo, lotes e versao
    POST /inventario/simular   (chave de LEITURA)   -> valida, nao grava
    POST /inventario/aplicar   (chave de GRAVACAO)  -> grava a contagem no CPS
Header X-API-Key. As chaves ficam SO' no servidor: variaveis de ambiente
INVENTARIO_API_KEY_LEITURA / INVENTARIO_API_KEY_GRAVACAO (e COLUMBIA_API_BASE),
ou instance/inventario_api.json {"base", "chave_leitura", "chave_gravacao"}.
Nunca vao pro navegador nem pro git.
"""
from __future__ import annotations

import json
import os

import requests
from flask import current_app

BASE_PADRAO = "https://columbia.consultoriarf.net"
EMPRESA = 1
DEPOSITO = 1


class ErroApiInventario(Exception):
    """Falha de comunicacao/HTTP com a API do CPS (mensagem pro usuario)."""

    def __init__(self, mensagem: str, status: int | None = None, corpo=None):
        super().__init__(mensagem)
        self.status = status
        self.corpo = corpo


def _config() -> dict:
    arquivo = {}
    try:
        caminho = os.path.join(current_app.instance_path, "inventario_api.json")
        if os.path.isfile(caminho):
            with open(caminho, encoding="utf-8") as fh:
                arquivo = json.load(fh) or {}
    except Exception:
        current_app.logger.warning("Inventario geral: instance/inventario_api.json ilegivel", exc_info=True)
    return {
        "base": (os.environ.get("COLUMBIA_API_BASE") or arquivo.get("base") or BASE_PADRAO).rstrip("/"),
        "leitura": (os.environ.get("INVENTARIO_API_KEY_LEITURA") or arquivo.get("chave_leitura") or "").strip(),
        "gravacao": (os.environ.get("INVENTARIO_API_KEY_GRAVACAO") or arquivo.get("chave_gravacao") or "").strip(),
    }


def configurada(gravacao: bool = False) -> bool:
    cfg = _config()
    return bool(cfg["gravacao"] if gravacao else cfg["leitura"])


def _chamar(metodo: str, rota: str, *, gravacao: bool = False, corpo: dict | None = None, timeout: int = 120):
    cfg = _config()
    chave = cfg["gravacao"] if gravacao else cfg["leitura"]
    if not chave:
        raise ErroApiInventario(
            "Chave da API de inventário não configurada no servidor "
            f"({'INVENTARIO_API_KEY_GRAVACAO' if gravacao else 'INVENTARIO_API_KEY_LEITURA'})."
        )
    try:
        resp = requests.request(
            metodo, cfg["base"] + rota, json=corpo, timeout=timeout,
            headers={"X-API-Key": chave, "Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise ErroApiInventario(f"Não foi possível falar com a API de inventário do ERP: {exc}") from exc
    try:
        dados = resp.json()
    except ValueError:
        dados = resp.text[:2000]
    if resp.status_code >= 400:
        raise ErroApiInventario(f"API de inventário respondeu {resp.status_code}.", resp.status_code, dados)
    return resp.status_code, dados


def listar_deposito() -> list[dict]:
    """Todos os produtos elegiveis do deposito principal (paginado de 500)."""
    produtos, offset = [], 0
    while True:
        _, dados = _chamar("GET", f"/inventario/deposito/{DEPOSITO}?cod_empresa={EMPRESA}&limit=500&offset={offset}")
        if not isinstance(dados, dict):
            raise ErroApiInventario("Resposta inesperada da API de inventário.")
        produtos.extend(dados.get("produtos") or [])
        if not dados.get("tem_mais"):
            return produtos
        offset += 500


def simular(payload: dict):
    """Valida sem gravar (chave de leitura). Devolve (status_http, resposta)."""
    return _chamar("POST", "/inventario/simular", corpo=payload)


def aplicar(payload: dict):
    """Grava no CPS (chave de gravacao). Devolve (status_http, resposta)."""
    return _chamar("POST", "/inventario/aplicar", gravacao=True, corpo=payload)
