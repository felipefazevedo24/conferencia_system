"""Cliente HTTP para a API do emitente (FastAPI) que atualiza a ordem de
faturamento no ERP deles com os dados capturados na conferencia de expedicao
(peso liquido/bruto, quantidade/especie de volumes, liberacao pro
faturamento).

Importante: o emitente pediu explicitamente que o Sync NUNCA conecte direto
no banco deles - toda alteracao tem que ir por essa API (PATCH
/ordem-faturamento/{codigo}), nunca por SQL direto.

O envio e assincrono e tolerante a falha (mesmo padrao de teams_service.py):
uma falha aqui nunca deve derrubar o salvamento local da conferencia.
"""
from __future__ import annotations

import os
import threading
from typing import Any

import requests
from flask import current_app

# URL base combinada com o emitente.
# O endpoint final esperado e: /producao/ordem-faturamento/{codigo}
#
# Pode ser sobreposta por variavel de ambiente:
# - ERP_ORDEM_FAT_API_URL
# - ERP_ORDEM_FAT_TERCEIRO_API_URL
#
# A variavel pode apontar para:
# 1) base da API (ex.: https://host/producao)
# 2) prefixo do recurso (ex.: https://host/producao/ordem-faturamento)
# 3) URL completa com placeholder (ex.: https://host/producao/ordem-faturamento/{codigo})
_URL_BASE_PADRAO = "https://columbia.consultoriarf.net/producao"
_URL_BASE_TERCEIRO_PADRAO = "https://columbia.consultoriarf.net/producao"


def _url_base(*, terceiro: bool = False) -> str:
    env_key = "ERP_ORDEM_FAT_TERCEIRO_API_URL" if terceiro else "ERP_ORDEM_FAT_API_URL"
    default_url = _URL_BASE_TERCEIRO_PADRAO if terceiro else _URL_BASE_PADRAO
    url = str(os.environ.get(env_key, "") or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        # Aceita tanto HTTP quanto HTTPS (conforme ambiente da API FastAPI).
        # Se vier sem esquema, ignora a config invalida e usa o padrao.
        try:
            current_app.logger.warning(
                "%s configurada sem esquema http/https (%s); usando URL padrao.", env_key, url
            )
        except Exception:
            pass
        url = ""
    return (url or default_url).rstrip("/")


def _url_destino(cod_ordem_fat: int, *, terceiro: bool = False) -> str:
    """Monta URL final da ordem com tolerancia a formatos legados de config."""
    base = _url_base(terceiro=terceiro).rstrip("/")

    # Permite configurar a URL completa com placeholder.
    if "{codigo}" in base:
        return base.format(codigo=cod_ordem_fat)

    # Se a config ja apontar para o recurso, so concatena o codigo.
    if base.endswith("/ordem-faturamento"):
        return f"{base}/{cod_ordem_fat}"

    # Padrao oficial: /producao/ordem-faturamento/{codigo}
    return f"{base}/ordem-faturamento/{cod_ordem_fat}"


def _to_numero(valor: Any):
    """Converte string BR ("150,50") ou numero pra int/float pro JSON.
    Devolve None se nao for um numero valido (o campo entao nao e enviado)."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return valor
    texto = str(valor).strip().replace(",", ".")
    if not texto:
        return None
    try:
        numero = float(texto)
    except ValueError:
        return None
    return int(numero) if numero.is_integer() else numero


def _montar_payload(
    *,
    especie_volumes: str | None,
    qtde_volumes: Any,
    peso_liquido: Any,
    peso_bruto: Any,
    liberado_para_faturamento: int | None,
) -> dict:
    payload: dict[str, Any] = {}
    especie = (especie_volumes or "").strip()
    if especie:
        payload["especie_volumes"] = especie
    qtde_num = _to_numero(qtde_volumes)
    if qtde_num is not None:
        payload["qtde_volumes"] = qtde_num
    peso_liq_num = _to_numero(peso_liquido)
    if peso_liq_num is not None:
        payload["peso_liquido"] = peso_liq_num
    peso_bru_num = _to_numero(peso_bruto)
    if peso_bru_num is not None:
        payload["peso_bruto"] = peso_bru_num
    if liberado_para_faturamento is not None:
        payload["liberado_para_faturamento"] = int(liberado_para_faturamento)
    return payload


def _enviar_async(app, url: str, payload: dict, cod_ordem_fat) -> None:
    with app.app_context():
        try:
            resp = requests.patch(url, json=payload, timeout=10)
            if resp.status_code == 404:
                app.logger.warning(
                    "Ordem-faturamento: API do emitente nao encontrou o codigo %s (404).",
                    cod_ordem_fat,
                )
                return
            resp.raise_for_status()
            app.logger.info(
                "Ordem-faturamento %s atualizada na API do emitente (%s).",
                cod_ordem_fat, list(payload.keys()),
            )
        except Exception as exc:  # pragma: no cover - best effort, nunca quebra o fluxo local
            app.logger.warning(
                "Falha ao atualizar ordem-faturamento %s na API do emitente: %s",
                cod_ordem_fat, exc,
            )


def atualizar_ordem_faturamento(
    cod_ordem_fat: int,
    *,
    especie_volumes: str | None = None,
    qtde_volumes: Any = None,
    peso_liquido: Any = None,
    peso_bruto: Any = None,
    liberado_para_faturamento: int | None = None,
    terceiro: bool = False,
) -> None:
    """Dispara (assincrono) um PATCH para a API do emitente com os campos
    informados dessa ordem de faturamento. So entra no corpo o que for
    passado - update parcial, igual a API deles espera. Nunca levanta
    excecao no caminho principal (best-effort)."""
    payload = _montar_payload(
        especie_volumes=especie_volumes,
        qtde_volumes=qtde_volumes,
        peso_liquido=peso_liquido,
        peso_bruto=peso_bruto,
        liberado_para_faturamento=liberado_para_faturamento,
    )
    if not payload:
        return

    app = current_app._get_current_object()
    url = _url_destino(cod_ordem_fat, terceiro=terceiro)
    threading.Thread(target=_enviar_async, args=(app, url, payload, cod_ordem_fat), daemon=True).start()
