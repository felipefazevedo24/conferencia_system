"""TEMPORARIO - Spend pela query do administrador do banco (12/11/2026).

Segundo Spend, so' para comparar em producao com o Spend atual
(compras_spend_service) antes de decidir se a logica de consulta muda.
As duas queries abaixo estao EXATAMENTE como o administrador enviou - nao
corrigir aqui, a ideia e' ver o numero dele. Apagar junto com
routes/compras_spend_admin_routes.py, templates/compras_spend_admin.html,
o registro em __init__.py e o link em base.html.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, timedelta

from ..compras import db as grv
from ..compras.config import get_settings

SQL_SPEND_BASELINE = """
WITH base AS (
    SELECT
        tc.cod_empresa,
        COALESCE(tc.dt_recebimento::date, tc.dt_nf::date, tc.dt_emissao::date) AS data_entrada,
        CASE
            WHEN COALESCE(tc.chv_nfe, '') ~ '^[0-9]{44}$'
                THEN SUBSTRING(tc.chv_nfe FROM 7 FOR 14)
            ELSE 'SEM_CNPJ'
        END AS cnpj,
        NULL::text AS cnpj_destinatario,
        NULLIF(TRIM(tc.fornecedor), '') AS fornecedor,
        CASE
            WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN 'SERVICO'
            ELSE 'PRODUTO'
        END AS tipo_item,
        NULL::text AS classificacao_item,
        NULLIF(TRIM(tc.cfop), '') AS cfop_entrada,
        NULLIF(TRIM(tc.cfop), '') AS cfop_nf,
        NULL::text AS natureza_operacao,
        tc.modelo_nf AS modelo,
        tc.serie_nf AS serie,
        tc.sub_serie,
        NULLIF(regexp_replace(COALESCE(tc.n_nf, ''), '[^0-9]', '', 'g'), '')::int AS numero_nf,
        CASE
            WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN COALESCE(tc.vl_servicos, 0)
            ELSE COALESCE(NULLIF(tc.vl_total_nf, 0), NULLIF(tc.total, 0), 0)
        END::numeric(18,2) AS valor_item
    FROM public.tcompras tc
    WHERE tc.cod_empresa = %(cod_empresa)s
        AND (CASE
            WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN COALESCE(tc.vl_servicos, 0)
            ELSE COALESCE(NULLIF(tc.vl_total_nf, 0), NULLIF(tc.total, 0), 0)
            END) <> 0
      AND COALESCE(tc.dt_recebimento::date, tc.dt_nf::date, tc.dt_emissao::date)
          BETWEEN COALESCE(%(data_de)s::date, '1900-01-01'::date)
              AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
      AND (%(tipo_item)s::text IS NULL OR
           (CASE WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN 'SERVICO' ELSE 'PRODUTO' END) = %(tipo_item)s::text)
), agg AS (
    SELECT
        cod_empresa,
        cnpj,
        COALESCE(fornecedor, '(Sem nome)') AS fornecedor,
        tipo_item,
        COALESCE(classificacao_item, '(Sem classificação)') AS classificacao_item,
        COALESCE(cfop_entrada, '(Sem CFOP entrada)') AS cfop_entrada,
        COALESCE(cfop_nf, '(Sem CFOP NF)') AS cfop_nf,
        COALESCE(natureza_operacao, '(Sem natureza)') AS natureza_operacao,
        EXTRACT(YEAR FROM data_entrada)::int AS ano,
        EXTRACT(MONTH FROM data_entrada)::int AS mes,
        TO_CHAR(data_entrada, 'YYYY-MM') AS periodo_mes,
        SUM(valor_item)::numeric(18,2) AS valor_mensal,
        COUNT(*)::int AS qtd_itens,
        COUNT(DISTINCT (modelo::text || '-' || serie::text || '-' || sub_serie::text || '-' || numero_nf::text))::int AS qtd_nf
    FROM base
    GROUP BY cod_empresa, cnpj, COALESCE(fornecedor, '(Sem nome)'), tipo_item,
             COALESCE(classificacao_item, '(Sem classificação)'),
             COALESCE(cfop_entrada, '(Sem CFOP entrada)'),
             COALESCE(cfop_nf, '(Sem CFOP NF)'),
             COALESCE(natureza_operacao, '(Sem natureza)'),
             EXTRACT(YEAR FROM data_entrada), EXTRACT(MONTH FROM data_entrada), TO_CHAR(data_entrada, 'YYYY-MM')
)
SELECT
    cod_empresa,
    cnpj,
    fornecedor,
    tipo_item,
    classificacao_item,
    cfop_entrada AS cfop,
    cfop_entrada,
    cfop_nf,
    natureza_operacao,
    ano,
    mes,
    periodo_mes,
    valor_mensal,
    SUM(valor_mensal) OVER (PARTITION BY cnpj, tipo_item, ano)::numeric(18,2) AS valor_anual,
    qtd_itens,
    qtd_nf
