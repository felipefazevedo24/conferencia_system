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


def listar_documentos_consyste(modelo: str = "nfe", q: str | None = None, campos: str | None = None, timeout: int = 20):
    token = current_app.config.get("CONSYSTE_TOKEN")
    modelo_limpo = str(modelo or "nfe").strip().lower()
    if modelo_limpo not in {"nfe", "nfse"}:
        modelo_limpo = "nfe"

    url = f"{current_app.config['CONSYSTE_API_BASE']}/{modelo_limpo}/lista"
    headers = {
        "X-Consyste-Auth-Token": token,
        "Accept": "application/json",
    }
    params = {}
    if q:
        params["q"] = str(q)
    if campos:
        params["campos"] = str(campos)

    response = requests.get(url, headers=headers, params=params, timeout=timeout)
    try:
        payload = response.json() if response.content else {}
    except Exception:
        payload = {"raw": response.text}
    return response.ok, response.status_code, _normalize_consyste_payload(payload)


def download_documento_consyste(
    modelo: str,
    formato: str = "xml",
    chave: str | None = None,
    documento_id: str | None = None,
    timeout: int = 30,
):
    token = current_app.config.get("CONSYSTE_TOKEN")
    modelo_limpo = str(modelo or "nfe").strip().lower()
    formato_limpo = str(formato or "xml").strip().lower()
    if formato_limpo not in {"xml", "pdf"}:
        formato_limpo = "xml"

    headers = {
        "X-Consyste-Auth-Token": token,
        "Accept": "application/xml" if formato_limpo == "xml" else "application/pdf",
    }

    if modelo_limpo == "nfse":
        doc_id = str(documento_id or "").strip()
        if not doc_id:
            return False, 400, b""
        url = f"{current_app.config['CONSYSTE_API_BASE']}/nfse/{doc_id}/download.{formato_limpo}"
    else:
        chave_limpa = re.sub(r"\D", "", str(chave or ""))
        if not chave_limpa:
            return False, 400, b""
        url = f"{current_app.config['CONSYSTE_API_BASE']}/nfe/{chave_limpa}/download.{formato_limpo}"

    response = requests.get(url, headers=headers, timeout=timeout)
    return response.ok, response.status_code, response.content
