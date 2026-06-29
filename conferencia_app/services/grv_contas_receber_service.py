"""Consulta de titulos do contas a receber no GRV/Postgres."""

from __future__ import annotations

import base64
import logging
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlencode

import requests
from flask import current_app

from .erp_lancamento_service import _conectar, _resolver_config

logger = logging.getLogger(__name__)


def _only_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _date_str(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:10], fmt).strftime("%d/%m/%Y")
        except ValueError:
            continue
    return text[:10]


def _iso_date(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)[:10]


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _status_titulo(vencimento: Any, pago: Any, data_pagamento: Any, valor: float, valor_pago: float) -> str:
    if bool(pago) or data_pagamento or valor_pago >= max(valor, 0.0):
        return "Pago"
    if isinstance(vencimento, datetime):
        venc = vencimento.date()
    elif isinstance(vencimento, date):
        venc = vencimento
    else:
        venc = None
    if venc and venc < date.today():
        return "Vencido"
    return "Em aberto"


def _row_to_titulo(row: dict[str, Any]) -> dict[str, Any]:
    valor = _float(row.get("valor"))
    valor_pago = _float(row.get("valor_pago"))
    saldo = max(valor - valor_pago, 0.0)
    numero_nota = str(row.get("numero_nota") or "").strip()
    documento = str(row.get("documento") or "").strip()
    parcela = row.get("parcela")
    total_parcelas = row.get("n_parcelas")
    vencimento_raw = row.get("vencimento_raw") or row.get("dt_vencimento")
    codigo = str(row.get("codigo") or "").strip()
    tem_pdf_flag = bool(row.get("boleto_pdf_disponivel"))
    boleto_gerado = bool(row.get("boleto_gerado") or row.get("nossonumero"))
    query = urlencode(
        {
            "codigo": codigo,
            "numero_nota": numero_nota,
            "documento": documento,
            "nosso_numero": str(row.get("nossonumero") or "").strip(),
            "cpf_cnpj": _only_digits(row.get("cpf_cnpj_pagador")),
            "valor": str(_float(row.get("valor"))),
        }
    )
    # Em respostas da bridge nem sempre vem boleto_pdf_disponivel; se o titulo ja
    # estiver com boleto emitido, expomos o link para tentativa de download oficial.
    url_boleto = f"/api/boletos/grv/pdf?{query}" if codigo and (tem_pdf_flag or boleto_gerado) else ""

    data_pagamento = row.get("data_pagamento") or row.get("dt_pagamento")
    status = _status_titulo(vencimento_raw, row.get("pago"), data_pagamento, valor, valor_pago)

    return {
        "fonte": "grv_postgres",
        "tipo": "titulo",
        "id_grv": codigo,
        "cod_cliente": str(row.get("cod_cliente") or ""),
        "nome_pagador": str(row.get("nome_pagador") or "").strip(),
        "cpf_cnpj_pagador": _only_digits(row.get("cpf_cnpj_pagador")),
        "numero_nota": numero_nota,
        "documento": documento,
        "orcamento": str(row.get("orcamento") or "").strip(),
        "nosso_numero": str(row.get("nossonumero") or "").strip(),
        "valor": valor if status == "Pago" else (saldo or valor),
        "valor_original": valor,
        "valor_pago": valor_pago,
        "vencimento": _date_str(vencimento_raw),
        "vencimento_iso": _iso_date(vencimento_raw),
        "data_pagamento": _date_str(data_pagamento),
        "status": status,
        "parcela": str(parcela or "").strip(),
        "n_parcelas": str(total_parcelas or "").strip(),
        "linha_digitavel": "",
        "codigo_barras": "",
        "url_boleto": url_boleto,
        "banco": current_app.config.get("BOLETO_BANK_LABEL", "Banco do Brasil"),
        "boleto_gerado": boleto_gerado,
        "pode_gerar_boleto": True,
        "chave_acesso": str(row.get("chave_acesso") or "").strip(),
    }