FROM agg
ORDER BY ano DESC, mes DESC, cnpj, tipo_item, classificacao_item
"""


SQL_SPEND_BASELINE_COMPOSICAO = """
SELECT
    tc.cod_empresa,
    COALESCE(tc.dt_recebimento::date, tc.dt_nf::date, tc.dt_emissao::date) AS data_entrada,
    NULLIF(TRIM(tc.n_nf), '') AS numero_nf,
    NULLIF(TRIM(tc.modelo_nf), '') AS modelo_nf,
    NULLIF(TRIM(tc.serie_nf), '') AS serie_nf,
    NULLIF(TRIM(tc.sub_serie), '') AS sub_serie,
    CASE
        WHEN COALESCE(tc.chv_nfe, '') ~ '^[0-9]{44}$'
            THEN SUBSTRING(tc.chv_nfe FROM 7 FOR 14)
        ELSE 'SEM_CNPJ'
    END AS cnpj,
    COALESCE(NULLIF(TRIM(tc.fornecedor), ''), '(Sem fornecedor)') AS fornecedor,
    COALESCE(NULLIF(TRIM(tc.cfop), ''), '(Sem CFOP)') AS cfop,
    CASE
        WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN COALESCE(tc.vl_servicos, 0)
        ELSE COALESCE(NULLIF(tc.vl_total_nf, 0), NULLIF(tc.total, 0), 0)
    END::numeric(18,2) AS valor_nf,
    NULLIF(TRIM(tc.chv_nfe), '') AS chave_nfe,
    tc.cod_fornecedor,
    tc.cod_tp_mov,
    NULLIF(TRIM(tc.tipo_movimento), '') AS tipo_movimento,
    CASE
        WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN 'SERVICO'
        ELSE 'PRODUTO'
    END AS tipo_item
FROM public.tcompras tc
WHERE tc.cod_empresa = %(cod_empresa)s
    AND (CASE
                WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN COALESCE(tc.vl_servicos, 0)
                ELSE COALESCE(NULLIF(tc.vl_total_nf, 0), NULLIF(tc.total, 0), 0)
            END) <> 0
  AND COALESCE(tc.dt_recebimento::date, tc.dt_nf::date, tc.dt_emissao::date)
      BETWEEN COALESCE(%(data_de)s::date, '1900-01-01'::date)
          AND COALESCE(%(data_ate)s::date, '9999-12-31'::date)
  AND (%(tipo_item)s::text IS NULL OR
       (CASE WHEN COALESCE(tc.vl_servicos, 0) <> 0 THEN 'SERVICO' ELSE 'PRODUTO' END) = %(tipo_item)s::text)
  AND (%(cnpj)s::text IS NULL OR
       (CASE
            WHEN COALESCE(tc.chv_nfe, '') ~ '^[0-9]{44}$'
                THEN SUBSTRING(tc.chv_nfe FROM 7 FOR 14)
            ELSE 'SEM_CNPJ'
        END) = %(cnpj)s::text)
    AND (%(fornecedor)s::text IS NULL OR UPPER(COALESCE(tc.fornecedor, '')) LIKE '%%' || UPPER(%(fornecedor)s::text) || '%%')
