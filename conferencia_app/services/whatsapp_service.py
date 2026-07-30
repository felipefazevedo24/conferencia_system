"""Envio profissional de WhatsApp via Meta Cloud API."""
from __future__ import annotations

from typing import Any

import requests
from flask import current_app


def _somente_digitos(valor: str | None) -> str:
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def _normalizar_destino(destino: str | None) -> str:
    numero = _somente_digitos(destino)
    if not numero:
        return ""
    ddd_padrao = str(current_app.config.get("WHATSAPP_DEFAULT_COUNTRY_CODE", "55") or "55").strip()
    ddd_padrao = _somente_digitos(ddd_padrao) or "55"
    if not numero.startswith(ddd_padrao):
        numero = ddd_padrao + numero
    return numero


def enviar_texto_whatsapp(destino: str, mensagem: str, *, contexto: str = "") -> dict[str, Any]:
    """Envia texto simples via Meta WhatsApp Cloud API.

    Retorna dict com chaves: sucesso, status_code, provider_id, erro.
    """
    app = current_app._get_current_object()

    if not app.config.get("WHATSAPP_FOB_ENABLED", False):
        return {"sucesso": False, "ignorado": True, "motivo": "WHATSAPP_FOB_ENABLED=0"}

    provider = str(app.config.get("WHATSAPP_PROVIDER", "META_CLOUD") or "META_CLOUD").strip().upper()
    if provider != "META_CLOUD":
        return {"sucesso": False, "ignorado": True, "motivo": f"Provider nao suportado: {provider}"}

    token = str(app.config.get("WHATSAPP_META_ACCESS_TOKEN") or "").strip()
    phone_number_id = str(app.config.get("WHATSAPP_META_PHONE_NUMBER_ID") or "").strip()
    api_version = str(app.config.get("WHATSAPP_META_API_VERSION") or "v21.0").strip()
    timeout = int(app.config.get("WHATSAPP_TIMEOUT_SECONDS", 15))

    if not token or not phone_number_id:
        return {
            "sucesso": False,
            "ignorado": True,
            "motivo": "Token/PhoneNumberId nao configurados (WHATSAPP_META_ACCESS_TOKEN/WHATSAPP_META_PHONE_NUMBER_ID).",
        }

    destino_norm = _normalizar_destino(destino)
    if not destino_norm:
        return {"sucesso": False, "ignorado": True, "motivo": "Destino sem numero valido."}

    texto = str(mensagem or "").strip()
    if not texto:
        return {"sucesso": False, "ignorado": True, "motivo": "Mensagem vazia."}

    url = f"https://graph.facebook.com/{api_version}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": destino_norm,
        "type": "text",
        "text": {
            "preview_url": False,
            "body": texto,
        },
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        data = resp.json() if resp.content else {}
    except Exception as exc:
        app.logger.warning("WhatsApp (%s): falha de transporte em %s: %s", provider, contexto or "envio", exc)
        return {"sucesso": False, "erro": str(exc)}

    if not resp.ok:
        app.logger.warning(
            "WhatsApp (%s): falha %s em %s -> %s",
            provider,
            resp.status_code,
            contexto or "envio",
            data,
        )
        return {
            "sucesso": False,
            "status_code": resp.status_code,
            "erro": data,
        }

    provider_id = None
    msgs = data.get("messages") if isinstance(data, dict) else None
    if isinstance(msgs, list) and msgs:
        provider_id = msgs[0].get("id")

    app.logger.info(
        "WhatsApp (%s): enviado em %s para %s (msg_id=%s)",
        provider,
        contexto or "envio",
        destino_norm,
        provider_id or "-",
    )
    return {
        "sucesso": True,
        "status_code": resp.status_code,
        "provider": provider,
        "provider_id": provider_id,
        "destino": destino_norm,
    }
