import re

import requests
from flask import current_app


def _normalize_consyste_payload(payload):
    if isinstance(payload, dict):
        return payload
    if payload is None:
        return {}
    return {"raw": str(payload)[:1000]}


def enviar_decisao_consyste(chave: str, decisao: str, observacao: str, timeout: int = 15):
    token = current_app.config.get("CONSYSTE_TOKEN")
    chave_limpa = re.sub(r"\D", "", str(chave))
    url = f"{current_app.config['CONSYSTE_API_BASE']}/nfe/{chave_limpa}/decisao-portaria/{decisao}"
    headers = {"X-Consyste-Auth-Token": token, "Content-Type": "application/json"}
    payload = {"observacao": observacao}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        try:
            body = response.json() if response.content else {}
        except Exception:
            body = {"raw": response.text}
        return response.ok, response.status_code, _normalize_consyste_payload(body)
    except Exception as exc:
        return False, 500, {"error": str(exc)[:500]}


def manifestar_destinatario_consyste(
    document_id: str,
    manifestacao: str = "confirmada",
    justificativa: str | None = None,
    cnpj: str | None = None,
    timeout: int = 20,
):
    token = current_app.config.get("CONSYSTE_TOKEN")
    document_id_limpo = re.sub(r"\D", "", str(document_id or ""))
    url = f"{current_app.config['CONSYSTE_API_BASE']}/nfe/{document_id_limpo}/manifestar/{manifestacao}"
    headers = {"X-Consyste-Auth-Token": token, "Content-Type": "application/json"}
    params = {}
    if justificativa:
        params["justificativa"] = justificativa
    if cnpj:
        params["cnpj"] = re.sub(r"\D", "", str(cnpj))

    response = requests.post(url, headers=headers, params=params, timeout=timeout)
    payload = None
    try:
        payload = response.json() if response.content else {}
    except Exception:
        payload = {"raw": response.text}
    return response.ok, response.status_code, _normalize_consyste_payload(payload)


def solicitar_emissao_nfe_consyste(
    cnpj: str,
    txt_payload: str,
    ambiente: int = 2,
    timeout: int = 30,
):
    token = current_app.config.get("CONSYSTE_TOKEN")
    cnpj_limpo = re.sub(r"\D", "", str(cnpj or ""))
    amb = 1 if int(ambiente) == 1 else 2

    url = f"{current_app.config['CONSYSTE_API_BASE']}/emissao/{amb}/nfe"
    headers = {
        "X-Consyste-Auth-Token": token,
        "Content-Type": "text/plain",
        "Accept": "application/json",
    }
    params = {"cnpj": cnpj_limpo}

    response = requests.post(url, headers=headers, params=params, data=str(txt_payload or ""), timeout=timeout)
    try:
        payload = response.json() if response.content else {}
    except Exception:
        payload = {"raw": response.text}
    return response.ok, response.status_code, _normalize_consyste_payload(payload)


def consultar_emissao_nfe_consyste(
    emissao_id: str,
    ambiente: int = 2,
    timeout: int = 20,
):
    token = current_app.config.get("CONSYSTE_TOKEN")
    amb = 1 if int(ambiente) == 1 else 2
    emissao_id_limpo = str(emissao_id or "").strip()

    url = f"{current_app.config['CONSYSTE_API_BASE']}/emissao/{amb}/nfe/{emissao_id_limpo}"
    headers = {
        "X-Consyste-Auth-Token": token,
        "Accept": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=timeout)
    try:
        payload = response.json() if response.content else {}
    except Exception:
        payload = {"raw": response.text}
    return response.ok, response.status_code, _normalize_consyste_payload(payload)