class GRVContasReceberService:
    """Le o contas a receber do GRV."""

    @staticmethod
    def is_configured() -> bool:
        cfg = _resolver_config()
        return bool((cfg.get("api_url") and cfg.get("api_token")) or (cfg.get("host") and cfg.get("database") and cfg.get("user")))

    @classmethod
    def consultar_abertos(
        cls,
        *,
        cpf_cnpj: str = "",
        numero_nota: str = "",
        orcamento: str = "",
        incluir_pagos: bool = False,
        limite: int = 200,
    ) -> list[dict[str, Any]]:
        cfg = _resolver_config()
        if not any((cpf_cnpj, numero_nota, orcamento)):
            return []
        if cfg.get("api_url"):
            try:
                return cls._consultar_abertos_via_api(
                    cfg,
                    cpf_cnpj=cpf_cnpj,
                    numero_nota=numero_nota,
                    orcamento=orcamento,
                    incluir_pagos=incluir_pagos,
                    limite=limite,
                )
            except Exception:
                logger.exception("Falha ao consultar contas a receber GRV via bridge")
        if cfg.get("host") and cfg.get("database") and cfg.get("user"):
            try:
                return cls._consultar_abertos_postgres(
                    cfg,
                    cpf_cnpj=cpf_cnpj,
                    numero_nota=numero_nota,
                    orcamento=orcamento,
                    incluir_pagos=incluir_pagos,
                    limite=limite,
                )
            except Exception:
                logger.exception("Falha ao consultar contas a receber GRV direto no Postgres")
        return []

    @classmethod
    def _consultar_abertos_via_api(
        cls,
        cfg: dict[str, Any],
        *,
        cpf_cnpj: str = "",
        numero_nota: str = "",
        orcamento: str = "",
        incluir_pagos: bool = False,
        limite: int = 200,
    ) -> list[dict[str, Any]]:
        url = f"{cfg['api_url'].rstrip('/')}/api/erp/contas-receber-aberto"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",
            "User-Agent": "ColumbiaSync/Portal-Cliente",
        }
        if cfg.get("api_token"):
            headers["Authorization"] = f"Bearer {cfg['api_token']}"
        payload = {
            "cpf_cnpj": _only_digits(cpf_cnpj),
            "numero_nota": str(numero_nota or "").strip(),
            "orcamento": str(orcamento or "").strip(),
            "incluir_pagos": bool(incluir_pagos),
            "limite": limite,
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=cfg.get("api_timeout") or 30)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("titulos") if isinstance(data, dict) else []
        return [_row_to_titulo(row) for row in rows if isinstance(row, dict)]

    @classmethod
    def _obter_boleto_pdf_via_api(cls, cfg: dict[str, Any], codigo: str) -> bytes | None:
        url = f"{cfg['api_url'].rstrip('/')}/api/erp/contas-receber-boleto-pdf"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "ngrok-skip-browser-warning": "true",
            "User-Agent": "ColumbiaSync/Portal-Cliente",
        }
        if cfg.get("api_token"):
            headers["Authorization"] = f"Bearer {cfg['api_token']}"

        resp = requests.post(
            url,
            headers=headers,
            json={"codigo": str(codigo or "").strip()},
            timeout=cfg.get("api_timeout") or 30,
        )
        if resp.status_code in (400, 404):
            return None
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            return None
        b64 = str(data.get("pdf_base64") or "").strip()
        if not b64:
            return None
        try:
            payload = base64.b64decode(b64)
        except Exception:
            return None
        return payload if payload.startswith(b"%PDF") else None

    @classmethod
    def _consultar_abertos_postgres(
        cls,
        cfg: dict[str, Any],
        *,
        cpf_cnpj: str = "",
        numero_nota: str = "",
        orcamento: str = "",
        incluir_pagos: bool = False,
        limite: int = 200,
    ) -> list[dict[str, Any]]:
        doc = _only_digits(cpf_cnpj)
        nf = str(numero_nota or "").strip()
        orc = str(orcamento or "").strip()
        limite = max(1, min(int(limite or 200), 500))

        filtros = []
        params: list[Any] = []
        if doc:
            filtros.append(
                "(regexp_replace(coalesce(c.rg_cgc, nf.cgc_cpf, ''), '\\D', '', 'g') = %s)"
            )
            params.append(doc)
        if nf:
            filtros.append("(r.n_nf::text = %s or coalesce(r.documento, '') ilike %s)")
            params.extend([nf, f"%{nf}%"])
        if orc:
            filtros.append(
                "(r.n_orcamento::text = %s or r.cod_orcamento::text = %s)"
            )
            params.extend([orc, orc])
        if not filtros:
            return []

        sql = f"""
            select
                r.codigo::text as codigo,
                r.cod_cliente,
                coalesce(nullif(r.cliente, ''), nullif(c.razao_social, ''), nullif(c.nome, ''), nullif(nf.razao_social, ''), nullif(nf.cliente, '')) as nome_pagador,
                coalesce(nullif(c.rg_cgc, ''), nullif(nf.cgc_cpf, '')) as cpf_cnpj_pagador,
                r.n_nf::text as numero_nota,
                coalesce(nullif(r.documento, ''), r.n_nf::text, r.codigo::text) as documento,
                coalesce(r.n_orcamento::text, r.cod_orcamento::text, '') as orcamento,
                r.dt_vencimento as vencimento_raw,
                coalesce(r.valor, 0) as valor,
                coalesce(r.valor_pago, 0) as valor_pago,
                coalesce(r.pago, 0) as pago,
                r.dt_pagamento as data_pagamento,
                r.parcela,
                r.n_parcelas,
                coalesce(r.nossonumero, '') as nossonumero,
                coalesce(r.boleto_gerado, 0) as boleto_gerado,
                coalesce(r.boleto_situacao, '') as boleto_situacao,
                case when r.boleto_pdf is null then 0 else 1 end as boleto_pdf_disponivel,
                coalesce(nf.chv_nfe, '') as chave_acesso
            from public.treceber r
            left join public.tcliente c
              on c.cod_empresa = r.cod_empresa
             and c.codigo = r.cod_cliente
            left join public.tnota_fiscal nf
              on nf.cod_empresa = r.cod_empresa
             and nf.numero = r.n_nf
             and nf.cod_cliente = r.cod_cliente
                        where ({'true' if incluir_pagos else '(coalesce(r.pago, 0) = 0 and r.dt_pagamento is null and coalesce(r.valor, 0) > coalesce(r.valor_pago, 0))'})
              and ({' or '.join(filtros)})
            order by r.dt_vencimento asc nulls last, r.codigo asc
            limit %s
        """
        params.append(limite)
        with _conectar(cfg) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                cols = [desc[0] for desc in cur.description]
                return [_row_to_titulo(dict(zip(cols, row))) for row in cur.fetchall()]

    @classmethod
    def obter_boleto_pdf_por_codigo(cls, codigo: str) -> bytes | None:
        cfg = _resolver_config()
        codigo_limpo = str(codigo or "").strip()
        if not codigo_limpo:
            return None
        if cfg.get("host") and cfg.get("database") and cfg.get("user"):
            sql = """
                select boleto_pdf
                from public.treceber
                where codigo::text = %s
                limit 1
            """
            with _conectar(cfg) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, [codigo_limpo])
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        payload = bytes(row[0])
                        if payload.startswith(b"%PDF"):
                            return payload

        if cfg.get("api_url"):
            try:
                return cls._obter_boleto_pdf_via_api(cfg, codigo_limpo)
            except Exception:
                logger.exception("Falha ao obter boleto PDF via bridge")
        return None
