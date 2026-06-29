"""Consulta NF-e emitida no ERP via API bridge da VM."""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests
from flask import current_app


NFE_EMAIL_DATA_MINIMA = date(2026, 5, 13)


def _somente_digitos(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _parse_date(valor: str | None) -> date | None:
    raw = str(valor or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw[:10]).date()
    except ValueError:
        return None


def normalizar_data_minima(valor: str | None = None) -> str:
    data = _parse_date(valor) or NFE_EMAIL_DATA_MINIMA
    if data < NFE_EMAIL_DATA_MINIMA:
        data = NFE_EMAIL_DATA_MINIMA
    return data.isoformat()


def _arquivo_config(app) -> dict[str, Any]:
    path = Path(app.instance_path) / "erp_lancamento_config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        app.logger.exception("Falha ao ler erp_lancamento_config.json")
        return {}


def _bridge_config() -> dict[str, Any]:
    app = current_app._get_current_object()
    arquivo = _arquivo_config(app)
    api_url = (
        os.environ.get("NFE_EMAIL_ERP_API_URL")
        or os.environ.get("ERP_LANCAMENTO_API_URL")
        or arquivo.get("api_url")
        or app.config.get("ERP_LANCAMENTO_API_URL")
        or ""
    )
    api_token = (
        os.environ.get("NFE_EMAIL_ERP_API_TOKEN")
        or os.environ.get("ERP_LANCAMENTO_API_TOKEN")
        or arquivo.get("api_token")
        or app.config.get("ERP_LANCAMENTO_API_TOKEN")
        or ""
    )
    timeout = (
        os.environ.get("NFE_EMAIL_ERP_API_TIMEOUT")
        or os.environ.get("ERP_LANCAMENTO_API_TIMEOUT")
        or arquivo.get("api_timeout")
        or app.config.get("ERP_LANCAMENTO_API_TIMEOUT")
        or 10
    )
    return {
        "api_url": str(api_url or "").strip().rstrip("/"),
        "api_token": str(api_token or ""),
        "timeout": int(timeout or 30),
    }


def _headers(cfg: dict[str, Any]) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/NFE-Email-ERP",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    return headers


def _post_bridge(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    cfg = _bridge_config()
    if not cfg["api_url"]:
        raise ValueError("ERP_LANCAMENTO_API_URL/api_url nao configurada para NF-e emitida.")
    resp = requests.post(
        f"{cfg['api_url']}{path}",
        headers=_headers(cfg),
        json=payload,
        timeout=cfg["timeout"],
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict) or not data.get("sucesso"):
        raise RuntimeError(str((data or {}).get("erro") or "Resposta invalida da API ERP NF-e."))
    return data


def listar_nfes_emitidas_erp(data_inicial: str | None = None) -> list[dict[str, Any]]:
    data_consulta = normalizar_data_minima(data_inicial)
    data = _post_bridge("/api/erp/nfe-emitidas", {"data_inicial": data_consulta})
    notas = data.get("notas") or []
    if not isinstance(notas, list):
        return []
    return [n for n in notas if isinstance(n, dict)]


def buscar_nfe_emitida_erp(numero_nf: str = "", chave: str = "") -> dict[str, Any] | None:
    payload = {
        "numero": str(numero_nf or "").strip(),
        "chave": _somente_digitos(chave),
        "data_minima": NFE_EMAIL_DATA_MINIMA.isoformat(),
    }
    if not payload["numero"] and not payload["chave"]:
        return None
    data = _post_bridge("/api/erp/nfe-emitida", payload)
    nota = data.get("nota")
    if not isinstance(nota, dict):
        return None

    for campo in ("xml_base64", "pdf_base64"):
        raw = str(nota.get(campo) or "")
        if raw:
            try:
                nota[campo.replace("_base64", "_bytes")] = base64.b64decode(raw)
            except Exception:
                nota[campo.replace("_base64", "_bytes")] = None
        else:
            nota[campo.replace("_base64", "_bytes")] = None
    return nota


def buscar_email_cadastro_erp(cnpj: str) -> dict[str, Any]:
    """Consulta e-mail de cliente/fornecedor diretamente no GRV/Postgres via bridge."""
    doc = _somente_digitos(cnpj)
    if not doc:
        return {}
    try:
        data = _post_bridge("/api/erp/cadastro-email-por-cnpj", {"cnpj": doc})
        if not data.get("encontrado"):
            return {}

        return {
            "email": str(data.get("email") or "").strip(),
            "nome": str(data.get("nome") or "").strip(),
            "documento": _somente_digitos(data.get("documento") or doc),
            "origem": str(data.get("origem") or "").strip(),
            "codigo": str(data.get("codigo") or "").strip(),
            "fonte": "GRVPostgres",
        }
    except Exception as exc:
        current_app.logger.warning("Falha ao consultar e-mail no cadastro GRV para %s: %s", doc, exc)
        return {}
