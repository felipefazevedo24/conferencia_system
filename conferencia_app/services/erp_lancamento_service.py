"""Integracao automatica com ERP (Postgres tcompras) para lancamento de NF.

Periodicamente consulta a tabela `tcompras` no Postgres do ERP procurando
NFs que ja foram lancadas la (`n_nf` + `dt_nf`). Para cada match com itens
locais com status="Concluido" e sem `numero_lancamento`, atualiza para
status="Lancado" gravando o `codigo` retornado pelo ERP, exatamente como
no fluxo manual de lancamento.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any

import requests
from flask import current_app

from ..extensions import db
from ..models import ItemNota

logger = logging.getLogger(__name__)


# Cache em memoria do resultado da ultima consulta ao ERP por NF.
# Estrutura: { numero_nota: {"motivo": str, "verificada_em": datetime} }
# Usado pela tela /lancamento (Documento de Entrada) para sinalizar de forma
# sutil que a NF foi consultada no ERP e nao foi possivel lancar.
_STATUS_CONSULTA: dict[str, dict[str, Any]] = {}


def obter_status_consulta(numero_nota: str) -> dict[str, Any] | None:
    """Retorna a ultima informacao de consulta ERP para a NF, ou None."""
    if not numero_nota:
        return None
    return _STATUS_CONSULTA.get(str(numero_nota).strip())


def _registrar_status(numero_nota: str, motivo: str) -> None:
    if not numero_nota:
        return
    _STATUS_CONSULTA[str(numero_nota).strip()] = {
        "motivo": motivo,
        "verificada_em": datetime.now(),
    }


def _limpar_status(numero_nota: str) -> None:
    _STATUS_CONSULTA.pop(str(numero_nota).strip(), None)


def _carregar_credenciais_arquivo() -> dict[str, Any]:
    """Le instance/erp_lancamento_config.json se existir.

    Estrutura esperada:
        {
          "host": "10.250.100.251",
          "port": 5432,
          "database": "CPS",
          "user": "DevLeitura",
          "password": "...",
          "table": "tcompras"
        }
    """
    try:
        path = os.path.join(current_app.instance_path, "erp_lancamento_config.json")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        logger.exception("Falha ao ler erp_lancamento_config.json")
        return {}


def _resolver_config() -> dict[str, Any]:
    arquivo = _carregar_credenciais_arquivo()
    cfg = current_app.config
    return {
        "host": str(arquivo.get("host") or cfg.get("ERP_LANCAMENTO_PG_HOST") or "").strip(),
        "port": int(arquivo.get("port") or cfg.get("ERP_LANCAMENTO_PG_PORT") or 5432),
        "database": str(arquivo.get("database") or cfg.get("ERP_LANCAMENTO_PG_DB") or "").strip(),
        "user": str(arquivo.get("user") or cfg.get("ERP_LANCAMENTO_PG_USER") or "").strip(),
        "password": str(arquivo.get("password") or cfg.get("ERP_LANCAMENTO_PG_PASSWORD") or ""),
        "table": str(arquivo.get("table") or cfg.get("ERP_LANCAMENTO_PG_TABLE") or "tcompras").strip(),
        "api_url": str(arquivo.get("api_url") or cfg.get("ERP_LANCAMENTO_API_URL") or "").strip().rstrip("/"),
        "api_token": str(arquivo.get("api_token") or cfg.get("ERP_LANCAMENTO_API_TOKEN") or ""),
        "api_timeout": int(arquivo.get("api_timeout") or cfg.get("ERP_LANCAMENTO_API_TIMEOUT") or 30),
        "usuario_lancamento": str(
            arquivo.get("usuario_lancamento")
            or cfg.get("ERP_LANCAMENTO_USUARIO")
            or "ERP"
        ).strip(),
    }


def _conectar(cfg: dict[str, Any]):
    import psycopg2  # type: ignore

    return psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        dbname=cfg["database"],
        user=cfg["user"],
        password=cfg["password"],
        connect_timeout=15,
    )


def _consultar_codigos_no_erp(cfg: dict[str, Any], chaves: list[tuple[str, datetime | None]]):
    """Recebe pares (n_nf, data_emissao_or_None) e retorna {n_nf: (codigo, dt_nf)}.

    Quando `data_emissao` vier preenchida, restringe pela data (match exato).
    Quando vier None (NFs antigas sem dhEmi), consulta apenas por n_nf e
    aceita linhas duplicadas quando todas apontarem para a mesma chave NF-e.
    """
    if not chaves:
        return {}

    table = cfg["table"]
    if not table.replace("_", "").isalnum():
        raise ValueError(f"Nome de tabela invalido: {table}")

    resultados: dict[str, tuple[str, Any]] = {}

    def _normalizar_linhas(rows):
        linhas_validas = [
            (str(row[0]).strip(), row[1], str(row[2] or "").strip())
            for row in rows
            if row and row[0] is not None and str(row[0]).strip()
        ]
        if not linhas_validas:
            return None

        chaves = {chv_nfe for _codigo, _dt_nf, chv_nfe in linhas_validas if chv_nfe}
        if len(chaves) == 1:
            codigo, dt_nf, _chv_nfe = linhas_validas[0]
            return codigo, dt_nf

        assinaturas = {
            (codigo, dt_nf.isoformat() if hasattr(dt_nf, "isoformat") else str(dt_nf))
            for codigo, dt_nf, _chv_nfe in linhas_validas
        }
        if not chaves and len(assinaturas) == 1:
            codigo, dt_nf, _chv_nfe = linhas_validas[0]
            return codigo, dt_nf
        return None

    sql_com_data = f"SELECT codigo, dt_nf, chv_nfe FROM {table} WHERE n_nf = %s AND dt_nf::date = %s LIMIT 50"
    sql_sem_data = f"SELECT codigo, dt_nf, chv_nfe FROM {table} WHERE n_nf = %s LIMIT 50"

    with _conectar(cfg) as conn:
        with conn.cursor() as cur:
            for n_nf, data_emi in chaves:
                try:
                    if data_emi is not None:
                        cur.execute(sql_com_data, (str(n_nf), data_emi.date()))
                        row = _normalizar_linhas(cur.fetchall())
                        if row:
                            resultados[str(n_nf)] = row
                        else:
                            # A data local vem do XML; em algumas bases a dt_nf do ERP
                            # representa a entrada/lancamento. Se a data falhar, tenta
                            # por numero e aceita apenas quando o retorno for inequivoco.
                            cur.execute(sql_sem_data, (str(n_nf),))
                            rows_sem_data = cur.fetchall()
                            row_sem_data = _normalizar_linhas(rows_sem_data)
                            if row_sem_data:
                                resultados[str(n_nf)] = row_sem_data
                            elif rows_sem_data:
                                logger.warning(
                                    "ERP Lancamento: NF %s sem match por data %s, mas com %d matches por numero; pulando por ambiguidade.",
                                    n_nf, data_emi.date(), len(rows_sem_data),
                                )
                                _registrar_status(
                                    str(n_nf),
                                    "ERP encontrou o numero, mas com multiplas chaves/datas - confirme manualmente",
                                )
                            else:
                                _registrar_status(str(n_nf), "Aguardando lancamento no ERP")
                    else:
                        cur.execute(sql_sem_data, (str(n_nf),))
                        rows = cur.fetchall()
                        row = _normalizar_linhas(rows)
                        if row:
                            resultados[str(n_nf)] = row
                        elif rows:
                            logger.warning(
                                "ERP Lancamento: NF %s tem %d matches em %s sem data_emissao local; pulando para evitar ambiguidade.",
                                n_nf, len(rows), table,
                            )
                            _registrar_status(
                                str(n_nf),
                                "Múltiplas chaves de acesso para esse número no ERP — vincule manualmente",
                            )
                        else:
                            _registrar_status(str(n_nf), "Aguardando lançamento no ERP")
                except Exception as exc:
                    logger.warning("Erro consultando ERP n_nf=%s dt=%s: %s", n_nf, data_emi, exc)
                    continue
    return resultados


def _dt_to_api_value(valor: datetime | None) -> str | None:
    if valor is None:
        return None
    return valor.date().isoformat()


def _parse_dt_nf_api(valor: Any) -> Any:
    if not isinstance(valor, str) or not valor.strip():
        return valor
    texto = valor.strip()
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except Exception:
        return valor


def _consultar_codigos_via_api(cfg: dict[str, Any], chaves: list[tuple[str, datetime | None]]):
    """Consulta a API bridge hospedada na VM.

    Contrato esperado:
        POST {api_url}/api/erp/lancamentos
        {"chaves": [{"n_nf": "123", "data_emissao": "2026-05-11"}]}

    Resposta:
        {
          "resultados": {"123": {"codigo": "ABC", "dt_nf": "2026-05-11"}},
          "status": {"123": "Aguardando lancamento no ERP"}
        }
    """
    if not chaves:
        return {}
    if not cfg.get("api_url"):
        raise ValueError("ERP_LANCAMENTO_API_URL nao configurada.")

    url = f"{cfg['api_url']}/api/erp/lancamentos"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/ERP-Lancamento",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"

    payload = {
        "chaves": [
            {"n_nf": str(n_nf), "data_emissao": _dt_to_api_value(data_emi)}
            for n_nf, data_emi in chaves
            if n_nf
        ]
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=cfg.get("api_timeout") or 30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError("Resposta invalida da API ERP (esperava objeto JSON).")

    status_map = data.get("status") or {}
    if isinstance(status_map, dict):
        for n_nf, motivo in status_map.items():
            if motivo:
                _registrar_status(str(n_nf), str(motivo))

    resultados_raw = data.get("resultados") or {}
    resultados: dict[str, tuple[str, Any]] = {}
    if isinstance(resultados_raw, dict):
        for n_nf, row in resultados_raw.items():
            if not isinstance(row, dict):
                continue
            codigo = str(row.get("codigo") or "").strip()
            if codigo:
                resultados[str(n_nf)] = (codigo, _parse_dt_nf_api(row.get("dt_nf")))
    elif isinstance(resultados_raw, list):
        for row in resultados_raw:
            if not isinstance(row, dict):
                continue
            n_nf = str(row.get("n_nf") or "").strip()
            codigo = str(row.get("codigo") or "").strip()
            if n_nf and codigo:
                resultados[n_nf] = (codigo, _parse_dt_nf_api(row.get("dt_nf")))

    return resultados


def _consultar_entrada_grv_via_api(cfg: dict[str, Any], numero_nota: str, codigo_lancamento: str = "", chave: str = "") -> dict[str, Any] | None:
    if not cfg.get("api_url"):
        return None
    url = f"{cfg['api_url']}/api/erp/entrada-chapa"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
        "User-Agent": "ColumbiaSync/ERP-Lancamento",
    }
    if cfg.get("api_token"):
        headers["Authorization"] = f"Bearer {cfg['api_token']}"
    payload = {
        "numero_ar": str(codigo_lancamento or "").strip(),
        "codigo_lancamento": str(codigo_lancamento or "").strip(),
        "numero_nota": str(numero_nota or "").strip(),
        "chave": str(chave or "").strip(),
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=cfg.get("api_timeout") or 30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        return None
    entrada = data.get("entrada")
    return entrada if isinstance(entrada, dict) else None


def _consultar_entrada_grv_direto(cfg: dict[str, Any], numero_nota: str, codigo_lancamento: str = "", chave: str = "") -> dict[str, Any] | None:
    if not cfg.get("host") or not cfg.get("database") or not cfg.get("user"):
        return None
    where = []
    params: list[Any] = []
    if codigo_lancamento:
        where.append("c.codigo::text = %s")
        params.append(str(codigo_lancamento))
    elif numero_nota:
        where.append("c.n_nf::text = %s")
        params.append(str(numero_nota))
    elif chave:
        where.append("regexp_replace(coalesce(c.chv_nfe, ''), '\\D', '', 'g') = %s")
        params.append("".join(ch for ch in str(chave) if ch.isdigit()))
    else:
        return None

    with _conectar(cfg) as conn:
        with conn.cursor() as cur:
            cur.execute("select column_name from information_schema.columns where table_schema = 'public' and table_name = 'tcom_aux'")
            cols_aux = {str(row[0]).lower() for row in cur.fetchall()}

            def col(alias: str, *candidates: str, default: str = "null") -> str:
                for name in candidates:
                    if name.lower() in cols_aux:
                        return f"a.{name} as {alias}"
                return f"{default} as {alias}"

            tax_select = ",\n            ".join([
                col("icms_base_calculo", "icms_base_calculo", "base_icms", "vl_base_icms", "vbc_icms", "bc_icms", default="0"),
                col("icms_aliquota", "icms_aliquota", "aliquota_icms", "aliq_icms", "p_icms", default="0"),
                col("icms_cst", "icms_cst", "cst_icms", "cst", "sit_trib_icms", default="''"),
                col("icms_valor", "icms_valor", "valor_icms", "vl_icms", "v_icms", default="0"),
                col("pis_base_calculo", "pis_base_calculo", "base_pis", "vl_base_pis", "vbc_pis", default="0"),
                col("pis_aliquota", "pis_aliquota", "aliquota_pis", "aliq_pis", "p_pis", default="0"),
                col("pis_cst", "pis_cst", "cst_pis", "sit_trib_pis", default="''"),
                col("pis_valor_credito", "pis_valor_credito", "valor_pis", "vl_pis", "v_pis", default="0"),
                col("cofins_base_calculo", "cofins_base_calculo", "base_cofins", "vl_base_cofins", "vbc_cofins", default="0"),
                col("cofins_aliquota", "cofins_aliquota", "aliquota_cofins", "aliq_cofins", "p_cofins", default="0"),
                col("cofins_cst", "cofins_cst", "cst_cofins", "sit_trib_cofins", default="''"),
                col("cofins_valor_credito", "cofins_valor_credito", "valor_cofins", "vl_cofins", "v_cofins", default="0"),
            ])
            sql = f"""
                select
                    c.codigo::text as codigo_lancamento,
                    c.n_nf::text as numero_nota,
                    coalesce(nullif(c.chv_nfe, ''), '') as chave_acesso,
                    a.numero_item,
                    coalesce(nullif(a.cfop, ''), nullif(c.cfop, '')) as cfop,
                    coalesce(nullif(a.descricao_cfop, ''), nullif(c.tipo_movimento, ''), nullif(c.codigo_movimentacao, '')) as natureza_operacao,
                    coalesce(nullif(a.cod_interno, ''), nullif(p.codigo_interno, '')) as cod_interno,
                    coalesce(nullif(a.produto, ''), nullif(p.nome, '')) as descricao,
                    coalesce(a.qtde, 0) as quantidade,
                    {tax_select}
                from public.tcompras c
                left join public.tcom_aux a
                  on a.cod_empresa = c.cod_empresa
                 and a.realciona_auto = c.codigo
                left join public.tproduto p
                  on p.cod_empresa = a.cod_empresa
                 and p.codigo = a.cod_produto
                where {" or ".join(where)}
                order by c.dt_lancamento desc nulls last, c.dt_nf desc nulls last, c.codigo desc, a.numero_item, a.guid_linha
                limit 200
            """
            cur.execute(sql, params)
            cols = [desc[0] for desc in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    if not rows:
        return None
    cab = rows[0]
    itens = [
        {
            "numero_item": row.get("numero_item"),
            "cfop": row.get("cfop") or "",
            "natureza_operacao": row.get("natureza_operacao") or "",
            "cod_interno": row.get("cod_interno") or "",
            "descricao": row.get("descricao") or "",
            "quantidade": row.get("quantidade") or 0,
            "icms_base_calculo": row.get("icms_base_calculo") or 0,
            "icms_aliquota": row.get("icms_aliquota") or 0,
            "icms_cst": row.get("icms_cst") or "",
            "icms_valor": row.get("icms_valor") or 0,
            "pis_base_calculo": row.get("pis_base_calculo") or 0,
            "pis_aliquota": row.get("pis_aliquota") or 0,
            "pis_cst": row.get("pis_cst") or "",
            "pis_valor_credito": row.get("pis_valor_credito") or 0,
            "cofins_base_calculo": row.get("cofins_base_calculo") or 0,
            "cofins_aliquota": row.get("cofins_aliquota") or 0,
            "cofins_cst": row.get("cofins_cst") or "",
            "cofins_valor_credito": row.get("cofins_valor_credito") or 0,
        }
        for row in rows
        if row.get("cod_interno") or row.get("descricao")
    ]
    return {
        "codigo_lancamento": cab.get("codigo_lancamento") or codigo_lancamento,
        "numero_nota": cab.get("numero_nota") or numero_nota,
        "chave_acesso": cab.get("chave_acesso") or chave,
        "itens": itens,
    }


def _normalizar_match_texto(valor: Any) -> str:
    import unicodedata

    text = str(valor or "").strip().upper()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(re for re in "".join(ch if ch.isalnum() else " " for ch in text).split() if re)


def _float_grv(valor: Any, default=0.0) -> float:
    try:
        if isinstance(valor, str):
            texto = valor.replace("R$", "").strip()
            if "," in texto:
                texto = texto.replace(".", "").replace(",", ".")
            return float(texto or default)
        return float(valor if valor is not None else default)
    except Exception:
        return float(default)


def _set_if_present(item: ItemNota, attr: str, row: dict[str, Any], *keys: str) -> None:
    for key in keys:
        if key in row:
            if attr == "cst_icms":
                setattr(item, attr, str(row.get(key) or "").strip()[:3])
            elif attr in {"cst_pis", "cst_cofins"}:
                setattr(item, attr, str(row.get(key) or "").strip()[:2])
            else:
                setattr(item, attr, _float_grv(row.get(key)))
            return


def _aplicar_codigos_grv(numero_nota: str, entrada: dict[str, Any]) -> int:
    itens_grv = [row for row in (entrada.get("itens") or []) if isinstance(row, dict) and str(row.get("cod_interno") or "").strip()]
    if not itens_grv:
        return 0

    itens_locais = ItemNota.query.filter_by(numero_nota=str(numero_nota)).order_by(ItemNota.id.asc()).all()
    atualizados = 0
    usados: set[int] = set()
    for item in itens_locais:
        melhor_idx = None
        codigo_atual = str(item.codigo_grv or "").strip().lower()
        if codigo_atual:
            for idx, row in enumerate(itens_grv):
                if idx not in usados and str(row.get("cod_interno") or "").strip().lower() == codigo_atual:
                    melhor_idx = idx
                    break
        item_desc = _normalizar_match_texto(item.descricao)
        item_cfop = str(item.cfop or "").strip()[:4]
        item_qtd = float(item.qtd_real or 0)
        if melhor_idx is None:
            for idx, row in enumerate(itens_grv):
                if idx in usados:
                    continue
                row_cfop = str(row.get("cfop") or "").strip()[:4]
                row_desc = _normalizar_match_texto(row.get("descricao"))
                try:
                    row_qtd = float(row.get("quantidade") or 0)
                except Exception:
                    row_qtd = 0.0
                if item_cfop and row_cfop and item_cfop != row_cfop:
                    continue
                if item_desc and row_desc and (item_desc in row_desc or row_desc in item_desc):
                    melhor_idx = idx
                    break
                if item_qtd and row_qtd and abs(item_qtd - row_qtd) < 0.0001:
                    melhor_idx = idx
                    break
        if melhor_idx is None and len(itens_locais) == len(itens_grv):
            for idx in range(len(itens_grv)):
                if idx not in usados:
                    melhor_idx = idx
                    break
        if melhor_idx is None:
            continue
        usados.add(melhor_idx)
        codigo = str(itens_grv[melhor_idx].get("cod_interno") or "").strip()
        if codigo:
            row_grv = itens_grv[melhor_idx]
            item.codigo_grv = codigo[:80]
            item.cfop_descricao_grv = str(row_grv.get("natureza_operacao") or row_grv.get("descricao_cfop") or "").strip()[:180]
            _set_if_present(item, "icms_base_calculo", row_grv, "icms_base_calculo", "base_icms", "vl_base_icms", "vbc_icms")
            _set_if_present(item, "icms_aliquota", row_grv, "icms_aliquota", "aliquota_icms", "aliq_icms", "p_icms")
            _set_if_present(item, "cst_icms", row_grv, "icms_cst", "cst_icms", "cst")
            _set_if_present(item, "icms_valor", row_grv, "icms_valor", "valor_icms", "vl_icms", "v_icms")
            _set_if_present(item, "pis_base_calculo", row_grv, "pis_base_calculo", "base_pis", "vl_base_pis", "vbc_pis")
            _set_if_present(item, "pis_aliquota", row_grv, "pis_aliquota", "aliquota_pis", "aliq_pis", "p_pis")
            _set_if_present(item, "cst_pis", row_grv, "pis_cst", "cst_pis")
            _set_if_present(item, "pis_valor_credito", row_grv, "pis_valor_credito", "valor_pis", "vl_pis", "v_pis")
            _set_if_present(item, "cofins_base_calculo", row_grv, "cofins_base_calculo", "base_cofins", "vl_base_cofins", "vbc_cofins")
            _set_if_present(item, "cofins_aliquota", row_grv, "cofins_aliquota", "aliquota_cofins", "aliq_cofins", "p_cofins")
            _set_if_present(item, "cst_cofins", row_grv, "cofins_cst", "cst_cofins")
            _set_if_present(item, "cofins_valor_credito", row_grv, "cofins_valor_credito", "valor_cofins", "vl_cofins", "v_cofins")
            item.tributos_origem = "GRV"
            item.tributos_grv_atualizado_em = datetime.now()
            atualizados += 1
    return atualizados


def sincronizar_codigos_grv_nota(numero_nota: str, codigo_lancamento: str = "", chave: str = "") -> int:
    cfg = _resolver_config()
    if not cfg.get("api_url") and (not cfg.get("host") or not cfg.get("database") or not cfg.get("user")):
        return 0
    try:
        entrada = (
            _consultar_entrada_grv_via_api(cfg, numero_nota, codigo_lancamento, chave)
            if cfg.get("api_url")
            else _consultar_entrada_grv_direto(cfg, numero_nota, codigo_lancamento, chave)
        )
        if not entrada:
            return 0
        total = _aplicar_codigos_grv(str(numero_nota), entrada)
        if total:
            db.session.commit()
        return total
    except Exception:
        db.session.rollback()
        logger.exception("Falha ao sincronizar codigo GRV da NF %s", numero_nota)
        return 0


def _consultar_codigos(cfg: dict[str, Any], chaves: list[tuple[str, datetime | None]]):
    if cfg.get("api_url"):
        return _consultar_codigos_via_api(cfg, chaves)
    return _consultar_codigos_no_erp(cfg, chaves)


def _aplicar_lancamento_local(
    numero_nota: str,
    codigo: str,
    usuario: str,
    dt_nf_erp: Any = None,
) -> int:
    """Replica exatamente o fluxo manual: status->Lancado, numero_lancamento=codigo.

    Faz tambem backfill de `data_emissao` quando estiver vazia e o ERP tiver
    retornado `dt_nf` (NFs importadas antes da feature).
    """
    update_values: dict[str, Any] = {
        "status": "Lançado",
        "usuario_lancamento": usuario,
        "data_lancamento": datetime.now(),
        "numero_lancamento": codigo,
    }
    rows = ItemNota.query.filter_by(numero_nota=numero_nota, status="Concluído").update(update_values)

    if dt_nf_erp is not None:
        try:
            dt_emissao = dt_nf_erp if isinstance(dt_nf_erp, datetime) else datetime.combine(dt_nf_erp, datetime.min.time())
            ItemNota.query.filter(
                ItemNota.numero_nota == numero_nota,
                ItemNota.data_emissao.is_(None),
            ).update({"data_emissao": dt_emissao})
        except Exception:
            logger.exception("Falha ao backfill data_emissao para NF %s", numero_nota)

    return int(rows or 0)


def _enfileirar_wms_se_disponivel(numero_nota: str, usuario: str) -> None:
    """Reaproveita a fila de integracao WMS usada no lancamento manual, se existir."""
    try:
        from ..routes.api_routes import _enfileirar_integracao_wms_nota_lancada  # type: ignore
    except Exception:
        return
    try:
        _enfileirar_integracao_wms_nota_lancada(numero_nota, usuario)
    except Exception:
        logger.exception("Falha ao enfileirar integracao WMS para NF %s", numero_nota)


def _manifestar_se_disponivel(numero_nota: str, usuario: str) -> dict[str, Any]:
    """Chama a manifestacao SEFAZ (via Consyste) igual o fluxo manual."""
    try:
        from ..routes.api_routes import _manifestar_confirmacao_operacao  # type: ignore
    except Exception as exc:
        logger.warning("Manifestacao indisponivel: %s", exc)
        return {"sucesso": False, "msg": "manifestacao_indisponivel"}
    try:
        return _manifestar_confirmacao_operacao(numero_nota, usuario) or {"sucesso": False}
    except Exception as exc:
        logger.exception("Falha na manifestacao SEFAZ para NF %s", numero_nota)
        return {"sucesso": False, "msg": str(exc)[:200]}


def _reverter_lancamento_local(numero_nota: str) -> None:
    """Reverte um lancamento aplicado localmente quando a manifestacao falha."""
    try:
        ItemNota.query.filter_by(numero_nota=numero_nota, status="Lançado").update(
            {
                "status": "Concluído",
                "usuario_lancamento": None,
                "data_lancamento": None,
                "numero_lancamento": None,
            }
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Falha ao reverter lancamento local da NF %s", numero_nota)


def executar_ciclo() -> dict[str, Any]:
    """Executa um ciclo de consulta no ERP e aplica os lancamentos encontrados."""
    resumo: dict[str, Any] = {
        "configurado": False,
        "candidatas": 0,
        "consultadas": 0,
        "encontradas": 0,
        "lancadas": 0,
        "erros": 0,
        "mensagem": "",
    }

    cfg = _resolver_config()
    if not cfg.get("api_url") and (not cfg["host"] or not cfg["database"] or not cfg["user"]):
        resumo["mensagem"] = "ERP_LANCAMENTO nao configurado (api_url ou host/database/user)."
        return resumo
    resumo["configurado"] = True

    # Coleta NFs candidatas: status Concluido, sem numero_lancamento.
    # Inclui NFs sem data_emissao (importadas antes desta feature); para essas,
    # a consulta no ERP sera feita apenas por n_nf (com match unico exigido).
    candidatas = (
        db.session.query(ItemNota.numero_nota, ItemNota.data_emissao)
        .filter(
            ItemNota.status == "Concluído",
            (ItemNota.numero_lancamento.is_(None)) | (ItemNota.numero_lancamento == ""),
        )
        .distinct()
        .all()
    )

    resumo["candidatas"] = len(candidatas)
    if not candidatas:
        resumo["mensagem"] = "Nenhuma NF aguardando lancamento."
        return resumo

    # Deduplica por numero_nota (uma NF pode aparecer varias vezes, com
    # data_emissao preenchida em algumas linhas e None em outras).
    pares_map: dict[str, datetime | None] = {}
    for n, d in candidatas:
        if not n:
            continue
        chave = str(n).strip()
        if chave not in pares_map or (pares_map[chave] is None and d is not None):
            pares_map[chave] = d
    pares: list[tuple[str, datetime | None]] = list(pares_map.items())
    resumo["consultadas"] = len(pares)

    try:
        achados = _consultar_codigos(cfg, pares)
    except Exception as exc:
        logger.exception("Erro ao consultar ERP Lancamento: %s", exc)
        resumo["erros"] += 1
        resumo["mensagem"] = f"Erro de conexao com ERP: {exc}"
        return resumo

    resumo["encontradas"] = len(achados)
    if not achados:
        resumo["mensagem"] = "Nenhuma NF localizada na tcompras neste ciclo."
        resumo["status_consulta"] = {
            str(n_nf): (_STATUS_CONSULTA.get(str(n_nf), {}) or {}).get("motivo", "")
            for n_nf, _data_emi in pares[:20]
            if _STATUS_CONSULTA.get(str(n_nf), {}).get("motivo")
        }
        return resumo

    usuario = cfg["usuario_lancamento"]
    total_lancadas = 0
    for n_nf, (codigo, dt_nf_erp) in achados.items():
        try:
            atualizadas = _aplicar_lancamento_local(n_nf, codigo, usuario, dt_nf_erp)
            if atualizadas > 0:
                # Commit do lancamento antes de chamar a SEFAZ (a manifestacao
                # consulta o numero_nota com status=Lancado).
                try:
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    logger.exception("Falha no commit do lancamento NF %s", n_nf)
                    resumo["erros"] += 1
                    continue

                # Manifestacao SEFAZ (Confirmacao da Operacao) - mesmo fluxo manual.
                manifest = _manifestar_se_disponivel(n_nf, usuario)
                if not manifest.get("sucesso"):
                    msg = manifest.get("msg") or "falha desconhecida"
                    logger.warning(
                        "ERP Lancamento: manifestacao falhou para NF %s (%s); revertendo lancamento.",
                        n_nf, msg,
                    )
                    _reverter_lancamento_local(n_nf)
                    _registrar_status(
                        str(n_nf),
                        f"Lançado no ERP, mas manifestação SEFAZ falhou: {msg}",
                    )
                    resumo["erros"] += 1
                    continue

                total_lancadas += 1
                _limpar_status(n_nf)
                sincronizar_codigos_grv_nota(str(n_nf), codigo_lancamento=str(codigo))
                try:
                    from .classificacao_contabil_service import classificar_nota
                    classificar_nota(str(n_nf))
                except Exception:
                    logger.exception("Falha ao classificar contabilmente a NF %s", n_nf)
                _enfileirar_wms_se_disponivel(n_nf, usuario)
                try:
                    from .entrada_chapa_email_service import notificar_entrada_chapa_lancada
                    notificar_entrada_chapa_lancada(
                        str(n_nf),
                        numero_ar=str(codigo),
                        usuario=usuario,
                        origem="Auto",
                        assincrono=False,
                    )
                except Exception:
                    logger.exception("Falha ao acionar aviso de entrada de chapa NF %s", n_nf)
                logger.info(
                    "ERP Lancamento: NF %s lancada automaticamente (codigo=%s, %d itens, manifestada).",
                    n_nf,
                    codigo,
                    atualizadas,
                )
        except Exception:
            logger.exception("Falha ao aplicar lancamento da NF %s", n_nf)
            db.session.rollback()
            resumo["erros"] += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Falha no commit final do ciclo ERP Lancamento")
        resumo["erros"] += 1

    resumo["lancadas"] = total_lancadas
    resumo["mensagem"] = f"{total_lancadas} NF(s) lancada(s) via ERP."
    return resumo
