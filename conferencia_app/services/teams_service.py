"""Notificacoes para canal do Teams via webhook (Power Automate / Workflows).

O webhook aceita Adaptive Card no formato {"type":"message","attachments":[...]}.
A URL contem uma assinatura secreta, entao NAO fica no git: e lida de
  1) variavel de ambiente TEAMS_WEBHOOK_EXPEDICAO_URL, ou
  2) instance/teams_config.json  ({"webhook_expedicao": "https://..."}).

Os envios sao assincronos e nunca quebram o fluxo principal.
"""
from __future__ import annotations

import json
import os
import threading

import requests
from flask import current_app

# Texto da mencao de canal (o Teams resolve via msteams.entities abaixo).
_MENTION_TEXT = "<at>Canal</at>"


def _webhook_url(env_var: str = "TEAMS_WEBHOOK_EXPEDICAO_URL", config_key: str = "webhook_expedicao") -> str:
    url = str(os.environ.get(env_var, "") or "").strip()
    if url:
        return url
    try:
        path = os.path.join(current_app.instance_path, "teams_config.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return str((json.load(fh) or {}).get(config_key) or "").strip()
    except Exception:
        return ""
    return ""


def _card_payload(titulo: str, linha_principal: str, subinfo: str | None = None, mencionar_canal: bool = False) -> dict:
    body = []
    content = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if mencionar_canal:
        body.append({"type": "TextBlock", "size": "Small", "isSubtle": True, "wrap": True, "text": f"{_MENTION_TEXT}"})
        content["msteams"] = {
            "entities": [
                {"type": "mention", "text": _MENTION_TEXT, "mentioned": {"id": "0", "name": "Canal"}}
            ]
        }
    body.append({"type": "TextBlock", "size": "Small", "weight": "Bolder", "color": "Good", "text": titulo, "wrap": True})
    body.append({"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": linha_principal, "wrap": True})
    if subinfo:
        body.append({"type": "TextBlock", "size": "Small", "isSubtle": True, "wrap": True, "text": subinfo})
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": content,
            }
        ],
    }


def _enviar_async(app, url: str, payload: dict) -> None:
    with app.app_context():
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except Exception as exc:  # pragma: no cover - best effort
            app.logger.warning("Falha ao enviar aviso ao Teams: %s", exc)


def enviar_card(
    titulo: str,
    linha_principal: str,
    subinfo: str | None = None,
    mencionar_canal: bool = False,
    *,
    env_var: str = "TEAMS_WEBHOOK_EXPEDICAO_URL",
    config_key: str = "webhook_expedicao",
) -> None:
    """Dispara um Adaptive Card no canal do Teams (assincrono, tolerante a falha)."""
    app = current_app._get_current_object()
    url = _webhook_url(env_var, config_key)
    if not url:
        app.logger.info("TEAMS: webhook nao configurado; aviso ignorado (%s).", linha_principal)
        return
    payload = _card_payload(titulo, linha_principal, subinfo, mencionar_canal)
    threading.Thread(target=_enviar_async, args=(app, url, payload), daemon=True).start()


def notificar_expedicao_conferida(
    nome: str,
    referencia: str,
    *,
    conferente: str | None = None,
    volumes: str | None = None,
    peso_bruto: str | None = None,
    env_var: str = "TEAMS_WEBHOOK_EXPEDICAO_URL",
    config_key: str = "webhook_expedicao",
) -> None:
    """Aviso de conferencia de expedicao finalizada.

    Formato principal (ex.): "PAULO CESAR & CIA LTDA - Orcamento 6953".
    """
    linha = f"{(nome or 'Nao informado').strip()} - {referencia}".strip()
    partes = []
    if conferente:
        partes.append(f"Conferente: {conferente}")
    if volumes:
        partes.append(f"Volumes: {volumes}")
    if peso_bruto:
        partes.append(f"Peso bruto: {peso_bruto}")
    subinfo = " · ".join(partes) or None
    enviar_card(
        "✅ Conferência de expedição finalizada",
        linha,
        subinfo,
        mencionar_canal=True,
        env_var=env_var,
        config_key=config_key,
    )
