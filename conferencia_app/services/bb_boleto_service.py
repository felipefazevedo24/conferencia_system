"""Integracao com a API de cobranca do Banco do Brasil."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import requests
from flask import current_app

from ..extensions import db
from ..models import BoletoContaReceber
from .grv_contas_receber_service import GRVContasReceberService

logger = logging.getLogger(__name__)


def _only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _to_float(value) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0

    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")

    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _format_api_date(value) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None

    iso_value = raw.replace("Z", "+00:00")
    if "T" in iso_value:
        try:
            return datetime.fromisoformat(iso_value).strftime("%d/%m/%Y")
        except ValueError:
            pass

    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return raw


def _api_date(value: datetime) -> str:
    return value.strftime("%d.%m.%Y")


class BBBoletoService:
    """Consulta e enriquecimento de boletos com dados do BB."""

    _token_cache: dict = {}
    _status_map = {
        1: "Registrado",
        6: "Pago",
        7: "Baixado",
        12: "Pago",
        14: "Em liquidacao",
        18: "Pago parcial",
        19: "Pago parcial",
        80: "Processando",
    }

    @classmethod
    def banco_label(cls) -> str:
        label = str(current_app.config.get("BOLETO_BANK_LABEL", "")).strip()
        return label or "Banco do Brasil"

    @classmethod
    def _timeout(cls) -> int:
        try:
            return int(current_app.config.get("BB_API_TIMEOUT_SECONDS", 30))
        except (TypeError, ValueError):
            return 30

    @classmethod
    def _cert(cls):
        cert_path = str(current_app.config.get("BB_CERT_PATH", "")).strip()
        key_path = str(current_app.config.get("BB_KEY_PATH", "")).strip()
        return (cert_path, key_path) if cert_path and key_path else None

    @classmethod
    def _token_url(cls) -> str:
        explicit = str(current_app.config.get("BB_OAUTH_TOKEN_URL", "")).strip()
        if explicit:
            return explicit

        base = str(current_app.config.get("BB_OAUTH_BASE", "https://oauth.hm.bb.com.br")).strip().rstrip("/")
        path = str(current_app.config.get("BB_OAUTH_TOKEN_PATH", "/oauth/token")).strip()
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base}{path}"

    @classmethod
    def _get_access_token(cls) -> str | None:
        cached = cls._token_cache.get("token")
        if cached and cls._token_cache.get("expires_at", 0) > datetime.now().timestamp():
            return cached

        client_id = str(current_app.config.get("BB_CLIENT_ID", "")).strip()
        client_secret = str(current_app.config.get("BB_CLIENT_SECRET", "")).strip()
        scope = str(current_app.config.get("BB_SCOPE", "")).strip()

        if not client_id or not client_secret:
            logger.warning("BB: credenciais não configuradas (BB_CLIENT_ID / BB_CLIENT_SECRET).")
            return None

        data = {"grant_type": "client_credentials"}
        if scope:
            data["scope"] = scope

        try:
            resp = requests.post(
                cls._token_url(),
                data=data,
                auth=(client_id, client_secret),
                cert=cls._cert(),
                timeout=cls._timeout(),
            )
            resp.raise_for_status()
            payload = resp.json()
            token = str(payload.get("access_token") or "").strip()
            if not token:
                logger.warning("BB: token OAuth retornou sem access_token.")
                return None

            cls._token_cache["token"] = token
            cls._token_cache["expires_at"] = datetime.now().timestamp() + int(payload.get("expires_in", 3500))
            return token
        except Exception as exc:
            logger.error("BB: erro ao obter token OAuth - %s", exc)
            return None

    @classmethod
    def _headers(cls) -> dict | None:
        token = cls._get_access_token()
        if not token:
            return None
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    @classmethod
    def _base_query_params(cls) -> dict | None:
        convenio = str(current_app.config.get("BB_CONVENIO", "")).strip()
        app_key = str(current_app.config.get("BB_DEVELOPER_APPLICATION_KEY", "")).strip()
        if not convenio or not app_key:
            return None
        try:
            dias = int(current_app.config.get("BB_CONSULTA_DIAS_RETROATIVOS", 730) or 730)
        except (TypeError, ValueError):
            dias = 730
        hoje = datetime.now()
        return {
            "gw-dev-app-key": app_key,
            "numeroConvenio": convenio,
            "indicadorSituacao": "A",
            "dataInicioVencimento": _api_date(hoje - timedelta(days=max(dias, 1))),
            "dataFimVencimento": _api_date(hoje + timedelta(days=730)),
        }

    @classmethod
    def _extrair_lista_boletos_api(cls, payload) -> list[dict]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)]
        if not isinstance(payload, dict):
            return []
        for key in (
            "boletos",
            "listaBoletos",
            "listaBoleto",
            "titulos",
            "listaTitulos",
            "itens",
            "items",
            "data",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [payload] if payload else []

    @classmethod
    def _normalize_bank_name(cls, value: str) -> str:
        raw = str(value or "").strip()
        if not raw or raw.upper().startswith("BOFA"):
            return cls.banco_label()
        return raw

    @classmethod
    def _normalize_status(cls, raw_status) -> str:
        if raw_status is None or raw_status == "":
            return "Gerado"

        try:
            status_code = int(raw_status)
        except (TypeError, ValueError):
            status_code = None

        if status_code is not None:
            return cls._status_map.get(status_code, str(status_code))

        text = str(raw_status).strip()
        lowered = text.lower()
        if any(word in lowered for word in ("liquidado", "pago")):
            return "Pago"
        if "baix" in lowered:
            return "Baixado"
        if "process" in lowered:
            return "Processando"
        if "registr" in lowered:
            return "Registrado"
        return text or "Gerado"

    @classmethod
    def _normalizar_boleto_api(cls, raw: dict) -> dict:
        pagador = raw.get("pagador") or {}
        if not isinstance(pagador, dict):
            pagador = {}

        status = (
            raw.get("descricaoEstadoTituloCobranca")
            or raw.get("estadoTituloCobranca")
            or raw.get("status")
        )

        return {
            "nosso_numero": str(
                raw.get("numeroTituloCliente")
                or raw.get("nossoNumero")
                or raw.get("numero")
                or ""
            ),
            "numero_nota": str(
                raw.get("numeroTituloBeneficiario")
                or raw.get("numeroDocumento")
                or raw.get("seuNumero")
                or ""
            ),
            "valor": _to_float(raw.get("valorOriginal") or raw.get("valor") or raw.get("valorCobrado")),
            "vencimento": _format_api_date(
                raw.get("dataVencimento")
                or raw.get("dataVencimentoTituloCobranca")
                or raw.get("vencimento")
            ),
            "data_pagamento": _format_api_date(
                raw.get("dataLiquidacao")
                or raw.get("dataCreditoLiquidacao")
                or raw.get("dataPagamento")
            ),
            "status": cls._normalize_status(status),
            "linha_digitavel": str(
                raw.get("linhaDigitavel")
                or raw.get("codigoLinhaDigitavel")
                or raw.get("linha_digitavel")
                or ""
            ),
            "codigo_barras": str(
                raw.get("codigoBarraNumerico")
                or raw.get("codigoBarras")
                or raw.get("codigo_barras")
                or ""
            ),
            "nome_pagador": str(
                pagador.get("nome")
                or raw.get("nomePagador")
                or raw.get("nome_pagador")
                or ""
            ),
            "cpf_cnpj_pagador": str(
                pagador.get("numeroInscricao")
                or raw.get("numeroInscricaoPagador")
                or raw.get("cpfCnpjPagador")
                or raw.get("cpf_cnpj_pagador")
                or ""
            ),
            "banco": cls.banco_label(),
        }

    @classmethod
    def _is_aberto(cls, boleto: dict) -> bool:
        status = str(boleto.get("status") or "").strip().lower()
        if any(word in status for word in ("pago", "baixado", "liquid")):
            return False
        return not bool(boleto.get("data_pagamento"))

    @classmethod
    def _filtrar_abertos(cls, boletos: list[dict]) -> list[dict]:
        return [boleto for boleto in boletos if cls._is_aberto(boleto)]

    @classmethod
    def _deduplicar(cls, boletos: list[dict]) -> list[dict]:
        vistos = set()
        resultado = []
        for item in boletos:
            chave = (
                item.get("fonte") or "",
                item.get("id_grv") or "",
                item.get("nosso_numero") or "",
                item.get("numero_nota") or "",
                item.get("vencimento") or "",
                round(float(item.get("valor") or 0), 2),
            )
            if chave in vistos:
                continue
            vistos.add(chave)
            resultado.append(item)
        return resultado

    @classmethod
    def consultar_boleto_por_nosso_numero(cls, nosso_numero: str) -> dict | None:
        headers = cls._headers()
        if headers is None:
            return None

        convenio = str(current_app.config.get("BB_CONVENIO", "")).strip()
        app_key = str(current_app.config.get("BB_DEVELOPER_APPLICATION_KEY", "")).strip()
        base_url = str(current_app.config.get("BB_API_BASE", "")).strip().rstrip("/")
        nosso_numero_digits = _only_digits(nosso_numero)

        if not convenio or not app_key or not base_url or not nosso_numero_digits:
            return None

        try:
            resp = requests.get(
                f"{base_url}/boletos/{nosso_numero_digits}",
                params={
                    "gw-dev-app-key": app_key,
                    "numeroConvenio": convenio,
                },
                headers=headers,
                cert=cls._cert(),
                timeout=cls._timeout(),
            )
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, list):
                payload = payload[0] if payload else {}
            if not isinstance(payload, dict) or not payload:
                return None
            return cls._normalizar_boleto_api(payload)
        except Exception as exc:
            logger.warning("BB: falha ao consultar boleto %s - %s", nosso_numero_digits, exc)
            return None

    @classmethod
    def consultar_boletos_api(cls, *, cpf_cnpj: str = "", numero_nota: str = "") -> list[dict]:
        headers = cls._headers()
        params_base = cls._base_query_params()
        base_url = str(current_app.config.get("BB_API_BASE", "")).strip().rstrip("/")
        if headers is None or params_base is None or not base_url:
            return []

        doc = _only_digits(cpf_cnpj)
        nota = str(numero_nota or "").strip()
        if not doc and not nota:
            return []

        tentativas: list[dict] = []
        if doc:
            tentativas.extend([
                {"numeroInscricaoPagador": doc},
                {"cpfCnpjPagador": doc},
                {"cnpjCpfPagador": doc},
            ])
        if nota:
            tentativas.extend([
                {"numeroTituloBeneficiario": nota},
                {"seuNumero": nota},
                {"numeroDocumento": nota},
            ])

        vistos = set()
        resultados: list[dict] = []
        for extra in tentativas:
            params = dict(params_base)
            params.update(extra)
            try:
                resp = requests.get(
                    f"{base_url}/boletos",
                    params=params,
                    headers=headers,
                    cert=cls._cert(),
                    timeout=cls._timeout(),
                )
                if resp.status_code in (400, 404):
                    continue
                resp.raise_for_status()
                for raw in cls._extrair_lista_boletos_api(resp.json()):
                    boleto = cls._normalizar_boleto_api(raw)
                    if doc and _only_digits(boleto.get("cpf_cnpj_pagador")) != doc:
                        continue
                    if nota and str(boleto.get("numero_nota") or "").strip() != nota:
                        continue
                    chave = boleto.get("nosso_numero") or boleto.get("linha_digitavel") or str(raw)
                    if chave in vistos:
                        continue
                    vistos.add(chave)
                    resultados.append(boleto)
                if resultados:
                    break
            except Exception as exc:
                logger.warning("BB: falha ao listar boletos (%s) - %s", extra, exc)
        return resultados

    @classmethod
    def _merge_boleto(cls, local_boleto: dict, api_boleto: dict | None) -> dict:
        merged = dict(local_boleto)
        if not api_boleto:
            merged["banco"] = cls._normalize_bank_name(merged.get("banco"))
            return merged

        for field in (
            "nosso_numero",
            "numero_nota",
            "linha_digitavel",
            "codigo_barras",
            "nome_pagador",
            "cpf_cnpj_pagador",
            "status",
            "vencimento",
            "data_pagamento",
        ):
            if api_boleto.get(field):
                merged[field] = api_boleto[field]

        api_value = api_boleto.get("valor")
        if api_value not in (None, "", 0, 0.0):
            merged["valor"] = api_value

        merged["banco"] = cls.banco_label()
        return merged

    @classmethod
    def _enriquecer_boletos_com_api(cls, boletos: list[dict]) -> tuple[list[dict], bool]:
        if not boletos or not cls.is_configured():
            return [cls._merge_boleto(item, None) for item in boletos], False

        enriched = []
        refreshed_any = False
        for boleto in boletos:
            api_boleto = None
            nosso_numero = _only_digits(boleto.get("nosso_numero"))
            if nosso_numero:
                api_boleto = cls.consultar_boleto_por_nosso_numero(nosso_numero)
            refreshed_any = refreshed_any or bool(api_boleto)
            enriched.append(cls._merge_boleto(boleto, api_boleto))
        return enriched, refreshed_any

    @classmethod
    def consultar_boletos_local(cls, cpf_cnpj: str, somente_abertos: bool = False) -> list[dict]:
        doc = _only_digits(cpf_cnpj)
        if not doc:
            return []

        boletos = BoletoContaReceber.query.filter(
            BoletoContaReceber.cpf_cnpj_pagador.isnot(None),
            db.func.replace(
                db.func.replace(
                    db.func.replace(BoletoContaReceber.cpf_cnpj_pagador, ".", ""),
                    "-",
                    "",
                ),
                "/",
                "",
            )
            == doc,
        ).order_by(BoletoContaReceber.data_geracao.desc()).all()

        resultado = [
            {
                "nosso_numero": b.nosso_numero,
                "numero_nota": b.numero_nota,
                "valor": float(b.valor or 0),
                "vencimento": b.vencimento.strftime("%d/%m/%Y") if b.vencimento else None,
                "data_pagamento": b.data_pagamento.strftime("%d/%m/%Y") if b.data_pagamento else None,
                "status": b.status,
                "linha_digitavel": b.linha_digitavel,
                "codigo_barras": b.codigo_barras,
                "nome_pagador": b.nome_pagador or "",
                "cpf_cnpj_pagador": _only_digits(b.cpf_cnpj_pagador or ""),
                "banco": cls._normalize_bank_name(b.banco),
                "data_geracao": b.data_geracao.strftime("%d/%m/%Y %H:%M") if b.data_geracao else "",
            }
            for b in boletos
        ]
        return cls._filtrar_abertos(resultado) if somente_abertos else resultado

    @classmethod
    def consultar_por_nota_valor(cls, numero_nota: str, valor: float, somente_abertos: bool = False) -> list[dict]:
        nota = str(numero_nota or "").strip()
        if not nota:
            return []

        titulos_grv = GRVContasReceberService.consultar_abertos(numero_nota=nota)
        query = BoletoContaReceber.query.filter(BoletoContaReceber.numero_nota == nota)
        if valor is not None and valor > 0:
            margem = valor * 0.01
            query = query.filter(BoletoContaReceber.valor.between(valor - margem, valor + margem))

        boletos = query.order_by(BoletoContaReceber.data_geracao.desc()).all()
        resultado = [
            {
                "nosso_numero": b.nosso_numero,
                "numero_nota": b.numero_nota,
                "valor": float(b.valor or 0),
                "vencimento": b.vencimento.strftime("%d/%m/%Y") if b.vencimento else None,
                "data_pagamento": b.data_pagamento.strftime("%d/%m/%Y") if b.data_pagamento else None,
                "status": b.status,
                "linha_digitavel": b.linha_digitavel,
                "codigo_barras": b.codigo_barras,
                "nome_pagador": b.nome_pagador or "",
                "cpf_cnpj_pagador": _only_digits(b.cpf_cnpj_pagador or ""),
                "banco": cls._normalize_bank_name(b.banco),
                "data_geracao": b.data_geracao.strftime("%d/%m/%Y %H:%M") if b.data_geracao else "",
            }
            for b in boletos
        ]
        if somente_abertos:
            resultado = cls._filtrar_abertos(resultado)
        enriched, _ = cls._enriquecer_boletos_com_api(resultado)
        api_result = cls.consultar_boletos_api(numero_nota=nota) if cls.is_configured() else []
        if somente_abertos:
            api_result = cls._filtrar_abertos(api_result)
        if valor is not None and valor > 0:
            margem = valor * 0.01
            api_result = [b for b in api_result if margem == 0 or (valor - margem) <= float(b.get("valor") or 0) <= (valor + margem)]

        return cls._deduplicar(titulos_grv + enriched + api_result)

    @classmethod
    def consultar_por_orcamento(cls, orcamento: str) -> list[dict]:
        numero = str(orcamento or "").strip()
        if not numero:
            return []
        return cls._deduplicar(GRVContasReceberService.consultar_abertos(orcamento=numero))

    @classmethod
    def consultar_boletos(cls, cpf_cnpj: str, somente_abertos: bool = False) -> dict:
        doc = _only_digits(cpf_cnpj)
        if not doc or len(doc) < 11:
            return {"fonte": "erro", "boletos": [], "mensagem": "CPF/CNPJ inválido."}

        titulos_grv = GRVContasReceberService.consultar_abertos(cpf_cnpj=doc)
        local_result = cls.consultar_boletos_local(doc, somente_abertos=somente_abertos)
        enriched, refreshed = cls._enriquecer_boletos_com_api(local_result)
        api_result = cls.consultar_boletos_api(cpf_cnpj=doc) if cls.is_configured() else []
        if somente_abertos:
            api_result = cls._filtrar_abertos(api_result)
        if api_result:
            return {
                "fonte": "grv_postgres+bb_api+local" if titulos_grv else ("bb_api+local" if enriched else "bb_api"),
                "boletos": cls._deduplicar(titulos_grv + enriched + api_result),
            }
        return {
            "fonte": "grv_postgres+local" if titulos_grv else ("bb_api+local" if refreshed else "local"),
            "boletos": cls._deduplicar(titulos_grv + enriched),
        }

    @staticmethod
    def is_configured() -> bool:
        client_id = str(current_app.config.get("BB_CLIENT_ID", "")).strip()
        client_secret = str(current_app.config.get("BB_CLIENT_SECRET", "")).strip()
        app_key = str(current_app.config.get("BB_DEVELOPER_APPLICATION_KEY", "")).strip()
        convenio = str(current_app.config.get("BB_CONVENIO", "")).strip()
        base_url = str(current_app.config.get("BB_API_BASE", "")).strip()
        return bool(client_id and client_secret and app_key and convenio and base_url)
