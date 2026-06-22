"""Persistencia simples das configuracoes de NF-e email em JSON dentro de instance/.

Permite que as alteracoes feitas na tela (modo_teste, cc, data_corte, etc.) sobrevivam
restarts do app sem depender de mexer em variaveis de ambiente.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any

from flask import current_app

_LOCK = threading.Lock()

_CHAVES = (
    "NFE_EMAIL_MODO_TESTE",
    "NFE_EMAIL_TESTE_DESTINO",
    "NFE_EMAIL_AUTO_NO_FATURAMENTO",
    "NFE_EMAIL_AUTO_ENABLED",
    "NFE_EMAIL_AUTO_DESDE",
    "NFE_EMAIL_CC",
    "NFE_EMAIL_POLL_INTERVAL_SECONDS",
    "NFE_EMAIL_CFOPS_ESPECIAIS",
    "NFE_EMAIL_DESTINATARIOS_ESPECIAIS",
    "MAIL_SMTP_TIMEOUT",
    "ENTRADA_CHAPA_EMAIL_ENABLED",
    "ENTRADA_CHAPA_EMAIL_DESTINATARIOS",
    "ENTRADA_CHAPA_EMAIL_CC",
    "ENTRADA_CHAPA_CFOPS",
    "ENTRADA_CHAPA_CONTROLE_LOTE_VALORES",
)


def _caminho_arquivo() -> str:
    return os.path.join(current_app.instance_path, "nfe_email_config.json")


def carregar_persistido(app=None) -> dict[str, Any]:
    """Le o JSON de instance/ e aplica sobre app.config. Tolerante a ausencia."""
    app = app or current_app._get_current_object()
    path = os.path.join(app.instance_path, "nfe_email_config.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            dados = json.load(f)
    except Exception:
        return {}
    with _LOCK:
        for k, v in (dados or {}).items():
            if k in _CHAVES:
                app.config[k] = v
    return dados or {}


def salvar_persistido(parcial: dict[str, Any]) -> dict[str, Any]:
    """Aplica `parcial` em app.config e grava JSON. Retorna o dict final."""
    app = current_app._get_current_object()
    os.makedirs(app.instance_path, exist_ok=True)
    with _LOCK:
        for k, v in (parcial or {}).items():
            if k in _CHAVES:
                app.config[k] = v
        snapshot = {k: app.config.get(k) for k in _CHAVES}
        with open(_caminho_arquivo(), "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return snapshot