ORDER BY data_entrada DESC NULLS LAST, numero_nf DESC NULLS LAST
LIMIT %(limite)s;
"""


# Consulta ao vivo no ERP, mes a mes (a bridge devolve tudo numa resposta so').
# Cache curto: reabrir a tela nao refaz as 12 chamadas.
_CACHE: dict = {}
_CACHE_SEG = 600


def _meses(de: str, ate: str):
    ano, mes = map(int, de.split("-"))
    fim_ano, fim_mes = map(int, ate.split("-"))
    while (ano, mes) <= (fim_ano, fim_mes):
        ini = date(ano, mes, 1)
        prox = date(ano + (mes == 12), mes % 12 + 1, 1)
        yield ini, prox - timedelta(days=1)
        ano, mes = prox.year, prox.month


def linhas_baseline(de: str, ate: str) -> list[dict]:
    chave = (de, ate)
    if chave in _CACHE and time.time() - _CACHE[chave][0] < _CACHE_SEG:
        return _CACHE[chave][1]
    empresa = get_settings().PG_COD_EMPRESA
    linhas = []
    for ini, fim in _meses(de, ate):
        linhas.extend(grv.fetch_all(SQL_SPEND_BASELINE, {
            "cod_empresa": empresa, "data_de": ini.isoformat(), "data_ate": fim.isoformat(), "tipo_item": None,
        }))
    _CACHE[chave] = (time.time(), linhas)
    return linhas


def painel(de: str, ate: str) -> dict:
    linhas = linhas_baseline(de, ate)
    por_mes, por_tipo = defaultdict(float), defaultdict(float)
    grupos: dict = {}
    total = sem_cnpj = 0.0
    nfs = 0
    for r in linhas:
        v = float(r.get("valor_mensal") or 0)
        total += v
        nfs += int(r.get("qtd_nf") or 0)
        por_mes[r.get("periodo_mes")] += v
        por_tipo[r.get("tipo_item")] += v
        if r.get("cnpj") == "SEM_CNPJ":
            sem_cnpj += v
        k = (r.get("cnpj"), r.get("fornecedor"), r.get("tipo_item"))
        g = grupos.setdefault(k, {"cnpj": k[0], "fornecedor": k[1], "tipo_item": k[2], "valor": 0.0, "nfs": 0, "cfops": set()})
        g["valor"] += v
        g["nfs"] += int(r.get("qtd_nf") or 0)
        g["cfops"].add(r.get("cfop"))
    fornecedores = sorted(grupos.values(), key=lambda g: -g["valor"])
    for g in fornecedores:
        g["valor"] = round(g["valor"], 2)
        g["cfops"] = ", ".join(sorted(c for c in g["cfops"] if c))
    return {
        "total": round(total, 2), "nfs": nfs, "sem_cnpj": round(sem_cnpj, 2), "linhas": len(linhas),
        "por_mes": {m: round(v, 2) for m, v in sorted(por_mes.items())},
        "por_tipo": {t: round(v, 2) for t, v in sorted(por_tipo.items())},
        "fornecedores": fornecedores,
    }


def composicao(de: str, ate: str, cnpj=None, fornecedor=None, tipo_item=None, limite: int = 500) -> list[dict]:
    fim = list(_meses(ate, ate))[0][1]
    return grv.fetch_all(SQL_SPEND_BASELINE_COMPOSICAO, {
        "cod_empresa": get_settings().PG_COD_EMPRESA, "data_de": f"{de}-01", "data_ate": fim.isoformat(),
        "tipo_item": tipo_item or None, "cnpj": cnpj or None, "fornecedor": fornecedor or None, "limite": limite,
    })
